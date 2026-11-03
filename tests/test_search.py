"""질문용 전체 기록 검색 (popme/search.py)."""
from datetime import datetime, timedelta, timezone

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
    out, counts = search.find(make_db(tmp_path), ["MCP"])
    assert "simonw" in out and "kodev" in out  # 'MCP를'도 걸림
    assert "점심" not in out
    assert "GeekNews" in out and "[2026-09-21 브리핑] **MCP 표준 바뀜** 본문" in out
    assert counts == {"posts": 2, "items": 1, "briefing": 1, "events": 0}


def test_date_range_is_kst_days(tmp_path):
    out, _ = search.find(make_db(tmp_path), ["MCP"], "2026-09-01", "2026-09-30")
    assert "simonw" in out and "kodev" not in out  # 10월 글은 빠짐


def test_like_wildcards_are_literal(tmp_path):
    db = make_db(tmp_path)
    assert "GeekNews" in search.find(db, ["MCP_tools"])[0]
    assert search.find(db, ["50%x"])[0] == ""  # %가 와일드카드로 쓰이지 않음


def _posts(db, specs):
    db.upsert_tweets([{"id": i, "author": i, "text": "Claude 소식", "created_at": when, "likes": likes,
                       "via": "search:0", "kind": "post"} for i, (when, likes) in specs.items()])


def test_half_of_posts_go_to_recent_ones(tmp_path):
    """좋아요 많은 옛 글이 있어도 절반은 최근 30일 글 — 몇 달 전 인기 글이 최근 소식을 다 밀어내지 않게."""
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=200)).isoformat()
    new = (now - timedelta(days=3)).isoformat()
    _posts(db, {"o1": (old, 900), "o2": (old, 800), "o3": (old, 700), "n1": (new, 1), "n2": (new, 2), "n3": (new, 3)})
    out, counts = search.find(db, ["Claude"], limits=(4, 30, 20, 10))
    assert counts["posts"] == 4
    assert "@n3" in out and "@n2" in out and "@n1" not in out  # 최근 몫 2개는 최근 글 중 점수순
    assert "@o1" in out and "@o2" in out and "@o3" not in out  # 나머지는 지금 점수순


def test_recent_share_falls_back_when_no_recent_posts(tmp_path):
    db = DB(tmp_path / "t.db")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    _posts(db, {"o1": (old, 900), "o2": (old, 800), "o3": (old, 700)})
    assert search.find(db, ["Claude"], limits=(2, 30, 20, 10))[1]["posts"] == 2  # 최근 글이 없으면 옛 글로 채움


def test_recent_share_counts_from_period_end(tmp_path):
    """기간을 정한 질문(「9월에」)은 그 기간 끝에서 30일 — 지금 기준이면 최근 몫이 비어 버린다."""
    db = DB(tmp_path / "t.db")
    _posts(db, {"a": ("2026-06-10T03:00:00+00:00", 900), "b": ("2026-06-11T03:00:00+00:00", 800),
                "c": ("2026-09-20T03:00:00+00:00", 1)})
    out, _ = search.find(db, ["Claude"], "2026-06-01", "2026-09-30", limits=(2, 30, 20, 10))
    assert "@c" in out and "@a" in out and "@b" not in out


def test_popular_and_own_account_posts_beat_keyword_stuffing(tmp_path):
    """검색어를 여럿 담은 글보다 반응 큰 글·당사자 계정 글. 좋아요는 플랫폼 안에서 순위로 (Threads는 X보다 훨씬 적다)."""
    db = DB(tmp_path / "t.db")
    when = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    db.upsert_tweets([
        {"id": "1", "author": "spam", "text": "Claude 클로드 출시 출시", "created_at": when, "likes": 3, "via": "search:0", "kind": "post"},
        {"id": "2", "author": "ClaudeDevs", "text": "Introducing Opus", "created_at": when, "likes": 50, "via": "search:0", "kind": "post"},
        {"id": "3", "author": "fan", "text": "Claude 새 모델 좋다", "created_at": when, "likes": 900, "via": "search:0", "kind": "post"},
        {"id": "4", "author": "th", "text": "Claude 써봤다", "created_at": when, "likes": 40, "via": "th-tag:AI", "kind": "post",
         "platform": "threads", "link": "https://www.threads.com/@th/post/1"},
        {"id": "5", "author": "th2", "text": "Claude 별로", "created_at": when, "likes": 2, "via": "th-tag:AI", "kind": "post",
         "platform": "threads", "link": "https://www.threads.com/@th2/post/2"},
        {"id": "6", "author": "th3", "text": "Claude 후기", "created_at": when, "likes": 10, "via": "th-tag:AI", "kind": "post",
         "platform": "threads", "link": "https://www.threads.com/@th3/post/3"},
    ])
    out, _ = search.find(db, ["Claude", "클로드", "출시"], limits=(3, 30, 20, 10))
    assert "@ClaudeDevs" in out and "@fan" in out and "@th:" in out  # Threads 40개는 Threads 안에선 1등
    assert "@spam" not in out and "@th2" not in out


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
