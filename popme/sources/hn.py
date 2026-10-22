"""Hacker News 출처 — HN 검색(Algolia)을 직접 부른다. 키 없음.

예전엔 hnrss.org를 거쳤는데, hnrss는 Algolia를 대신 불러 주는 중간 서비스라 검색 한 번에 8~10초가 걸리고 붐비면 502를 냈다
(2026-10 매 수집 7곳씩 실패). 직접 부르면 0.6초. 글 모양은 hnrss와 같게 (주소 = 원문, 요약 = 원문·댓글 주소·점수) — 지난 글과 이어지게.

feeds.toml 한 줄: type = "hn", words = "Claude OpenAI …"(이 중 하나라도 — OR), points = 100(이 점수 이상),
front = true면 지금 첫 화면 글 중 points 이상.
"""
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://hn.algolia.com/api/v1/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) POPME/0.1"
ITEM = "https://news.ycombinator.com/item?id={}"


def params(feed):
    """feed → (주소, 검색 조건). 화면의 「주소」 칸에도 이 주소를 보인다."""
    points = int(feed.get("points", 100))
    if feed.get("front"):
        q = {"tags": "front_page", "hitsPerPage": 50}
        return API + "search?" + urllib.parse.urlencode(q), points
    words = " ".join(feed.get("words", "").split())
    q = {"query": words, "optionalWords": words,  # optionalWords에 전부 넣으면 「이 중 하나라도」(OR)
         "tags": "story", "numericFilters": f"points>={points}", "hitsPerPage": 20}
    return API + "search_by_date?" + urllib.parse.urlencode(q), points


def fetch(feed, conditional=True, timeout=20):
    url, points = params(feed)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        hits = json.loads(r.read().decode("utf-8")).get("hits", [])
    limit = int(feed.get("max_chars", 500))
    skip = re.compile(feed["skip"], re.I) if feed.get("skip") else None
    base = {"source": feed["name"], "grp": feed.get("group", "")}
    out = []
    for h in hits:
        title = " ".join((h.get("title") or "").split())
        if not title or (h.get("points") or 0) < points or (skip and skip.search(title)):
            continue
        link = ITEM.format(h["objectID"])
        text = re.sub(r"<[^>]+>", " ", html.unescape(h.get("story_text") or ""))
        summary = " ".join(f"{text} Article URL: {h.get('url') or link} Comments URL: {link} "
                           f"Points: {h.get('points') or 0} # Comments: {h.get('num_comments') or 0}".split())
        ts = h.get("created_at_i")
        out.append({**base, "url": h.get("url") or link, "title": title[:200], "summary": summary[:limit],
                    "published": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None})
    return out


def describe(feed):
    if feed.get("front"):
        return f"Hacker News 첫 화면 중 {feed.get('points', 100)}점 넘은 글"
    return f"Hacker News에서 「{' · '.join(feed.get('words', '').split())}」 중 하나가 들어간 {feed.get('points', 100)}점 넘은 글"
