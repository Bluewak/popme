"""X 수집: 전용 Chrome이 X 페이지를 열 때 받아오는 내부 GraphQL 응답(JSON)을 가로채 저장한다.

요청 헤더(x-client-transaction-id 등)는 브라우저가 직접 만들기 때문에 위조할 게 없다.
읽기 전용이며, 사람 속도로 몇 번만 스크롤한다.
"""
import json
import logging
import random
import re
from datetime import datetime, timezone
from urllib.parse import quote

from popme import chrome
from popme.collectors import VIA_X_CHECK, VIA_X_LIST, VIA_X_SEARCH, VIA_X_USER, NeedLogin, dump_raw, reached_since

log = logging.getLogger(__name__)

# X는 내부 API 이름을 자주 바꾼다 (예: UserTweets → UserOriginalsTimeline).
# 그래서 이름을 고정하지 않고 모든 GraphQL 응답에서 트윗을 찾되, 다른 사람 글이 섞이는 곁가지만 뺀다.
SKIP_OPS = ("Sidebar", "Explore", "Recommendation", "Trends", "Spotlights")
BLOCK_MARKERS = ("/login", "/i/flow/login", "/account/access", "/i/flow/consent")
# 돌아가며 보는 계정(since_by)은 며칠 치를 내려가야 해서 스크롤 상한을 따로. 5~6일에 한 번 여는 계정이라
# 하루로 나누면 매일 열어 1번 스크롤하던 때와 요청 수가 같다. 그래도 못 닿으면 로그를 남긴다
DEEP_SCROLLS = 6
# 리스트 한 번에 최대 스크롤. 한 번에 글 약 90개 — 계정 100개의 하루치가 5번 안팎 (2026-11 시험).
# 20번이던 때 멤버 163개의 하루치(1,500~2,400개)가 넘쳐 2026-11-24~26 내리 못 닿았다. 40번이면 이틀치쯤
LIST_SCROLLS = 40
LIST_DEEP = LIST_SCROLLS // 2  # 끝까지 못 갔어도 글 담긴 응답이 이만큼이면 「깊이 읽었다」 (2026-11-24 실제: 스크롤 20번에 응답 23개)


def list_id(url):
    """리스트 주소 → ID (x.com/i/lists/123…). 숫자만 줘도 된다. 모르는 모양이면 None."""
    m = re.search(r"(?:lists/)?(\d{6,})", url or "")
    return m.group(1) if m else None


def set_list_membership(page, handle, list_name, member=True):
    """계정 페이지 「…」 → Add/remove from Lists → list_name 줄 체크를 member에 맞춘다 → Save.
    → "added" | "already" (넣으려는데 이미 있음) | "removed" | "absent" (빼려는데 이미 없음). 사람이 하듯 한 번에 한 계정.
    실패하면 예외 (계정이 없어졌거나 화면이 바뀜)."""
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(random.uniform(3500, 5500))
    if any(m in page.url for m in BLOCK_MARKERS):
        raise NeedLogin(f"X 로그인이 필요해요 ({page.url})")
    page.locator("[data-testid=userActions]").first.click(timeout=15000)
    page.wait_for_timeout(random.uniform(900, 1600))
    page.get_by_role("menuitem").filter(has_text="List").first.click(timeout=15000)
    page.wait_for_timeout(random.uniform(2000, 3000))
    dlg = page.get_by_role("dialog")
    # 이름이 정확히 같은 줄 하나만. 「AI」로 찾으면 「AI research」도 걸려 엉뚱한 리스트를 바꿀 수 있다 (Codex 리뷰 #61)
    rows = dlg.locator("[data-testid=listCell]").filter(has=page.get_by_text(list_name, exact=True))
    n = rows.count()
    if n != 1:
        page.keyboard.press("Escape")
        raise RuntimeError(f"리스트 「{list_name}」 줄이 {n}개예요 (1개여야 함)")
    row = rows.first
    if (row.get_attribute("aria-checked", timeout=15000) == "true") == member:
        page.keyboard.press("Escape")
        return "already" if member else "absent"
    row.click()
    page.wait_for_timeout(random.uniform(800, 1400))
    dlg.get_by_role("button", name="Save").first.click(timeout=15000)
    page.wait_for_timeout(random.uniform(1500, 2500))
    return "added" if member else "removed"


def list_name(data, lid):
    """리스트 화면의 응답에서 리스트 이름 (set_list_membership이 대화상자에서 줄을 찾을 때). 없으면 None."""
    found = []

    def walk_(o):
        if isinstance(o, dict):
            if str(o.get("id_str") or o.get("rest_id") or "") == lid and o.get("name") and "member_count" in o:
                found.append(o["name"])
            for v in o.values():
                walk_(v)
        elif isinstance(o, list):
            for v in o:
                walk_(v)
    walk_(data)
    return found[0] if found else None


def timeline_entries(obj, out=None):
    """응답에서 타임라인에 직접 올라온 글의 created_at만 (리트윗이면 리트윗한 시각). walk는 인용·리트윗 원문까지 펼쳐서,
    최근 리트윗이 오래된 인용 글을 담고 있으면 펼친 글의 2/3가 오래된 글이 된다 — 그걸로 멈출지 정하면 새 글을 남기고 멈췄다."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        item = obj.get("itemContent")
        if isinstance(item, dict) and "tweet_results" in item:
            p = parse_tweet((item["tweet_results"] or {}).get("result") or {})
            if p and p["created_at"]:
                out.append(p["created_at"])
            return out
        for v in obj.values():
            timeline_entries(v, out)
    elif isinstance(obj, list):
        for v in obj:
            timeline_entries(v, out)
    return out


def _local(iso):
    """UTC ISO → 「11/23 09:32」 (내 컴퓨터 시각). 모르면 「?」."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%m/%d %H:%M")
    except (TypeError, ValueError):
        return "?"


def _list_reached(dates, since):
    """마지막 응답의 타임라인 글(timeline_entries)이 절반 넘게 since보다 오래됐으면 그 아래는 이미 받은 구간이다.
    하나가 아니라 절반: 고정글이나 늦게 퍼진 리트윗 하나로 멈추지 않게."""
    return bool(dates) and sum(1 for c in dates if c < since) * 2 > len(dates)


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


def _visit(page, url, via, scrolls, sink, ops_seen, owner=None, since=None, users=None, answered=None):
    """owner·since를 주면 (계정 페이지) 그 계정 글이 since 이전까지 내려왔을 때 스크롤을 멈춘다.
    users(dict)에는 응답에 실린 계정 규모를 모은다. answered(list): 계정 타임라인 응답을 정상으로 받았으면 True를 넣는다 (timeline_ok)
    (글이 0개여도 페이지는 정상으로 본 것 — 조용한 계정)."""
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
    if owner and since and done == scrolls and not reached_since(drain(), owner, since):
        log.info("X %s: 스크롤 %d번으로 %s까지 못 내려감 (그 사이 앞쪽 글은 빠짐)", via, scrolls, since[:16])
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
    if answered is not None and any(timeline_ok(r, data) for r, data, _ in parsed):
        answered.append(True)
    return got


def timeline_ok(r, data):
    """정상으로 받은 계정 타임라인 응답인가 (글이 0개여도). 오류 JSON(429 등의 {"errors": …})이나 다른 응답
    (UserByScreenName 같은 계정 정보)만 받았으면 아니다 — 그런 날을 「봤다」로 남기면 다음 수집이 그 사이 글을 건너뛴다."""
    if not isinstance(data, dict) or data.get("errors") or getattr(r, "status", 200) != 200:
        return False
    result = (((data.get("data") or {}).get("user") or {}).get("result") or {})
    return any(k in result for k in ("timeline", "timeline_v2"))


def check_profiles(ctx, handles, progress=lambda s: None, users=None):
    """발굴 후보 프로필을 한 번씩 열어 최근 글을 받는다 (꾸준함 판단용). 스크롤 2번, 사람 속도."""
    page = chrome.new_page(ctx)
    out, ops_seen = [], set()
    try:
        for i, h in enumerate(handles):
            progress(f"X 후보 확인 {i + 1}/{len(handles)}")
            _visit(page, f"https://x.com/{h}", f"{VIA_X_CHECK}{h}", 2, out, ops_seen, users=users)
            page.wait_for_timeout(random.uniform(4000, 9000))
    finally:
        page.close()
    return out


def _visit_list(page, url, via, since, sink, ops_seen, users=None, meta=None):
    """리스트 타임라인 → (받은 글 수, since까지 내려갔는가, 내려간 데까지의 시각 UTC ISO | None, 글이 담긴 응답 수).
    since(UTC ISO)까지 내려가면 멈춘다 (최대 LIST_SCROLLS번). 못 내려갔을 때 「봤다」로 남길지는 collect가 정한다
    (응답 수로 「깊이 읽었는데 글이 많았다」와 「더 불러와지지 않았다」를 가른다).
    리스트는 보는 계정 전체의 글을 시간순으로 한 페이지에 준다 — 계정 페이지를 30개씩 돌아가며 열면 한 바퀴에 5일쯤 걸려,
    그 사이 글의 56%가 어느 브리핑에도 못 들어갔다(2026-11-04~18). 계정 페이지가 안 주는 리트윗도 온다."""
    responses, parsed, tops = [], [], []  # tops: 응답마다 타임라인 글의 시각

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
                tops.append(timeline_entries(data))
                if meta is not None and "name" not in meta:
                    name = list_name(data, via.split(":", 1)[-1])
                    if name:
                        meta["name"] = name
            parsed.append((r, data, tweets))

    page.on("response", handler)
    done = 0
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(random.uniform(3000, 5000))
        if any(m in page.url for m in BLOCK_MARKERS):
            raise NeedLogin(f"X 로그인이 필요해요 ({page.url})")
        for _ in range(LIST_SCROLLS):
            drain()
            last = next((d for d in reversed(tops) if d), [])
            if since and _list_reached(last, since):
                break
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(random.uniform(2500, 5000))
            done += 1
    finally:
        page.remove_listener("response", handler)
    drain()
    last = sorted(next((d for d in reversed(tops) if d), []))
    reached = last[len(last) // 2] if last else None  # 마지막 응답의 가운데 시각 (고정글·늦게 퍼진 리트윗 하나에 안 끌리게)
    complete = not since or _list_reached(last, since)
    if not complete:
        log.info("X 리스트: 스크롤 %d번으로 %s까지 못 내려감 (%s까지 봄)", done, since[:16], (reached or "?")[:16])
    got = 0
    for r, data, tweets in parsed:
        if not tweets:
            continue
        dump_raw("x", f"{via}_{_op(r.url)}".replace(":", "_").replace("/", "_")[:60], data)
        for t in tweets:
            t["via"] = via
        sink.extend(tweets)
        got += len(tweets)
    log.info("X 리스트: 트윗 %d개 (GraphQL 응답 %d개, 스크롤 %d번)", got, len(responses), done)
    return got, complete, reached, sum(1 for d in tops if d)


def collect(ctx, cfg, accounts, progress=lambda s: None, since=None, users=None, on_batch=None, since_by=None,
            visited=None, list_since=None, list_meta=None, gaps=None):
    """accounts: 핵심 계정 handle 목록. since(UTC ISO): 계정 페이지는 이 시각 이전 글까지만 스크롤.
    since_by({handle 소문자: UTC ISO}): 그 계정만 다른 시각까지, 스크롤도 DEEP_SCROLLS번까지 (돌아가며 보는 계정 — 지난번에 본 시각).
    [x] list_url이 있으면 계정 페이지를 다 본 뒤 리스트도 본다 (list_since까지). 같은 글은 처음 받은 경로(via)가 남으므로
    맨 마지막에 봐야 날마다 여는 계정의 글이 계정 페이지 경로로 남는다 — 리스트로 옮기기 전 나란히 견줘 보는 기간.
    리스트에서 글을 받았으면 visited에 "list:<ID>"를 넣는다. list_since까지 못 내려갔으면 gaps(list)에 사람 말로 남긴다.
    list_meta(dict)에는 리스트 이름을 담는다 (동기화용).
    visited(list): 정상으로 연 계정 페이지(글이 0개여도)의 handle을 차례로 넣는다 (중간에 실패해도 그때까지 본 것).
    users(dict): 응답에 실린 계정 규모(팔로워 등)를 모아 둘 곳. 반환: 파싱된 트윗 리스트.
    on_batch(새 트윗들): 페이지 하나를 볼 때마다 — 중간에 끊겨도 모은 데까지 저장되게."""
    xcfg = cfg.get("x", {})
    scrolls = int(xcfg.get("scrolls_per_page", 3))
    if not any(c["name"] == "auth_token" for c in ctx.cookies("https://x.com")):
        raise NeedLogin("X 로그인이 필요해요. 트레이 메뉴 > 전용 Chrome 열기에서 로그인해 주세요.")
    page = chrome.new_page(ctx)
    out, empty_streak, ops_seen = [], 0, set()
    try:
        targets = []
        handles = list(accounts)
        random.shuffle(handles)
        targets += [(f"https://x.com/{h}", f"{VIA_X_USER}{h}") for h in handles]
        targets += [(f"https://x.com/search?q={quote(q)}&src=typed_query&f=top", f"{VIA_X_SEARCH}{i}")
                    for i, q in enumerate(xcfg.get("discovery_searches", []))]

        for i, (url, via) in enumerate(targets):
            progress(f"X 수집 중 {i + 1}/{len(targets)}")
            owner = via[len(VIA_X_USER):] if via.startswith(VIA_X_USER) else None  # 검색 결과는 시간순이 아니라 멈추지 않음
            before = len(out)
            deep = bool(owner and owner.lower() in (since_by or {}))
            stop = since_by[owner.lower()] if deep else since
            answered = []
            got = _visit(page, url, via, max(scrolls, DEEP_SCROLLS) if deep else scrolls, out, ops_seen, owner, stop, users,
                         answered)
            # 본 것으로 남기기: 글을 받았거나, 글은 0개라도 X가 정상 응답한 페이지 (조용한 계정). 예전엔 글이 있어야만 남겨서
            # 조용한 계정이 매번 다시 뽑혀 돌아가며 보기 자리를 차지했다. 로그인 막힘·예외는 여기 오지 않는다
            if owner and (got or answered) and visited is not None:
                visited.append(owner)
            if on_batch and len(out) > before:
                on_batch(out[before:])
            empty_streak = empty_streak + 1 if got == 0 else 0
            if empty_streak >= 3:
                # 로그인은 됐는데 계속 비면 X 응답 구조가 바뀐 것. 더 두드리지 않고 단서를 남긴다
                log.warning("X 빈 응답 연속. 받은 GraphQL: %s", sorted(ops_seen))
                raise RuntimeError("X에서 연속으로 글을 못 찾았어요. X 응답 구조가 바뀐 것 같아요 "
                                   f"(로그에 받은 API 이름 기록: {', '.join(sorted(ops_seen))[:120]})")
            page.wait_for_timeout(random.uniform(4000, 9000))
        lid = list_id(xcfg.get("list_url"))
        if lid:
            progress("X 리스트 수집 중")
            before = len(out)
            start = list_since or since
            got, complete, reached, pages = _visit_list(page, f"https://x.com/i/lists/{lid}", f"{VIA_X_LIST}{lid}", start,
                                                        out, ops_seen, users, list_meta)
            # 끝까지 못 내려갔어도 깊이 읽었으면(글이 많았을 뿐) 본 것으로 남기고, 빠진 구간은 경고로 알린다.
            # 예전엔 남기지 않았다(Codex 리뷰 #61) — 그러면 다음 기준 시각이 그대로라 날마다 더 멀어져 다시는 닿지 못했고,
            # 48시간 뒤 리스트 계정 전부가 계정 페이지 돌아가며 보기로 돌아갔다 (2026-11-23~26: 152개, 한 바퀴 10일).
            # 더 불러와지지 않아 얕게 끝났으면(429·화면 구조 변경) 남기지 않는다 — 계속 그러면 48시간 뒤 계정 페이지로 돌아가게
            deep = pages >= LIST_DEEP
            if got and (complete or deep) and visited is not None:
                visited.append(f"{VIA_X_LIST}{lid}")
            if got and not complete and gaps is not None:
                gaps.append(f"X 리스트를 {_local(reached)}까지만 내려가서 {_local(start)}~그 사이 글은 빠졌어요" if deep else
                            f"X 리스트가 더 불러와지지 않아 {_local(reached)}까지만 받았어요 "
                            "(이틀 넘게 이러면 계정 페이지로 돌아가며 봐요)")
            if on_batch and len(out) > before:
                on_batch(out[before:])
    finally:
        page.close()
    return out


def sync_list(ctx, adds, removes, name, on_result, progress=lambda s: None):
    """리스트에 adds를 넣고 removes를 뺀다 (사람 속도, 계정 사이 6~12초).
    on_result(handle, set_list_membership 결과 | "failed")로 하나씩 알린다 — 중간에 끊겨도 한 데까지 남게.
    로그인이 풀렸으면 멈춘다 (NeedLogin을 그대로 올린다)."""
    work = [(h, True) for h in adds] + [(h, False) for h in removes]
    page = chrome.new_page(ctx)
    try:
        for i, (h, member) in enumerate(work):
            progress(f"X 리스트 정리 중 {i + 1}/{len(work)}")
            try:
                on_result(h, set_list_membership(page, h, name, member))
            except NeedLogin:
                raise
            except Exception as e:
                log.warning("X 리스트 @%s %s 실패: %s", h, "넣기" if member else "빼기", e)
                on_result(h, "failed")
            page.wait_for_timeout(random.uniform(6000, 12000))
    finally:
        page.close()
