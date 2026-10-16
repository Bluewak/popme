"""수집 → 브리핑 파이프라인과 아침 스케줄."""
import json
import logging
import threading
import time
from datetime import datetime

from popme import briefing, catalog, chrome, config, discovery, event_weather, llm, sources
from popme.collectors import NeedLogin, caldav, ics, threads, timetree, x
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


def why(e):
    """수집·브리핑 오류 → 사용자에게 보일 짧은 이유. 원문은 log.exception으로 남긴다."""
    if isinstance(e, NeedLogin):
        return str(e)
    s = str(e).lower()
    if chrome_closed(e):
        return "Chrome 창이 닫혔어요"
    if "net::err_internet_disconnected" in s or "getaddrinfo" in s or "urlopen error" in s or "network" in s:
        return "인터넷 연결이 불안정했어요"
    if "timeout" in s or "timed out" in s:
        return "페이지가 너무 오래 걸렸어요"
    if any(k in s for k in ("limit", "quota", "429", "rate")):
        return f"{llm.provider_name(config.load())} 사용 한도에 걸렸어요. 한도가 풀리면 「브리핑만 다시 만들기」를 눌러 주세요"
    if any(k in s for k in ("login", "auth", "api key", "credential", "401")):
        return "AI 로그인이 풀렸어요. 트레이 → 설정 바꾸기 → 브리핑 AI에서 로그인해 주세요"
    if "구조가 바뀐" in s:  # 수집기가 이미 사람 말로 쓴 경고
        return str(e).split(" (로그에")[0]
    return "알 수 없는 문제가 생겼어요 (popme.log에 자세히 남겼어요)"


def calendar_source(cfg):
    """일정을 어디서 읽나: "timetree"(기본) | "ics"(구글 등 ICS 주소) | "caldav"(iCloud 계정 + 앱 암호) | "none"."""
    return cfg.get("calendar", {}).get("source", "timetree")


class Jobs:
    def __init__(self, notify=lambda kind, msg: None):
        """notify(kind, msg): kind = 'briefing' | 'error'"""
        self.cfg = config.load()
        self.db = DB(config.DB_PATH)
        self.notify = notify
        self.lock = threading.Lock()
        self.state = {"running": False, "status": "대기 중", "warnings": [], "last_run": None}
        self._tried = None

    def _progress(self, s):
        self.state["status"] = s
        log.info(s)

    # --- 브라우저가 필요한 수집 (TimeTree, X, Threads) ---
    def _browser_collect(self, warnings):
        port = int(self.cfg.get("chrome", {}).get("port", 9333))
        chrome.ensure_chrome(port)
        if self.cfg.get("chrome", {}).get("minimize_while_collecting"):
            # Threads·X 스레드가 탭을 열기 전에 최소화해야 새 탭이 배경 탭으로 열린다 (chrome.new_page)
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
        try:
            self._with_browser(port, lambda ctx: self._collect_calendar_x(ctx, warnings, progress("x")))
        except Exception as e:  # 붙기 전에 Chrome이 닫혔을 때 등 — 나머지(RSS·브리핑)는 이어서
            log.exception("X 실패")
            warnings.append(self._fail("X", e))
        th.join()

    def _with_browser(self, port, fn):
        """Playwright 동기 API는 스레드끼리 나눠 쓸 수 없어서 스레드마다 따로 붙는다."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                ctx = browser.contexts[0]
                try:
                    ctx.add_init_script(BANNER_JS)
                except Exception:
                    log.warning("수집 중 안내 띠를 못 붙였어요", exc_info=True)
                fn(ctx)
            finally:
                # connect_over_cdp로 붙은 브라우저는 close()해도 Chrome 자체는 계속 떠 있다.
                # 사용자가 창을 닫아 이미 끊겼으면 여기서 또 오류가 나는데, 그걸로 브리핑까지 멈추지 않게
                try:
                    browser.close()
                except Exception:
                    log.info("전용 Chrome 연결 정리 실패 (이미 닫힘)", exc_info=True)

    def _collect_calendar_x(self, ctx, warnings, progress):
        source = calendar_source(self.cfg)
        try:
            if source == "ics":
                self.db.upsert_events(*ics.collect(self.cfg, progress, warnings))
            elif source == "caldav":
                self.db.upsert_events(*caldav.collect(self.cfg, progress, warnings))
            elif source == "timetree":
                self.db.upsert_events(*timetree.collect(ctx, self.cfg, progress))
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
            handles = briefing.x_to_visit(self.cfg, self.db)  # 30개가 넘으면 관심사 계정은 돌아가며
            since = briefing.window_start(self.db).isoformat()  # 이보다 오래된 글까지 내려오면 스크롤 멈춤
            users = {}  # 응답에 실려 오는 계정 규모 (발굴 인기 기준용)
            # 페이지 하나 볼 때마다 저장 — 중간에 Chrome이 닫혀도 모은 데까지는 남는다
            x.collect(ctx, self.cfg, handles, progress, since, users, on_batch=self._saver(users))
            self.db.upsert_profiles(list(users.values()))  # 새 글 없이 끝난 페이지의 계정 규모도
            discovery.update(self.db, handles, self.cfg)
            self._check_candidates("x", ctx, warnings, progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("X 실패")
            warnings.append(self._fail("X", e))
        progress("X 완료")

    def _collect_threads(self, ctx, warnings, progress):
        try:
            th_users = briefing.threads_accounts(self.cfg, self.db)
            since = briefing.window_start(self.db).isoformat()
            users = {}  # 계정 페이지에 실려 오는 팔로워 수 (발굴 인기 기준용)
            threads.collect(ctx, self.cfg, th_users, progress, since, users, on_batch=self._saver(users))
            self.db.upsert_profiles(list(users.values()))  # 새 글 없이 끝난 페이지의 계정 규모도
            discovery.update_threads(self.db, th_users, self.cfg)
            self._check_candidates("threads", ctx, warnings, progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("Threads 실패")
            warnings.append(self._fail("Threads", e))
        progress("Threads 완료")

    def _saver(self, users):
        def save(batch):
            self.db.upsert_tweets(batch)
            self.db.upsert_profiles(list(users.values()))
        return save

    def _fail(self, what, e):
        """수집 경고 한 줄 (사람 말). Chrome이 닫힌 거면 CHROME_CLOSED로 시작 — 끝나고 「다시 모으기」를 띄운다."""
        if chrome_closed(e):
            if self is not None:
                self._closed.append(what)
            return f"{CHROME_CLOSED} {missed([what])} 이번에 못 모았어요"
        return f"{what} 수집 실패: {why(e)}"

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
            if collect:
                self._browser_collect(warnings)
                self._progress("RSS 수집 중")
                items, errs = sources.collect(catalog.active(self.cfg), self._progress)
                self.db.upsert_items(items)
                warnings += [f"RSS 실패 — {e}" for e in errs]
            if make_brief:
                self._progress(f"브리핑 작성 중 ({llm.provider_name(self.cfg)})")
                date, md, payload = briefing.make_briefing(self.cfg, self.db)
                if warnings:
                    md += "\n\n---\n수집 경고\n" + "\n".join(f"- {w}" for w in warnings)
                self.db.save_briefing(date, md, json.dumps(payload, ensure_ascii=False))
                discovery.remember_verdicts(self.db, payload["items"], date)  # 발굴 탭에도 같은 추천 이유
                (config.BRIEFING_DIR / f"{date}.md").write_text(md, encoding="utf-8")
                self.notify("briefing", "브리핑이 준비됐어요")
            self._progress("완료" + (f" (경고 {len(warnings)}개)" if warnings else ""))
            if self._closed:  # 사용자가 창을 닫은 것 — 몰래 다시 켜지 않고, 다시 모을지 묻는다
                self.notify("chrome_closed", f"{missed(list(dict.fromkeys(self._closed)))} 이번에 못 모았어요")
        except Exception as e:
            log.exception("실행 실패")
            reason = why(e)
            warnings.append(reason)
            self._progress(f"실패: {reason}")
            self.notify("error", f"브리핑을 못 만들었어요: {reason}")
        finally:
            self.state.update(running=False, warnings=warnings, last_run=datetime.now().strftime("%H:%M"))
            self.lock.release()

    def run_full(self, force=False, blocking=False, make_brief=True, collect=True):
        """collect=False면 수집 없이 저장된 데이터로 브리핑만 다시 만든다."""
        if not self.lock.acquire(blocking=False):
            return False
        if blocking:
            self._run(make_brief, collect)
        else:
            threading.Thread(target=self._run, args=(make_brief, collect), daemon=True).start()
        return True

    def morning_loop(self):
        """앱이 켜져 있는 동안 1분마다: 지정 시각이 지났는데 오늘 브리핑이 없으면 실행."""
        while True:
            try:
                after = self.cfg.get("schedule", {}).get("briefing_after", "09:00")
                now = datetime.now()
                if now.strftime("%H:%M") >= after and not self.db.briefing_for(now.strftime("%Y-%m-%d")):
                    if not self.state["running"] and self._tried != now.date():
                        self._tried = now.date()   # 실패해도 자동 실행은 하루 한 번만
                        self.run_full()
            except Exception:
                log.exception("스케줄 확인 실패")
            time.sleep(60)
