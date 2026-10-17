"""출처 모듈 (popme/sources): 서버가 다르면 동시에, 같은 서버는 차례로, 하나가 실패해도 나머지는 받는다."""
import threading
import time

import pytest

from popme import sources, topics


def test_collect_parallel_by_host(monkeypatch):
    running = {}            # 호스트 → 지금 받는 중인 수
    overlap = []            # 같은 호스트를 동시에 받은 적이 있나
    lock = threading.Lock()

    def fake_fetch(feed):
        host = feed["url"].split("/")[2]
        with lock:
            running[host] = running.get(host, 0) + 1
            overlap.append(running[host] > 1)
        time.sleep(0.05)
        with lock:
            running[host] -= 1
        if feed["name"] == "고장":
            raise OSError("HTTP Error 502")
        return [{"source": feed["name"], "grp": "", "url": feed["url"], "title": feed["name"], "summary": "", "published": None}]
    monkeypatch.setattr(sources.rss, "fetch", fake_fetch)
    feeds = [{"name": f"hn{i}", "url": f"https://hnrss.org/q{i}"} for i in range(3)] + \
            [{"name": "고장", "url": "https://hnrss.org/bad"}, {"name": "블로그", "url": "https://blog.example.com/feed"}]
    seen = []
    items, errors = sources.collect(feeds, seen.append)
    assert sorted(i["source"] for i in items) == ["hn0", "hn1", "hn2", "블로그"]
    assert errors == ["고장: HTTP Error 502"]
    assert not any(overlap)                          # 같은 서버(hnrss)는 한 번에 하나씩
    assert seen[-1] == "소식 받는 중 5/5"


def test_unknown_type_is_an_error():
    with pytest.raises(KeyError):
        sources.module({"type": "없는종류"})


@pytest.mark.parametrize("title, topic, want", [
    ("GPT-4o 2027년 1월 단종 예고", "ent", True),
    ("Bedrock에 Claude 새 모델, 서울 리전 지원", "ent", True),
    ("AI 기본법 시행령 고영향 AI 기준 공개", "ent", True),
    ("CVE-2026-12345: LiteLLM 키 유출", "ent", True),
    ("HyperCLOVA X 새 버전 공개", "ent", True),
    ("믿음에 관한 종교학 글", "ent", False),          # 믿:음(Mi:dm)만
    ("메모리 반도체 가격 정책 변화", "ent", False),      # 가격은 API 요금일 때만
    ("Qdrant 1.16: 하이브리드 검색 GA", "rag", True),
    ("Docling으로 HWP 문서 파싱하기", "rag", True),
    ("리랭커로 RAG 정확도 올린 실측", "rag", True),
    ("Voyage rerank-3 공개", "rag", True),
    ("PS5 탈옥 성공", "rag", False),
    ("Claude Code 새 기능", "rag", False),             # 일반 AI 소식은 "ai"
])
def test_ent_rag_topics(title, topic, want):
    assert topics.matches({"title": title, "summary": ""}, topic) is want


def test_rss_asks_only_for_changes(monkeypatch):
    """두 번째부터는 ETag·Last-Modified를 붙여 묻고, 304(바뀐 것 없음)면 빈 목록 — 글은 이미 DB에 있다."""
    import json
    import urllib.error
    from popme.sources import rss
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>v1</title><link href="https://g/1"/></entry></feed>'
    seen = []

    class R:
        headers = {"ETag": 'W/"abc"', "Last-Modified": "Sat, 17 Oct 2026 15:17:32 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return atom

    def urlopen(req, timeout=20):
        seen.append(dict(req.header_items()))
        if req.get_header("If-none-match") == 'W/"abc"':
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        return R()

    monkeypatch.setattr(rss.urllib.request, "urlopen", urlopen)
    feed = {"name": "Codex 릴리스", "url": "https://github.com/openai/codex/releases.atom"}
    assert [i["title"] for i in rss.fetch(feed)] == ["v1"]
    assert rss.fetch(feed) == []
    assert seen[1]["If-modified-since"] == "Sat, 17 Oct 2026 15:17:32 GMT"
    rss.flush()
    assert json.loads(rss.CACHE_PATH.read_text(encoding="utf-8"))[feed["url"]]["etag"] == 'W/"abc"'
    # 오래된 값은 쓰지 않는다 (DB를 지웠어도 다시 받게)
    rss._cache[feed["url"]]["at"] = "2026-01-01T00:00:00+00:00"
    assert [i["title"] for i in rss.fetch(feed)] == ["v1"]


def test_rss_broken_body_is_not_remembered(monkeypatch):
    from popme.sources import rss

    class R:
        headers = {"ETag": '"x"'}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"<not xml"

    monkeypatch.setattr(rss.urllib.request, "urlopen", lambda req, timeout=20: R())
    with pytest.raises(Exception):
        rss.fetch({"name": "x", "url": "https://e/x"})
    assert "https://e/x" not in rss._load()
