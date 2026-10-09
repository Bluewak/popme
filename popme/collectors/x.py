"""X 수집: 전용 Chrome이 X 페이지를 열 때 받아오는 내부 GraphQL 응답(JSON)을 가로채 저장한다.

요청 헤더(x-client-transaction-id 등)는 브라우저가 직접 만들기 때문에 위조할 게 없다.
읽기 전용이며, 사람 속도로 몇 번만 스크롤한다.
"""
import json
import logging
import random
from datetime import datetime, timezone
from urllib.parse import quote

from popme.collectors import NeedLogin, dump_raw, reached_since

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


def parse_user(u):
    """User 객체 → 계정 규모. 예전 응답 구조(legacy.followers_count)도 읽는다."""
    core, leg = u.get("core") or {}, u.get("legacy") or {}
    rel, cnt = u.get("relationship_counts") or {}, u.get("tweet_counts") or {}
    handle = core.get("screen_name") or leg.get("screen_name")
    followers = rel.get("followers", leg.get("followers_count"))
    if not handle or not isinstance(followers, int):
        return None
    # 소속 배지 (회사 직원 계정 옆 회사 로고): 링크가 회사 계정을 가리킨다
    label = (u.get("affiliates_highlighted_label") or {}).get("label") or {}
    org_url = (label.get("url") or {}).get("url") or ""
    return {"platform": "x", "handle": handle, "name": core.get("name") or leg.get("name"),
            "followers": followers, "following": rel.get("following", leg.get("friends_count")),
            "posts": cnt.get("tweets", leg.get("statuses_count")),
            "verified": (u.get("verification") or {}).get("verified_type") or ("blue" if u.get("is_blue_verified") else ""),
            "org": org_url.rstrip("/").rsplit("/", 1)[-1] or None if org_url else None,
            "lang": u.get("profile_description_language")}


def walk(obj, out, users=None):
    """응답 구조가 조금 바뀌어도 버티도록, 트리 전체에서 Tweet 객체를 찾는다 (리트윗·인용 원문 포함).
    users(dict)를 주면 작성자 계정 규모도 모은다."""
    if isinstance(obj, dict):
        if obj.get("__typename") in ("Tweet", "TweetWithVisibilityResults"):
            p = parse_tweet(obj)
            if p and p["id"] and p["author"]:
                out.append(p)
        elif users is not None and obj.get("__typename") == "User":
            u = parse_user(obj)
            if u:
                users[u["handle"].lower()] = u
        for v in obj.values():
            walk(v, out, users)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out, users)


def _op(url):
    return url.split("?")[0].rsplit("/", 1)[-1]


def _visit(page, url, via, scrolls, sink, ops_seen, owner=None, since=None, users=None):
    """owner·since를 주면 (계정 페이지) 그 계정 글이 since 이전까지 내려왔을 때 스크롤을 멈춘다.
    users(dict)에는 응답에 실린 계정 규모를 모은다."""
    responses, parsed = [], []  # parsed: (응답 원본, 트윗들) — 받은 응답을 차례로 풀어 둔다

    def handler(r):
        if "/i/api/graphql/" in r.url and not any(s in _op(r.url) for s in SKIP_OPS):
            responses.append(r)

    def drain():
        while len(parsed) < len(responses):
            r = responses[len(parsed)]
            ops_seen.add(_op(r.url))
            try:
                data = r.json()
            except Exception:
                data = None
            tweets = []
            if data is not None:
                walk(data, tweets, users)
            parsed.append((r, data, tweets))
        return [t for _, _, ts in parsed for t in ts]

    page.on("response", handler)
    done = 0
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(random.uniform(3000, 5000))
        if any(m in page.url for m in BLOCK_MARKERS):
            raise NeedLogin(f"X 로그인이 필요해요 ({page.url})")
        for _ in range(scrolls):
            if owner and since and reached_since(drain(), owner, since):
                break  # 지난 브리핑 이후 글은 이미 다 받음
            page.mouse.wheel(0, random.randint(1400, 2400))
            page.wait_for_timeout(random.uniform(2500, 5000))
            done += 1
    finally:
        page.remove_listener("response", handler)
    drain()

    got = 0
    for r, data, tweets in parsed:
        if not tweets:
            continue
        dump_raw("x", f"{via}_{_op(r.url)}".replace(":", "_").replace("/", "_")[:60], data)
        for t in tweets:
            t["via"] = via
        sink.extend(tweets)
        got += len(tweets)
    log.info("X %s: 트윗 %d개 (GraphQL 응답 %d개, 스크롤 %d번)", via, got, len(responses), done)
    return got


def check_profiles(ctx, handles, progress=lambda s: None, users=None):
    """발굴 후보 프로필을 한 번씩 열어 최근 글을 받는다 (꾸준함 판단용). 스크롤 2번, 사람 속도."""
    page = ctx.new_page()
    out, ops_seen = [], set()
    try:
        for i, h in enumerate(handles):
            progress(f"X 후보 확인 {i + 1}/{len(handles)}")
            _visit(page, f"https://x.com/{h}", f"check:{h}", 2, out, ops_seen, users=users)
            page.wait_for_timeout(random.uniform(4000, 9000))
    finally:
        page.close()
    return out


def collect(ctx, cfg, accounts, progress=lambda s: None, since=None, users=None):
    """accounts: 핵심 계정 handle 목록. since(UTC ISO): 계정 페이지는 이 시각 이전 글까지만 스크롤.
    users(dict): 응답에 실린 계정 규모(팔로워 등)를 모아 둘 곳. 반환: 파싱된 트윗 리스트."""
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
            owner = via[5:] if via.startswith("user:") else None  # 검색 결과는 시간순이 아니라 멈추지 않음
            got = _visit(page, url, via, scrolls, out, ops_seen, owner, since, users)
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
