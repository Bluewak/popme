"""RSS/Atom 출처 — 로그인 없이 공식 변경내역·블로그·커뮤니티 새 글을 가져온다."""
import gzip
import html
import json
import logging
import re
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from popme import config

log = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) POPME/0.1"

# 조건부 요청: 지난번 응답의 ETag·Last-Modified를 붙여 물으면, 바뀐 게 없을 때 서버가 본문 없이 304만 준다.
# GitHub(릴리스·미러)·hnrss가 지원 — 출처가 100곳을 넘어도 요청 부담·내려받는 양이 작고, GitHub의 비로그인 제한에도 덜 걸린다.
# 오래된 값(VALID_DAYS)은 쓰지 않는다: DB를 지웠는데 304만 받아 글이 안 들어오는 일이 없게
CACHE_PATH = config.DATA_DIR / "feed-cache.json"
VALID_DAYS = 3
_cache, _lock = None, threading.Lock()


def _load_unlocked():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _load():
    with _lock:
        return _load_unlocked()


def _validators(url):
    c = _load().get(url) or {}
    fresh = c.get("at", "") >= (datetime.now(timezone.utc) - timedelta(days=VALID_DAYS)).isoformat()
    h = {}
    if fresh and c.get("etag"):
        h["If-None-Match"] = c["etag"]
    if fresh and c.get("modified"):
        h["If-Modified-Since"] = c["modified"]
    return h


def _remember(url, headers):
    etag, modified = headers.get("ETag"), headers.get("Last-Modified")
    with _lock:
        if etag or modified:
            _load_unlocked()[url] = {"etag": etag, "modified": modified, "at": datetime.now(timezone.utc).isoformat()}
        else:
            _load_unlocked().pop(url, None)


def flush():
    """받기가 끝나면 한 번 저장 (sources.collect가 부른다)."""
    with _lock:
        if _cache is not None:
            CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


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
    """새 글 목록. 지난번과 바뀐 게 없으면(304) [] — 그때 받은 글은 이미 DB에 있다."""
    url = feed["url"]
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **_validators(url)})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data, headers = r.read(), r.headers
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return []
        raise
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
    _remember(url, headers)  # 읽어서 저장까지 된 다음에만 (중간에 깨지면 다음에 처음부터 다시 받게)
    return [i for i in items if not (skip and skip.search(i["title"]))]


def describe(feed):
    return f"제목에 「{feed['skip']}」이 있으면 빼고 받아요" if feed.get("skip") else ""
