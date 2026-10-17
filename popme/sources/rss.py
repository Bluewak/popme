"""RSS/Atom 출처 — 로그인 없이 공식 변경내역·블로그·커뮤니티 새 글을 가져온다."""
import gzip
import html
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) POPME/0.1"


def _clean(s, limit):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def fetch(feed):
    req = urllib.request.Request(feed["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":  # 묻지 않아도 gzip으로 주는 곳이 있다 (Google DeepMind 블로그 등) — urllib은 안 풀어 줌
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    limit = int(feed.get("max_chars", 500))
    base = {"source": feed["name"], "grp": feed.get("group", "")}
    skip = re.compile(feed["skip"], re.I) if feed.get("skip") else None  # 예: 매일 나오는 nightly 릴리스
    items = []
    for it in root.iter("item"):  # RSS 2.0
        link = (it.findtext("link") or "").strip()
        if link:
            items.append({**base, "url": link, "title": _clean(it.findtext("title"), 200),
                          "summary": _clean(it.findtext("description"), limit),
                          "published": _date(it.findtext("pubDate"))})
    for e in root.iter(f"{ATOM}entry"):  # Atom
        link_el = e.find(f"{ATOM}link[@rel='alternate']")
        if link_el is None:  # Element는 자식이 없으면 falsy라 `or`로 이으면 안 됨
            link_el = e.find(f"{ATOM}link")
        link = link_el.get("href") if link_el is not None else ""
        if link:
            body = e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary")
            items.append({**base, "url": link, "title": _clean(e.findtext(f"{ATOM}title"), 200),
                          "summary": _clean(body, limit),
                          "published": _date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"))})
    return [i for i in items if not (skip and skip.search(i["title"]))]


def describe(feed):
    return f"제목에 「{feed['skip']}」이 있으면 빼고 받아요" if feed.get("skip") else ""
