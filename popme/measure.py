"""새 분야를 만들 때 그 분야 대표 계정의 팔로워를 잰다 (popme/fieldscale.py의 기준을 정하려고).

먼저 앱 DB(평소 수집할 때 쌓인 계정 규모)를 보고, 없는 X 계정만 전용 Chrome으로 프로필을 열어 응답에 실린 수를 읽는다.
사용자 본인의 로그인된 X라 조심한다: 한 번에 MAX_X곳까지, 사이 8~15초, 앱이 모으는 중이면 열지 않는다, 막히면 바로 멈춘다.
전용 Chrome이 꺼져 있으면 수집 때처럼 켜고 창은 내려 둔다.
"""
import logging
import random
import re
import sqlite3
import urllib.request

from popme import chrome, config

log = logging.getLogger(__name__)
MAX_X = 10
LOG = config.DATA_DIR / "popme.log"


def known(handles):
    """앱 DB에 이미 있는 X 팔로워 {handle(소문자): 수}."""
    db = config.DATA_DIR / "popme.db"
    if not handles or not db.exists():
        return {}
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        marks = ",".join("?" * len(handles))
        rows = c.execute(f"SELECT handle, followers FROM profiles WHERE platform='x' AND followers IS NOT NULL "
                         f"AND lower(handle) IN ({marks})", [h.lower() for h in handles]).fetchall()
        c.close()
        return {h.lower(): f for h, f in rows}
    except sqlite3.Error:
        log.warning("앱 DB에서 팔로워 읽기 실패", exc_info=True)
        return {}


def app_collecting():
    """앱이 지금 모으는 중인가 (로그의 마지막 popme.jobs 줄이 끝난 상태가 아니면). 모르면 모으는 중으로 본다."""
    try:
        tail = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except OSError:
        return False
    jobs = [line for line in tail if "popme.jobs:" in line]
    return bool(jobs) and not re.search(r"popme\.jobs: (완료|실패|인터넷 연결을 기다리는 중)", jobs[-1])


def _chrome_up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3).close()
        return True
    except OSError:
        return False


def x_followers(cfg, handles):
    """X 계정 팔로워 {handle(소문자): 수} — 전용 Chrome이 떠 있고 앱이 모으는 중이 아닐 때만, MAX_X곳까지."""
    from playwright.sync_api import sync_playwright

    from popme.collectors import x as xcol
    port = int(cfg.get("chrome", {}).get("port", 9333))
    handles = list(dict.fromkeys(h for h in handles if h))[:MAX_X]
    if not handles or app_collecting():
        return {}
    if not _chrome_up(port):  # 수집 때처럼 켜고 창은 내려 둔다 (새 분야를 만드는 건 사용자가 시작한 일)
        try:
            chrome.ensure_chrome(port)
        except Exception:
            log.warning("전용 Chrome을 못 켜서 팔로워 재기를 건너뛰어요", exc_info=True)
            return {}
    users, out = {}, {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        try:
            chrome.set_minimized(ctx.pages[0] if ctx.pages else ctx.new_page(), True)
        except Exception:
            log.info("전용 Chrome 창 내리기 실패", exc_info=True)
        page = chrome.new_page(ctx)

        def handler(r):
            if "/i/api/graphql/" in r.url and "UserByScreenName" in r.url:
                try:
                    xcol.walk(r.json(), [], users)
                except Exception:
                    pass
        page.on("response", handler)
        try:
            for i, h in enumerate(handles):
                page.goto(f"https://x.com/{h}", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                if any(m in page.url for m in xcol.BLOCK_MARKERS) or "rate" in (page.title() or "").lower():
                    log.warning("X가 막아서 팔로워 재기를 멈춰요 (%s)", page.url)
                    break
                if i < len(handles) - 1:
                    page.wait_for_timeout(random.uniform(8000, 15000))
        finally:
            try:
                page.close()
                browser.close()  # 연결만 끊는다 (Chrome은 그대로)
            except Exception:
                pass
    for u in users.values():
        if u.get("handle") and isinstance(u.get("followers"), int):
            out[u["handle"].lower()] = u["followers"]
    return {h.lower(): out[h.lower()] for h in handles if h.lower() in out}
