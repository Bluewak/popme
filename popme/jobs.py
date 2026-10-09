"""수집 → 브리핑 파이프라인과 아침 스케줄."""
import json
import logging
import threading
import time
from datetime import datetime

from popme import briefing, chrome, config, discovery, event_weather, llm
from popme.collectors import NeedLogin, ics, rss, threads, timetree, x
from popme.db import DB

log = logging.getLogger(__name__)


def calendar_source(cfg):
    """일정을 어디서 읽나: "timetree"(기본) | "ics"(구글·iCloud 등 ICS 주소) | "none"."""
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
                warnings.append(f"Threads 수집 실패: {e}")

        th = threading.Thread(target=threads_job, daemon=True)
        th.start()
        self._with_browser(port, lambda ctx: self._collect_calendar_x(ctx, warnings, progress("x")))
        th.join()

    def _with_browser(self, port, fn):
        """Playwright 동기 API는 스레드끼리 나눠 쓸 수 없어서 스레드마다 따로 붙는다."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                fn(browser.contexts[0])
            finally:
                # connect_over_cdp로 붙은 브라우저는 close()해도 Chrome 자체는 계속 떠 있다
                browser.close()

    def _collect_calendar_x(self, ctx, warnings, progress):
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        if self.cfg.get("chrome", {}).get("minimize_while_collecting"):
            chrome.set_minimized(first, True)
        source = calendar_source(self.cfg)
        try:
            if source == "ics":
                self.db.upsert_events(*ics.collect(self.cfg, progress, warnings))
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
            warnings.append(f"일정 수집 실패: {e}")
        try:
            handles = [a["handle"] for a in briefing.core_accounts(self.cfg, self.db)]
            since = briefing.window_start(self.db).isoformat()  # 이보다 오래된 글까지 내려오면 스크롤 멈춤
            users = {}  # 응답에 실려 오는 계정 규모 (발굴 인기 기준용)
            tweets = x.collect(ctx, self.cfg, handles, progress, since, users)
            self.db.upsert_tweets(tweets)
            self.db.upsert_profiles(users.values())
            discovery.update(self.db, handles, self.cfg)
            self._check_candidates("x", ctx, warnings, progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("X 실패")
            warnings.append(f"X 수집 실패: {e}")
        progress("X 완료")

    def _collect_threads(self, ctx, warnings, progress):
        try:
            th_users = briefing.threads_accounts(self.cfg, self.db)
            since = briefing.window_start(self.db).isoformat()
            users = {}  # 계정 페이지에 실려 오는 팔로워 수 (발굴 인기 기준용)
            self.db.upsert_tweets(threads.collect(ctx, self.cfg, th_users, progress, since, users))
            self.db.upsert_profiles(users.values())
            discovery.update_threads(self.db, th_users, self.cfg)
            self._check_candidates("threads", ctx, warnings, progress)
        except NeedLogin as e:
            warnings.append(str(e))
        except Exception as e:
            log.exception("Threads 실패")
            warnings.append(f"Threads 수집 실패: {e}")
        progress("Threads 완료")

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
        warnings = []
        try:
            self.state.update(running=True, warnings=[])
            if collect:
                self._browser_collect(warnings)
                self._progress("RSS 수집 중")
                items, errs = rss.collect(self.cfg, self._progress)
                self.db.upsert_items(items)
                warnings += [f"RSS 실패 — {e}" for e in errs]
            if make_brief:
                self._progress(f"브리핑 작성 중 ({llm.provider_name(self.cfg)})")
                date, md, payload = briefing.make_briefing(self.cfg, self.db)
                if warnings:
                    md += "\n\n---\n수집 경고\n" + "\n".join(f"- {w}" for w in warnings)
                self.db.save_briefing(date, md, json.dumps(payload, ensure_ascii=False))
                (config.BRIEFING_DIR / f"{date}.md").write_text(md, encoding="utf-8")
                self.notify("briefing", "브리핑이 준비됐어요")
            self._progress("완료" + (f" (경고 {len(warnings)}개)" if warnings else ""))
        except Exception as e:
            log.exception("실행 실패")
            warnings.append(str(e))
            self._progress(f"실패: {e}")
            self.notify("error", f"실행 실패: {e}")
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
