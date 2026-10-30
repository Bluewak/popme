"""새 분야를 만들 때 그 분야 유명 계정에서 출발해 타고 내려간다 (popme/fieldscale.py의 기준을 그 분야 자체 분포로 정하려고).

유명 계정의 팔로잉 목록을 전용 Chrome으로 열면 그가 팔로우하는 계정들의 팔로워·자기소개가 응답에 같이 실려 온다
— 한 번 열 때마다 한 단계 아래 계정 수십 곳의 규모를 얻는다 (crawl). known()은 앱 DB(평소 수집으로 쌓인 계정 규모).
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


def _users_in(obj, out):
    """응답 트리에서 X 계정(User) → [(handle, 계정 규모 + 자기소개)]. 팔로잉 목록·프로필 응답 모두."""
    from popme.collectors import x as xcol
    if isinstance(obj, dict):
        if obj.get("__typename") == "User":
            u = xcol.parse_user(obj)
            if u:
                leg = obj.get("legacy") or {}
                u["bio"] = leg.get("description") or ((obj.get("profile_bio") or {}).get("description")) or ""
                out.append(u)
        for v in obj.values():
            _users_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _users_in(v, out)


def crawl(cfg, seeds):
    """유명 계정(seeds)에서 한 단계 아래로: 각자의 팔로잉 목록을 열어 → {"users": {handle(소문자): 규모·자기소개},
    "edges": [(유명 계정, 그가 팔로우하는 계정, 그 계정 자기소개)]}. 그 분야 유명 계정이 직접 고른 계정이 곧 그 분야 사람들이다.
    앱이 모으는 중이 아닐 때만, MAX_X곳까지. 열지 못하면 빈 결과."""
    from playwright.sync_api import sync_playwright

    from popme.collectors import x as xcol
    port = int(cfg.get("chrome", {}).get("port", 9333))
    seeds = list(dict.fromkeys(h for h in seeds if h))[:MAX_X]
    users, edges = {}, []
    if not seeds or app_collecting():
        return {"users": {}, "edges": []}
    if not _chrome_up(port):  # 수집 때처럼 켜고 창은 내려 둔다 (새 분야를 만드는 건 사용자가 시작한 일)
        try:
            chrome.ensure_chrome(port)
        except Exception:
            log.warning("전용 Chrome을 못 켜서 분야 조사를 건너뛰어요", exc_info=True)
            return {"users": {}, "edges": []}
    now = {"seed": ""}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        try:
            chrome.set_minimized(ctx.pages[0] if ctx.pages else ctx.new_page(), True)
        except Exception:
            log.info("전용 Chrome 창 내리기 실패", exc_info=True)
        page = chrome.new_page(ctx)

        def handler(r):
            if "/i/api/graphql/" not in r.url:
                return
            found = []
            try:
                _users_in(r.json(), found)
            except Exception:
                return
            following = "Following" in r.url
            for u in found:
                h = u["handle"].lower()
                users[h] = u
                if following and h != now["seed"].lower():
                    edges.append((now["seed"], u["handle"], u.get("bio", "")))
        page.on("response", handler)
        try:
            for i, h in enumerate(seeds):
                now["seed"] = h
                page.goto(f"https://x.com/{h}/following", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)
                if any(m in page.url for m in xcol.BLOCK_MARKERS):
                    log.warning("X가 막아서 분야 조사를 멈춰요 (%s)", page.url)
                    break
                page.mouse.wheel(0, 4000)  # 한 번 더 내려 목록을 조금 더
                page.wait_for_timeout(2500)
                if i < len(seeds) - 1:
                    page.wait_for_timeout(random.uniform(8000, 15000))
        finally:
            try:
                page.close()
                browser.close()  # 연결만 끊는다 (Chrome은 그대로)
            except Exception:
                pass
    return {"users": users, "edges": edges}


def x_followers(cfg, handles):
    """X 계정 팔로워 {handle(소문자): 수} — crawl의 계정 규모에서."""
    got = crawl(cfg, handles)["users"]
    return {h.lower(): got[h.lower()]["followers"] for h in handles if h and h.lower() in got
            and isinstance(got[h.lower()].get("followers"), int)}
