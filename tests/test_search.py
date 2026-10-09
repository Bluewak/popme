"""질문용 전체 기록 검색 (popme/search.py)."""
from popme import briefing, search
from popme.db import DB


def make_db(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_tweets([
        {"id": "1", "author": "simonw", "text": "New MCP server for Claude Code", "created_at": "2026-09-10T03:00:00+00:00",
         "likes": 500, "via": "user:simonw", "kind": "post"},
        {"id": "2", "author": "kodev", "text": "MCP를 써보니 편하다", "created_at": "2026-10-08T03:00:00+00:00",
         "likes": 5, "via": "search:0", "kind": "post"},
        {"id": "3", "author": "food", "text": "점심 맛집", "created_at": "2026-09-12T03:00:00+00:00",
         "likes": 900, "via": "search:0", "kind": "post"},
    ])
    db.upsert_items([{"url": "https://x.dev/a", "source": "GeekNews", "grp": "커뮤니티", "title": "50% off MCP_tools",
                      "summary": "s", "published": "2026-09-20T00:00:00+00:00"}])
    db.save_briefing("2026-09-21", "# 브리핑\n- **MCP 표준 바뀜** 본문\n- 다른 소식\n")
    return db


def test_finds_across_tables_with_korean_particles(tmp_path):
    out = search.find(make_db(tmp_path), ["MCP"])
    assert "simonw" in out and "kodev" in out  # 'MCP를'도 걸림
    assert "점심" not in out
    assert "GeekNews" in out and "[2026-09-21 브리핑] **MCP 표준 바뀜** 본문" in out


def test_date_range_is_kst_days(tmp_path):
    out = search.find(make_db(tmp_path), ["MCP"], "2026-09-01", "2026-09-30")
    assert "simonw" in out and "kodev" not in out  # 10월 글은 빠짐


def test_like_wildcards_are_literal(tmp_path):
    db = make_db(tmp_path)
    assert "GeekNews" in search.find(db, ["MCP_tools"])
    assert search.find(db, ["50%x"]) == ""  # %가 와일드카드로 쓰이지 않음


def test_fallback_keywords_strip_particles():
    assert search.fallback_keywords("지난달에 누가 MCP를 얘기했지?") == ["지난달", "MCP"]


def test_plan_search_falls_back_when_llm_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no claude")
    monkeypatch.setattr(briefing.llm, "ask", boom)
    assert briefing.plan_search({}, "클로드코드 소식 알려줘") == (["클로드코드"], None, None)


def test_plan_search_validates_llm_output(monkeypatch):
    monkeypatch.setattr(briefing.llm, "ask", lambda *a, **k:
                        '```json\n{"keywords": ["MCP", "엠씨피"], "since": "2026-09-01", "until": "지난달"}\n```')
    assert briefing.plan_search({}, "q") == (["MCP", "엠씨피"], "2026-09-01", None)
