"""Threads 수집: 전용 Chrome에서 검색·토픽 태그·계정 페이지를 열고, 페이지에 실려 오는 게시물 데이터를 읽는다.

발굴 로직 (사용자 승인): 여러 검색어·태그에 반복해서 등장하고 반응(좋아요)이 큰 한국어 작성자를 후보로 올린다.
"""
import json
import logging
import random
import re
from datetime import datetime, timezone
from urllib.parse import quote

from popme.collectors import NeedLogin, dump_raw

log = logging.getLogger(__name__)
HANGUL = re.compile(r"[가-힣]")


def is_korean(text):
    return len(HANGUL.findall(text or "")) >= 5


def walk(o, out):
    if isinstance(o, dict):
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
            walk(v, out)
    elif isinstance(o, list):
        for v in o:
            walk(v, out)


def _visit(page, url, via, scrolls, sink):
    responses = []
    handler = lambda r: responses.append(r) if ("graphql" in r.url or "/api/" in r.url) else None
    page.on("response", handler)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(random.uniform(4000, 6000))
        if "/login" in page.url:
            raise NeedLogin("Threads 로그인이 필요해요. 트레이 메뉴 > 전용 Chrome 열기에서 로그인해 주세요.")
        for _ in range(scrolls):
            page.mouse.wheel(0, random.randint(1500, 2300))
            page.wait_for_timeout(random.uniform(2500, 4500))
    finally:
        page.remove_listener("response", handler)

    posts = []
    for r in responses:
        try:
            walk(r.json(), posts)
        except Exception:
            continue
    # 첫 화면 게시물은 XHR이 아니라 HTML 안 JSON으로 실려 온다
    for raw in page.locator('script[type="application/json"]').all_text_contents():
        if '"caption"' in raw:
            try:
                walk(json.loads(raw), posts)
            except ValueError:
                pass
    posts = list({p["id"]: p for p in posts}.values())
    if posts:
        dump_raw("threads", via.replace(":", "_")[:40], posts[:50])
    for p in posts:
        p["via"] = via
    sink.extend(posts)
    log.info("Threads %s: 게시물 %d개", via, len(posts))
    return len(posts)


def collect(ctx, cfg, accounts, progress=lambda s: None):
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
            empty = empty + 1 if _visit(page, url, via, scrolls, out) == 0 else 0
            if empty >= 3:
                raise RuntimeError("Threads에서 연속으로 게시물을 못 찾았어요. 페이지 구조가 바뀐 것 같아요.")
            page.wait_for_timeout(random.uniform(4000, 8000))
    finally:
        page.close()
    return out
