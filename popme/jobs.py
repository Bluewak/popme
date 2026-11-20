"""수집 → 브리핑 파이프라인과 아침 스케줄."""
import json
import logging
import socket
import threading
import time
from datetime import datetime

from popme.fileio import atomic_write_text
from popme import accounts, briefing, catalog, chrome, config, discovery, event_weather, fieldscale, funnel, llm, sources
from popme.collectors import NeedLogin, caldav, ics, threads, timetree, x, youtube
from popme.db import DB

log = logging.getLogger(__name__)

# 수집 중 전용 Chrome 창이 닫혔을 때 남기는 경고 (앞부분으로 알아본다 — app.py가 「다시 모으기」 말풍선을 띄운다)
CHROME_CLOSED = "Chrome 창이 닫혀서"
# 수집하는 동안 전용 Chrome의 모든 페이지 아래에 띄우는 띠 — 무슨 창인지, 닫으면 안 된다는 걸 알 수 있게.
# CDP로 붙어 있는 동안만 들어간다 (연결을 끊으면 새 페이지엔 안 붙음). 클릭은 그대로 통과
BANNER_JS = """(() => {
  if (window.top !== window) return;
  const add = () => {
    if (document.getElementById("popme-collecting")) return;
    const d = document.createElement("div");
    d.id = "popme-collecting";
    d.textContent = "POPME가 소식을 모으는 중이에요 · 끝나면 저절로 멈춰요 · 이 창은 닫지 말아 주세요";
    d.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:#e8865a;color:#fff;"
      + "font:13px/1.7 'Malgun Gothic',sans-serif;text-align:center;pointer-events:none;opacity:.93";
    document.documentElement.appendChild(d);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add); else add();
})()"""


# 인터넷 확인: 이름을 찾고(DNS) 연결까지 되는지 — 2026-10-20 아침 09:00에 인터넷이 끊겨 있었는데 그대로 모아서
# 출처 176곳 실패·X 「응답 구조가 바뀐 것 같아요」(오진)·묵은 자료로 만든 브리핑이 나왔다
NET_HOSTS = [("www.gstatic.com", 443), ("github.com", 443), ("www.naver.com", 443)]
OFFLINE = "인터넷이 끊겨서"
RETRY_OFFLINE = 3  # 하루에 「연결되면 다시 모으기」를 몇 번까지


def online(timeout=4):
    """인터넷이 되나: 알려진 곳 중 하나라도 이름을 찾고 연결되면 참."""
    for host, port in NET_HOSTS:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def missed(names):
    """「X·Threads는」「일정은」 — 받침에 맞는 조사까지 붙인 이름."""
    text = "·".join(names)
    last = text[-1]
    batchim = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 > 0
    return text + ("은" if batchim else "는")


def chrome_closed(e):
    """수집 중에 사용자가 전용 Chrome 창을 닫아 Playwright 연결이 끊긴 오류인가."""
    s = str(e).lower()
    # ("disconnected"만 보면 인터넷 끊김 net::ERR_INTERNET_DISCONNECTED까지 걸려서 넣지 않는다)
    return any(k in s for k in ("has been closed", "target closed", "browser closed", "connection closed", "econnrefused"))


def why(e, cfg=None):
    """수집·브리핑 오류 → 사용자에게 보일 짧은 이유. 원문은 log.exception으로 남긴다. cfg: AI 이름을 쓸 설정 (없으면 기본 공급자)."""
    if isinstance(e, NeedLogin):
        return str(e)
    if isinstance(e, json.JSONDecodeError):  # AI 답이 두 번 다 JSON이 아니었다 ("Expecting ',' delimiter …")
        return "AI 답 형식이 깨졌어요. 「브리핑만 다시 만들기」를 눌러 주세요"
    s = str(e).lower()
    if chrome_closed(e):
        return "Chrome 창이 닫혔어요"
    if "net::err_internet_disconnected" in s or "getaddrinfo" in s or "urlopen error" in s or "network" in s:
        return "인터넷 연결이 불안정했어요"
    if "net::err_aborted" in s:
        return "페이지를 여는 도중에 끊겼어요"
    if "timeout" in s or "timed out" in s:
        return "페이지가 너무 오래 걸렸어요"
    if llm.is_limit(s):  # config.load()를 다시 부르지 않는다 (설정 옮기기 같은 부수 효과가 있다)
        return f"{llm.provider_name(cfg or {})} 사용 한도에 걸렸어요. 한도가 풀리면 「브리핑만 다시 만들기」를 눌러 주세요"
    if llm.is_login(s):
        return "AI 로그인이 풀렸어요. 트레이 → 설정 → 브리핑 AI에서 로그인해 주세요"
    if "구조가 바뀐" in s:  # 수집기가 이미 사람 말로 쓴 경고
        return str(e).split(" (로그에")[0]
    return "알 수 없는 문제가 생겼어요 (popme.log에 자세히 남겼어요)"


def calendar_source(cfg):
    """일정을 어디서 읽나: "timetree"(기본) | "ics"(구글 등 ICS 주소) | "caldav"(iCloud 계정 + 앱 암호) | "none"."""
    return cfg.get("calendar", {}).get("source", "timetree")


class Saver:
    """수집 페이지마다 부르는 중간 저장 (on_batch). 계정 규모(users)는 바뀐 것만 저장한다 — 예전엔 페이지마다 그때까지
    모은 계정 전부를 다시 저장해서(수백~천여 명 × 페이지 30개) 쓰기가 제곱으로 늘었다. flush(): 남은 것 저장."""

    def __init__(self, db, users):
        self.db, self.users, self.saved = db, users, {}

    def flush(self):
        fresh = {h: u for h, u in list(self.users.items()) if self.saved.get(h) != u}
        if fresh:
            self.db.upsert_profiles(list(fresh.values()))
            self.saved.update((h, dict(u)) for h, u in fresh.items())

    def __call__(self, batch):
        self.db.upsert_tweets(batch)
        self.flush()


class Jobs:
    def __init__(self, notify=lambda kind, msg: None):
        """notify(kind, msg): kind = 'briefing' | 'error'"""
        self.cfg = config.load()
        self.db = DB(config.DB_PATH)
        self.notify = notify
        self.lock = threading.Lock()
        # 설정 파일이 깨져 대신 쓴 설정으로 켜졌으면 캐릭터가 경고로 알린다 (config.load)
        self.state = {"running": False, "status": "대기 중", "warnings": [config.LOAD_ERROR] if config.LOAD_ERROR else [],
                      "last_run": None}
        self._tried = None
        self.offline_retry = False  # 인터넷이 끊겨 멈춤 → 연결되면 아침 자동 수집을 다시
        self._retries = 0

    def _progress(self, s):
        self.state["status"] = s
        log.info(s)

    # --- 브라우저가 필요한 수집 (TimeTree, X, Threads) ---
    def _browser_collect(self, warnings):
        port = int(self.cfg.get("chrome", {}).get("port", 9333))
        chrome.ensure_chrome(port)
        if self._minimizing():
            # 수집 중 창은 내려 둔다 (기본). Threads·X 스레드가 탭을 열기 전에 내려야 새 탭이 뒤에서 열린다 (chrome.new_page)
            self._with_browser(port, lambda ctx: chrome.set_minimized(
                ctx.pages[0] if ctx.pages else ctx.new_page(), True))
        # X와 Threads는 서로 다른 사이트라 동시에 돌린다. 각 사이트에서 보면 여전히 탭 하나가 사람 속도로 움직인다.
        # (같은 사이트를 탭 여러 개로 동시에 돌리면 요청이 몰려 봇으로 보이니 그렇게 하지 않는다)
        parts = {}

        def progress(key):
            def report(s):  # 상태 표시: "X 수집 중 3/33 · Threads 수집 중 5/47"
                parts[key] = s
                self.state["status"] = " · ".join(parts.values())
                log.info(s)
            return report

        def threads_job():
            try:
                self._with_browser(port, lambda ctx: self._collect_threads(ctx, warnings, progress("threads")))
            except Exception as e:
                log.exception("Threads 실패")
                warnings.append(self._fail("Threads", e))

        th = threading.Thread(target=threads_job, daemon=True)
        th.start()
        videos = [f for f in catalog.active(self.cfg) if f.get("type") == "youtube"]
        yt = threading.Thread(target=self._collect_youtube, args=(port, videos, warnings, progress("youtube")), daemon=True)
        if videos:  # 유튜브도 사이트가 달라 동시에 (탭 하나)
            yt.start()
        try:
            self._with_browser(port, lambda ctx: self._collect_calendar_x(ctx, warnings, progress("x")))
        except Exception as e:  # 붙기 전에 Chrome이 닫혔을 때 등 — 나머지(RSS·브리핑)는 이어서
            log.exception("X 실패")
            warnings.append(self._fail("X", e))
        th.join()
        if videos:
            yt.join()

    def _minimizing(self):
        """수집 중 전용 Chrome 창을 내려 둘까 (기본 켜짐 — config [chrome] minimize_while_collecting). 로그인 창은 해당 없음."""
        return self.cfg.get("chrome", {}).get("minimize_while_collecting", True)

    def _with_browser(self, port, fn):
        """Playwright 동기 API는 스레드끼리 나눠 쓸 수 없어서 스레드마다 따로 붙는다."""
        with chrome.attached(port) as ctx:
            try:
                ctx.add_init_script(BANNER_JS)
            except Exception:
                log.warning("수집 중 안내 띠를 못 붙였어요", exc_info=True)
            if self is not None and self._minimizing():
                # 예비: 새 탭은 뒤에서 열리지만(chrome.new_page), 그래도 창이 다시 커지면 새 탭마다 다시 내린다
                # (X·Threads가 따로 붙으니 양쪽 다)
                ctx.on("page", lambda page: chrome.set_minimized(page, True))
            fn(ctx)

    def _collect_calendar_x(self, ctx, warnings, progress):
        source = calendar_source(self.cfg)
        try:
            cal = self.cfg.get("calendar", {})
            holidays = cal.get("holidays", True)
            if source == "ics":
                events, synced = ics.collect(self.cfg, progress, warnings)
                self.db.upsert_events(events, synced)
                keep = {ics.calendar_id(c).split(":", 1)[0] for c in cal.get("ics", []) if (c.get("url") or "").strip()}
                # 예전 순서 id(ics0…) 일정은 설정된 캘린더가 모두 새 id로 받아졌을 때만 지운다
                # (하나라도 받기에 실패하면 그 캘린더의 예전 일정이 남아 있어야 위젯이 비지 않는다)
                done = {s.split(":", 1)[0] for s in synced}
                self.db.retire_calendars("ics", keep, holidays, keep_legacy=not keep <= done)
            elif source == "caldav":
                self.db.upsert_events(*caldav.collect(self.cfg, progress, warnings))
                keep = {caldav.calendar_id(c).split(":", 1)[0]
                        for c in self.cfg.get("caldav", {}).get("calendars", []) if (c.get("url") or "").strip()}
                self.db.retire_calendars("caldav", keep, holidays)
            elif source == "timetree":
                self.db.upsert_events(*timetree.collect(ctx, self.cfg, progress))
                self.db.retire_calendars("timetree")
            else:  # 안 씀: 예전 출처의 일정이 위젯에 남지 않게
                self.db.retire_calendars("none")
            if source != "none":
                try:
                    event_weather.refresh(self.cfg, self.db)  # 새 일정의 장소 → 그날 날씨
                except Exception:
                    log.exception("일정 장소 확인 실패")
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("일정 수집 실패")
            warnings.append(self._fail("일정", e))
        try:
            since = briefing.window_start(self.db).isoformat()  # 이보다 오래된 글까지 내려오면 스크롤 멈춤
            # 30개가 넘으면 관심사 계정은 돌아가며 — 그 계정들은 지난번에 본 시각까지 내려 본다 (since_by)
            handles, since_by = accounts.x_plan(self.cfg, self.db, since, gaps=warnings)
            users = {}  # 응답에 실려 오는 계정 규모 (발굴 인기 기준용)
            visited = []  # 실제로 글을 받아 온 계정 — 로그인이 풀리거나 창이 닫혀 못 본 계정은 「봤다」로 남기지 않는다
            saver = self._saver(users)
            meta = {}  # 리스트 이름 (리스트를 보면 채워진다)
            try:
                # 페이지 하나 볼 때마다 저장 — 중간에 Chrome이 닫혀도 모은 데까지는 남는다
                x.collect(ctx, self.cfg, handles, progress, since, users, on_batch=saver,
                          since_by=since_by, visited=visited, list_since=accounts.list_since(self.cfg, since),
                          list_meta=meta)
            finally:
                accounts.mark_x_visited(visited)
            saver.flush()  # 새 글 없이 끝난 페이지의 계정 규모도
            self._sync_x_list(ctx, meta.get("name"), warnings, progress)
            # 발굴은 이번에 연 계정만이 아니라 보는 계정 전체를 「이미 보는 계정」으로 (돌아가며 안 연 계정이 후보로 오르지 않게)
            discovery.update(self.db, [a["handle"] for a in accounts.core_accounts(self.cfg, self.db)], self.cfg)
            self._calibrate()
            self._check_candidates("x", ctx, warnings, progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("X 실패")
            warnings.append(self._fail("X", e))
        progress("X 완료")

    def _sync_x_list(self, ctx, name, warnings, progress):
        """X 리스트를 보는 계정에 맞춘다 (넣기·빼기 합쳐 하루 LIST_ADD_PER_DAY개, accounts.list_todo). 실패해도 수집은 그대로
        (리스트에 없는 계정은 계정 페이지로 돌아가며 본다)."""
        adds, removes, name = accounts.list_todo(self.cfg, self.db, name)
        if not adds and not removes:
            return
        try:
            x.sync_list(ctx, adds, removes, name, lambda h, r: accounts.mark_list(self.cfg, h, r), progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("X 리스트 동기화 실패")
            warnings.append(self._fail("X 리스트에 계정 넣기", e))

    def _collect_threads(self, ctx, warnings, progress):
        try:
            th_users = accounts.threads_accounts(self.cfg, self.db)
            since = briefing.window_start(self.db).isoformat()
            users = {}  # 계정 페이지에 실려 오는 팔로워 수 (발굴 인기 기준용)
            saver = self._saver(users)
            threads.collect(ctx, self.cfg, th_users, progress, since, users, on_batch=saver)
            saver.flush()  # 새 글 없이 끝난 페이지의 계정 규모도
            discovery.update_threads(self.db, th_users, self.cfg)
            self._check_candidates("threads", ctx, warnings, progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("Threads 실패")
            warnings.append(self._fail("Threads", e))
        progress("Threads 완료")

    def _collect_youtube(self, port, feeds, warnings, progress):
        """유튜브 채널 새 영상 (popme/collectors/youtube.py) → RSS 글과 같은 표에."""
        try:
            since = briefing.window_start(self.db)
            items = []

            def run(ctx):
                got, errs = youtube.collect(ctx, feeds, progress, since)
                items.extend(got)
                warnings.extend(f"유튜브 실패 — {e}" for e in errs)
            self._with_browser(port, run)
            self.db.upsert_items(items)
        except Exception as e:
            log.exception("유튜브 실패")
            warnings.append(self._fail("유튜브", e))

    def _calibrate(self):
        """분야 발굴 기준 보정 (popme/fieldscale.py): 매주, 또는 기준이 아직 없는 관심사가 있으면 바로 — 방금 모은 계정 팔로워로."""
        try:
            if fieldscale.due() or fieldscale.missing(self.cfg):
                done = fieldscale.calibrate(self.db, self.cfg)
                if done:
                    log.info("분야 발굴 기준 보정: %s", ", ".join(done))
        except Exception:
            log.exception("분야 발굴 기준 보정 실패")

    def _saver(self, users):
        return Saver(self.db, users)

    def _fail(self, what, e):
        """수집 경고 한 줄 (사람 말). Chrome이 닫힌 거면 CHROME_CLOSED로 시작 — 끝나고 「다시 모으기」를 띄운다.
        인터넷이 끊긴 거면 그렇게 말한다 (빈 응답을 「X 응답 구조가 바뀐 것 같아요」로 오진하지 않게)."""
        if not online():
            return f"{OFFLINE} {missed([what])} 이번에 못 모았어요"
        if chrome_closed(e):
            if self is not None:
                self._closed.append(what)
            return f"{CHROME_CLOSED} {missed([what])} 이번에 못 모았어요"
        return f"{what} 수집 실패: {why(e, getattr(self, 'cfg', None))}"

    def _check_candidates(self, platform, ctx, warnings, progress):
        """발굴 1·2단계: 팔로워 기준 → 필요한 후보만 프로필을 한 번 열어 (팔로워·)꾸준함 확인 → 승인 계정 점검."""
        try:
            discovery.evaluate(self.db, self.cfg, platform)
            todo = discovery.needs_check(self.db, self.cfg, platform)
            if todo:
                users = {}
                collector = x if platform == "x" else threads
                names = [h.split("/", 1)[-1] for h in todo]
                self.db.upsert_tweets(collector.check_profiles(ctx, names, progress, users))
                self.db.upsert_profiles(users.values())
                self.db.mark_checked(todo)
                discovery.evaluate(self.db, self.cfg, platform)
            discovery.review(self.db, self.cfg, platform)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("발굴 후보 확인 실패")
            warnings.append(f"발굴 후보 확인 실패 ({platform}): {e}")

    def _run(self, make_brief, collect=True):
        warnings, self._closed = [], []  # _closed: Chrome이 닫혀 못 모은 것 (X·Threads·일정)
        try:
            self.state.update(running=True, warnings=[])
            if collect and not online():  # 처음부터 끊겨 있으면 모으지도, 묵은 자료로 브리핑을 만들지도 않는다
                return self._offline(warnings, "인터넷이 연결돼 있지 않아서 모으지 않았어요. 연결되면 다시 모을게요")
            if collect:
                try:  # 전용 Chrome이 늦게 뜨거나 안 떠도 RSS·브리핑은 이어서 (예전엔 그날 브리핑이 통째로 없었다)
                    self._browser_collect(warnings)
                except Exception as e:
                    log.exception("전용 Chrome 수집 실패")
                    warnings.append(self._fail("전용 Chrome", e))
                self._progress("RSS 수집 중")
                feeds = catalog.active(self.cfg)
                items, errs = sources.collect(feeds, self._progress)
                self.db.upsert_items(items)
                warnings += [f"RSS 실패 — {e}" for e in errs]
                if len(errs) >= max(5, len(feeds) // 2) and not online():  # 모으는 도중에 끊김
                    return self._offline(warnings, "인터넷이 끊겨서 이번엔 거의 못 모았어요. 연결되면 다시 모을게요")
            if make_brief:
                self._progress(f"브리핑 작성 중 ({llm.provider_name(self.cfg)})")
                date, md, payload = briefing.make_briefing(self.cfg, self.db)
                try:
                    warnings += funnel.warnings(self.db, payload.get("stats") or {}, date)
                except Exception:
                    log.exception("평소와 견주기 실패")
                if warnings:
                    md += "\n\n---\n수집 경고\n" + "\n".join(f"- {w}" for w in warnings)
                self.db.save_briefing(date, md, json.dumps(payload, ensure_ascii=False))
                discovery.remember_verdicts(self.db, payload["items"], date)  # 발굴 탭에도 같은 추천 이유
                atomic_write_text(config.BRIEFING_DIR / f"{date}.md", md)
                self.notify("briefing", "브리핑이 준비됐어요")
            self._progress("완료" + (f" (경고 {len(warnings)}개)" if warnings else ""))
            if self._closed:  # 사용자가 창을 닫은 것 — 몰래 다시 켜지 않고, 다시 모을지 묻는다
                self.notify("chrome_closed", f"{missed(list(dict.fromkeys(self._closed)))} 이번에 못 모았어요")
        except Exception as e:
            log.exception("실행 실패")
            reason = why(e, self.cfg)
            warnings.append(reason)
            self._progress(f"실패: {reason}")
            self.notify("error", f"브리핑을 못 만들었어요: {reason}")
        finally:
            self.state.update(running=False, warnings=warnings, last_run=datetime.now().strftime("%H:%M"))
            self.lock.release()

    def _offline(self, warnings, text):
        """인터넷이 없어서 멈춤: 경고는 한 줄로, 브리핑은 만들지 않고, 아침 자동 수집이면 연결되면 다시 (morning_loop)."""
        log.warning(text)
        warnings[:] = [text]
        self.offline_retry = True
        self._progress("인터넷 연결을 기다리는 중")
        self.notify("offline", text)

    def run_full(self, force=False, blocking=False, make_brief=True, collect=True):
        """collect=False면 수집 없이 저장된 데이터로 브리핑만 다시 만든다."""
        if not self.lock.acquire(blocking=False):
            return False
        if blocking:
            self._run(make_brief, collect)
        else:
            threading.Thread(target=self._run, args=(make_brief, collect), daemon=True).start()
        return True

    def morning_tick(self, now):
        """지정 시각이 지났는데 오늘 브리핑이 없으면 실행. 인터넷이 없으면 연결될 때까지 기다렸다가 (몇 분이 걸려도) 그때 모은다.
        모으다 끊겼으면 연결되면 다시 (하루 RETRY_OFFLINE번까지). 반환: 실행했나."""
        after = self.cfg.get("schedule", {}).get("briefing_after", "09:00")
        if now.strftime("%H:%M") < after or self.db.briefing_for(now.strftime("%Y-%m-%d")) or self.state["running"]:
            return False
        retry = self.offline_retry and self._retries < RETRY_OFFLINE
        if self._tried == now.date() and not retry:
            return False  # 실패해도 자동 실행은 하루 한 번만 (인터넷이 끊겨 멈춘 건 빼고)
        if not online():
            if self.state["status"] != "인터넷 연결을 기다리는 중":
                self._progress("인터넷 연결을 기다리는 중")
            return False
        self._retries = self._retries + 1 if self._tried == now.date() else 0
        self._tried = now.date()
        self.offline_retry = False
        return self.run_full()

    def morning_loop(self):
        """앱이 켜져 있는 동안 1분마다 morning_tick."""
        while True:
            try:
                self.morning_tick(datetime.now())
            except Exception:
                log.exception("스케줄 확인 실패")
            time.sleep(60)
