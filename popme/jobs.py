"""수집 → 브리핑 파이프라인과 아침 스케줄."""
import json
import logging
import threading
import time
from datetime import datetime

from popme import briefing, chrome, config, discovery, event_weather
from popme.collectors import NeedLogin, rss, threads, timetree, x
from popme.db import DB

log = logging.getLogger(__name__)


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

    # --- 브라우저가 필요한 수집 (TimeTree, X) ---
    def _browser_collect(self, warnings):
        from playwright.sync_api import sync_playwright
        port = int(self.cfg.get("chrome", {}).get("port", 9333))
        chrome.ensure_chrome(port)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            first = ctx.pages[0] if ctx.pages else ctx.new_page()
            if self.cfg.get("chrome", {}).get("minimize_while_collecting"):
                chrome.set_minimized(first, True)
            try:
                self.db.upsert_events(*timetree.collect(ctx, self.cfg, self._progress))
                try:
                    event_weather.refresh(self.cfg, self.db)  # 새 일정의 장소 → 그날 날씨
                except Exception:
                    log.exception("일정 장소 확인 실패")
            except NeedLogin as e:
                warnings.append(str(e))
            except Exception as e:
                log.exception("TimeTree 실패")
                warnings.append(f"TimeTree 수집 실패: {e}")
            try:
                handles = [a["handle"] for a in briefing.core_accounts(self.cfg, self.db)]
                tweets = x.collect(ctx, self.cfg, handles, self._progress)
                self.db.upsert_tweets(tweets)
                discovery.update(self.db, handles)
            except NeedLogin as e:
                warnings.append(str(e))
            except Exception as e:
                log.exception("X 실패")
                warnings.append(f"X 수집 실패: {e}")
            try:
                th_users = briefing.threads_accounts(self.cfg, self.db)
                self.db.upsert_tweets(threads.collect(ctx, self.cfg, th_users, self._progress))
                discovery.update_threads(self.db, th_users)
            except NeedLogin as e:
                warnings.append(str(e))
            except Exception as e:
                log.exception("Threads 실패")
                warnings.append(f"Threads 수집 실패: {e}")
            # connect_over_cdp로 붙은 브라우저는 close()해도 Chrome 자체는 계속 떠 있다
            browser.close()

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
                self._progress("브리핑 작성 중 (Claude)")
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
