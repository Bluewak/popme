"""RSS/Atom 수집 — 로그인 없이 공식 변경내역·블로그·커뮤니티 새 글을 가져온다."""
import html
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
# 일반 개발 커뮤니티(GeekNews·HN 인기)에서 AI 관련 글만 브리핑에 넘길 때 (피드 topic = "ai", popme/briefing.py).
# 제목 + 요약 앞부분에서 찾는다. 「개발」「모델」「API」처럼 넓은 말은 넣지 않는다 — 커뮤니티 글 대부분에 걸린다.
# 짧은 영문 약어는 앞뒤가 영문자가 아닐 때만 (html의 ml, said의 ai는 안 걸리고 「AI가」는 걸리게)
AI_TOPIC = re.compile(
    r"(?<![A-Za-z])(AI|A\.I\.|AGI|LLMs?|GPT|ML|RAG|MCP|SLM|xAI)(?![A-Za-z])|Muse Spark|"
    r"ChatGPT|OpenAI|Anthropic|Claude|Gemini|DeepMind|Llama|Mistral|Qwen|DeepSeek|Grok|Copilot|Codex|Cursor|"
    r"Hugging ?Face|agent|language model|reasoning model|open.?weights?|inference|fine.?tun|prompt|embedding|"
    r"vector (db|database|search)|transformer|neural|diffusion|machine learning|deep learning|"
    r"인공지능|에이전트|에이젼트|언어 ?모델|생성형|프롬프트|바이브 ?코딩|머신 ?러닝|딥 ?러닝|클로드|제미나이|코덱스|챗GPT|"
    r"오픈AI|앤트로픽|하네스|추론 ?모델|임베딩|벡터 ?(DB|검색)|파인 ?튜닝|멀티모달", re.I)


def is_ai(item):
    return bool(AI_TOPIC.search(f"{item.get('title') or ''} {(item.get('summary') or '')[:300]}"))
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
        root = ET.fromstring(r.read())
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


def collect(cfg, progress=lambda s: None):
    out, errors = [], []
    for feed in cfg.get("rss", []):
        progress(f"RSS: {feed['name']}")
        try:
            out += fetch(feed)
        except Exception as e:
            log.warning("RSS 실패 %s: %s", feed["name"], e)
            errors.append(f"{feed['name']}: {e}")
    return out, errors
