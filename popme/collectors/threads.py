"""Threads 수집: 전용 Chrome에서 검색·토픽 태그·계정 페이지를 열고, 페이지에 실려 오는 게시물 데이터를 읽는다.

발굴 로직 (사용자 승인): 여러 검색어·태그에 반복해서 등장하고 반응(좋아요)이 큰 한국어 작성자를 후보로 올린다.
"""
import json
import logging
import random
import re
from datetime import datetime, timezone
from urllib.parse import quote

from popme.collectors import NeedLogin, dump_raw, reached_since

log = logging.getLogger(__name__)
HANGUL = re.compile(r"[가-힣]")


def is_korean(text):
    return len(HANGUL.findall(text or "")) >= 5


def walk(o, out, users=None):
    """게시물을 out에 모은다. users(dict)를 주면 팔로워 수가 실린 사용자 객체도 모은다
    (프로필 페이지·마우스 올리기 카드에만 실려 오고, 태그·검색 결과에는 없다)."""
    if isinstance(o, dict):
        if users is not None and isinstance(o.get("username"), str) and isinstance(o.get("follower_count"), int):
            users[o["username"].lower()] = {
                "platform": "threads", "handle": o["username"], "name": o.get("full_name") or o["username"],
                "followers": o["follower_count"], "following": None, "posts": None,  # Threads는 팔로잉 수를 안 줌
                "verified": "verified" if o.get("is_verified") else "", "org": None, "lang": None}
        u = o.get("user")
        if isinstance(u, dict) and u.get("username") and o.get("code") and ("caption" in o or "text_post_app_info" in o):
            cap = o.get("caption") or {}
            text = (cap.get("text") if isinstance(cap, dict) else "") or ""
            taken = o.get("taken_at")
            out.append({
                "id": f"th:{o['code']}",
                "author": u["username"], "author_name": u.get("full_name") or u["username"],
                "text": text,
                "created_at": datetime.fromtimestamp(taken, timezone.utc).isoformat() if taken else None,
                "lang": "ko" if is_korean(text) else None,
                "likes": o.get("like_count") or 0, "retweets": 0, "quotes": 0, "views": None,
                "replies": (o.get("text_post_app_info") or {}).get("direct_reply_count") or 0,
                "urls": "[]", "kind": "post", "ref_author": None, "ref_id": None,
                "platform": "threads", "link": f"https://www.threads.com/@{u['username']}/post/{o['code']}",
            })
        for v in o.values():
            walk(v, out, users)
    elif isinstance(o, list):
        for v in o:
            walk(v, out, users)


def _page_posts(page, responses, from_responses, users=None):
    """지금까지 받은 게시물. from_responses는 응답을 푼 결과를 쌓아 두는 리스트 (같은 응답을 두 번 풀지 않게)."""
    while len(from_responses) < len(responses):
        posts = []
        try:
            walk(responses[len(from_responses)].json(), posts, users)
        except Exception:
            pass
        from_responses.append(posts)
    posts = [p for ps in from_responses for p in ps]
    # 첫 화면 게시물은 XHR이 아니라 HTML 안 JSON으로 실려 온다. 프로필 주인의 팔로워 수는 게시물이 없는 덩어리에 따로 있다
    for raw in page.locator('script[type="application/json"]').all_text_contents():
        if '"caption"' in raw or (users is not None and '"follower_count"' in raw):
            try:
                walk(json.loads(raw), posts, users)
            except ValueError:
                pass
    return posts


def _visit(page, url, via, scrolls, sink, owner=None, since=None, users=None):
    """owner·since를 주면 (계정 페이지) 그 계정 글이 since 이전까지 내려왔을 때 스크롤을 멈춘다.
    users(dict)에는 페이지에 실린 팔로워 수를 모은다."""
    responses, from_responses = [], []
    handler = lambda r: responses.append(r) if ("graphql" in r.url or "/api/" in r.url) else None
    page.on("response", handler)
    done = 0
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(random.uniform(4000, 6000))
        if "/login" in page.url:
            raise NeedLogin("Threads 로그인이 필요해요. 트레이 메뉴 > 전용 Chrome 열기에서 로그인해 주세요.")
        for _ in range(scrolls):
            if owner and since and reached_since(_page_posts(page, responses, from_responses, users), owner, since):
                break  # 지난 브리핑 이후 글은 이미 다 받음
            page.mouse.wheel(0, random.randint(1500, 2300))
            page.wait_for_timeout(random.uniform(2500, 4500))
            done += 1
    finally:
        page.remove_listener("response", handler)

    posts = list({p["id"]: p for p in _page_posts(page, responses, from_responses, users)}.values())
    if posts:
        dump_raw("threads", via.replace(":", "_")[:40], posts[:50])
    for p in posts:
        p["via"] = via
    sink.extend(posts)
    log.info("Threads %s: 게시물 %d개 (스크롤 %d번)", via, len(posts), done)
    return len(posts)


def check_profiles(ctx, usernames, progress=lambda s: None, users=None):
    """발굴 후보 프로필을 한 번씩 열어 팔로워 수와 최근 글을 받는다 (태그 페이지엔 팔로워 수가 없음)."""
    page = ctx.new_page()
    out = []
    try:
        for i, u in enumerate(usernames):
            progress(f"Threads 후보 확인 {i + 1}/{len(usernames)}")
            _visit(page, f"https://www.threads.com/@{u}", f"th-check:{u}", 2, out, users=users)
            page.wait_for_timeout(random.uniform(4000, 8000))
    finally:
        page.close()
    return out


def collect(ctx, cfg, accounts, progress=lambda s: None, since=None, users=None):
    """since(UTC ISO): 계정 페이지는 이 시각 이전 글까지만 스크롤. 검색·태그는 시간순이 아니라 그대로.
    users(dict): 페이지에 실린 팔로워 수를 모아 둘 곳 (계정 페이지를 열 때 같이 온다)."""
    tcfg = cfg.get("threads", {})
    if not any(c["name"] == "sessionid" for c in ctx.cookies(["https://www.threads.com"])):
        raise NeedLogin("Threads 로그인이 필요해요. 트레이 메뉴 > 전용 Chrome 열기에서 로그인해 주세요.")
    scrolls = int(tcfg.get("scrolls_per_page", 3))
    targets = [(f"https://www.threads.com/@{a}", f"th-user:{a}") for a in accounts]
    targets += [(f"https://www.threads.com/search?q={quote(q)}&serp_type=default", f"th-search:{q}")
                for q in tcfg.get("searches", [])]
    targets += [(f"https://www.threads.com/search?q={quote(t)}&serp_type=tags", f"th-tag:{t}")
                for t in tcfg.get("tags", [])]
    page = ctx.new_page()
    out, empty = [], 0
    try:
        for i, (url, via) in enumerate(targets):
            progress(f"Threads 수집 중 {i + 1}/{len(targets)}")
            owner = via[8:] if via.startswith("th-user:") else None
            empty = empty + 1 if _visit(page, url, via, scrolls, out, owner, since, users) == 0 else 0
            if empty >= 3:
                raise RuntimeError("Threads에서 연속으로 게시물을 못 찾았어요. 페이지 구조가 바뀐 것 같아요.")
            page.wait_for_timeout(random.uniform(4000, 8000))
    finally:
        page.close()
    return out
