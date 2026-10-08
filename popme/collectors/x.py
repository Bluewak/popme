"""X 수집: 전용 Chrome이 X 페이지를 열 때 받아오는 내부 GraphQL 응답(JSON)을 가로채 저장한다.

요청 헤더(x-client-transaction-id 등)는 브라우저가 직접 만들기 때문에 위조할 게 없다.
읽기 전용이며, 사람 속도로 몇 번만 스크롤한다.
"""
import json
import logging
import random
from datetime import datetime, timezone
from urllib.parse import quote

from popme.collectors import NeedLogin, dump_raw

log = logging.getLogger(__name__)

# X는 내부 API 이름을 자주 바꾼다 (예: UserTweets → UserOriginalsTimeline).
# 그래서 이름을 고정하지 않고 모든 GraphQL 응답에서 트윗을 찾되, 다른 사람 글이 섞이는 곁가지만 뺀다.
SKIP_OPS = ("Sidebar", "Explore", "Recommendation", "Trends", "Spotlights")
BLOCK_MARKERS = ("/login", "/i/flow/login", "/account/access", "/i/flow/consent")


def _unwrap(t):
    if t.get("__typename") == "TweetWithVisibilityResults":
        return t.get("tweet") or {}
    return t


def _user(t):
    u = ((t.get("core") or {}).get("user_results") or {}).get("result") or {}
    core, leg = u.get("core") or {}, u.get("legacy") or {}
    return core.get("screen_name") or leg.get("screen_name"), core.get("name") or leg.get("name")


def parse_tweet(t):
    t = _unwrap(t)
    leg = t.get("legacy")
    if not leg or "full_text" not in leg:
        return None
    handle, name = _user(t)
    note = (((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}).get("text")

    kind, ref_author, ref_id = "post", None, None
    rt = (leg.get("retweeted_status_result") or {}).get("result")
    quoted = (t.get("quoted_status_result") or {}).get("result")
    if rt:
        rt = _unwrap(rt)
        kind, ref_author, ref_id = "retweet", _user(rt)[0], rt.get("rest_id")
    elif quoted:
        quoted = _unwrap(quoted)
        kind, ref_author, ref_id = "quote", _user(quoted)[0], quoted.get("rest_id")
    elif leg.get("in_reply_to_screen_name"):
        kind, ref_author, ref_id = "reply", leg["in_reply_to_screen_name"], leg.get("in_reply_to_status_id_str")

    try:
        created = datetime.strptime(leg["created_at"], "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc).isoformat()
    except (KeyError, ValueError):
        created = None
    urls = [u["expanded_url"] for u in (leg.get("entities") or {}).get("urls", []) if u.get("expanded_url")]
    views = (t.get("views") or {}).get("count")
    return {
        "id": t.get("rest_id") or leg.get("id_str"),
        "author": handle, "author_name": name,
        "text": note or leg["full_text"],
        "created_at": created, "lang": leg.get("lang"),
        "likes": leg.get("favorite_count", 0), "retweets": leg.get("retweet_count", 0),
        "replies": leg.get("reply_count", 0), "quotes": leg.get("quote_count", 0),
        "views": int(views) if views and str(views).isdigit() else None,
        "urls": json.dumps(urls), "kind": kind, "ref_author": ref_author, "ref_id": ref_id,
    }


def walk(obj, out):
    """응답 구조가 조금 바뀌어도 버티도록, 트리 전체에서 Tweet 객체를 찾는다 (리트윗·인용 원문 포함)."""
    if isinstance(obj, dict):
        if obj.get("__typename") in ("Tweet", "TweetWithVisibilityResults"):
            p = parse_tweet(obj)
            if p and p["id"] and p["author"]:
                out.append(p)
        for v in obj.values():
            walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out)


def _op(url):
    return url.split("?")[0].rsplit("/", 1)[-1]


def _visit(page, url, via, scrolls, sink, ops_seen):
    responses = []

    def handler(r):
        if "/i/api/graphql/" in r.url and not any(s in _op(r.url) for s in SKIP_OPS):
            responses.append(r)

    page.on("response", handler)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(random.uniform(3000, 5000))
        if any(m in page.url for m in BLOCK_MARKERS):
            raise NeedLogin(f"X 로그인이 필요해요 ({page.url})")
        for _ in range(scrolls):
            page.mouse.wheel(0, random.randint(1400, 2400))
            page.wait_for_timeout(random.uniform(2500, 5000))
    finally:
        page.remove_listener("response", handler)

    got = 0
    for r in responses:
        ops_seen.add(_op(r.url))
        try:
            data = r.json()
        except Exception:
            continue
        tweets = []
        walk(data, tweets)
        if not tweets:
            continue
        dump_raw("x", f"{via}_{_op(r.url)}".replace(":", "_").replace("/", "_")[:60], data)
        for t in tweets:
            t["via"] = via
        sink.extend(tweets)
        got += len(tweets)
    log.info("X %s: 트윗 %d개 (GraphQL 응답 %d개)", via, got, len(responses))
    return got


def collect(ctx, cfg, accounts, progress=lambda s: None):
    """accounts: 핵심 계정 handle 목록. 반환: 파싱된 트윗 리스트."""
    xcfg = cfg.get("x", {})
    scrolls = int(xcfg.get("scrolls_per_page", 3))
    if not any(c["name"] == "auth_token" for c in ctx.cookies("https://x.com")):
        raise NeedLogin("X 로그인이 필요해요. 트레이 메뉴 > 전용 Chrome 열기에서 로그인해 주세요.")
    page = ctx.new_page()
    out, empty_streak, ops_seen = [], 0, set()
    try:
        targets = []
        if xcfg.get("list_url"):
            targets.append((xcfg["list_url"], "list"))
        else:
            handles = list(accounts)
            random.shuffle(handles)
            targets += [(f"https://x.com/{h}", f"user:{h}") for h in handles]
        targets += [(f"https://x.com/search?q={quote(q)}&src=typed_query&f=top", f"search:{i}")
                    for i, q in enumerate(xcfg.get("discovery_searches", []))]

        for i, (url, via) in enumerate(targets):
            progress(f"X 수집 중 {i + 1}/{len(targets)}")
            got = _visit(page, url, via, scrolls, out, ops_seen)
            empty_streak = empty_streak + 1 if got == 0 else 0
            if empty_streak >= 3:
                # 로그인은 됐는데 계속 비면 X 응답 구조가 바뀐 것. 더 두드리지 않고 단서를 남긴다
                log.warning("X 빈 응답 연속. 받은 GraphQL: %s", sorted(ops_seen))
                raise RuntimeError("X에서 연속으로 글을 못 찾았어요. X 응답 구조가 바뀐 것 같아요 "
                                   f"(로그에 받은 API 이름 기록: {', '.join(sorted(ops_seen))[:120]})")
            page.wait_for_timeout(random.uniform(4000, 9000))
    finally:
        page.close()
    return out
