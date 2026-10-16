"""RSS 고르기 (popme/collectors/rss.py, popme/briefing.py rss_items): 커뮤니티는 AI 글만, 묶음별 상한, 시험판 빼기."""
from datetime import datetime, timedelta, timezone

import pytest

from popme import briefing
from popme.collectors import rss
from popme.db import DB


@pytest.mark.parametrize("title, want", [
    ("Self-Evolving Agent Harness - 모델은 그대로, 하네스만 바꾸기", True),
    ("Show GN: livero: 48KiB RAM에서 도는 온디바이스 벡터 DB", True),
    ("Linus Torvalds's first LLM-assisted patch series", True),
    ("Introducing Grok 4.7", True),
    ("Muse Spark Open", True),
    ("AI가 코드 리뷰를 대신한다면", True),
    ("Claude Code로 사이드 프로젝트 만들기", True),
    ("에픽게임즈, 런쳐 재작성과 Fortnite 리눅스 포팅", False),
    ("Wayland desktop for Linux with skeuomorphic design", False),
    ("Haxy - 프로젝트 메타데이터를 Git 저장소에 보관하는 협업 플랫폼", False),  # 메타데이터
    ("요즘 드라마 참교육 영상", False),  # 드라마의 「라마」
    ("Parsing HTML and XML faster", False),  # html·xml의 ml
])
def test_is_ai(title, want):
    assert rss.is_ai({"title": title, "summary": ""}) is want


def test_skip_drops_nightly(monkeypatch):
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Release v0.66.0-nightly.20261016</title><link href="https://g/1"/><updated>2026-10-16T00:00:00Z</updated></entry>
      <entry><title>Release v0.65.0</title><link href="https://g/2"/><updated>2026-10-15T00:00:00Z</updated></entry>
    </feed>"""

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return atom
    monkeypatch.setattr(rss.urllib.request, "urlopen", lambda req, timeout=20: R())
    titles = [i["title"] for i in rss.fetch({"name": "Gemini CLI", "url": "https://example.com/releases.atom", "skip": "nightly|preview"})]
    assert titles == ["Release v0.65.0"]


def test_rss_items_filters_community_and_caps_groups(tmp_path, monkeypatch):
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(30):  # 커뮤니티 30개 중 AI 글은 짝수만
        rows.append({"url": f"https://gn/{i}", "source": "GeekNews", "grp": "한국 커뮤니티",
                     "title": f"Claude 활용 {i}" if i % 2 == 0 else f"게임 엔진 소식 {i}", "summary": "",
                     "published": (now - timedelta(minutes=i)).isoformat()})
    for i in range(50):  # 공식 50개 → 상한 40, 최신순
        rows.append({"url": f"https://rel/{i}", "source": "Claude Code 릴리스", "grp": "공식 변경내역",
                     "title": f"v{i}", "summary": "", "published": (now - timedelta(minutes=i)).isoformat()})
    rows.append({"url": "https://old", "source": "Claude Code 릴리스", "grp": "공식 변경내역", "title": "옛 글",
                 "summary": "", "published": (now - timedelta(days=10)).isoformat()})
    db.upsert_items(rows)
    cfg = {"rss": [{"name": "GeekNews", "topic": "ai"}, {"name": "Claude Code 릴리스"}]}
    got = briefing.rss_items(cfg, db, (now - timedelta(days=2)).isoformat())
    official = [r for r in got if r["grp"] == "공식 변경내역"]
    community = [r for r in got if r["grp"] == "한국 커뮤니티"]
    assert len(official) == briefing.RSS_CAPS["공식 변경내역"] and official[0]["title"] == "v0"  # 최신부터
    assert len(community) == 15 and all("Claude" in r["title"] for r in community)  # AI 글만
    assert got.index(official[0]) < got.index(community[0])  # 공식 출처가 먼저
    assert "옛 글" not in [r["title"] for r in got]


def test_rss_items_takes_new_date_only_posts(tmp_path):
    """날짜만 주는 피드(0시로 찍힘): 처음 본 글이면 이틀 안 글은 넣고, 예전에 본 글·오래된 글은 안 넣는다."""
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=12)
    day0 = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    db.upsert_items([
        {"url": "https://c/new", "source": "Claude 블로그", "grp": "공식 발표", "title": "어제 0시로 찍힌 새 글",
         "summary": "", "published": day0.isoformat()},
        {"url": "https://c/old", "source": "Claude 블로그", "grp": "공식 발표", "title": "열흘 전 글",
         "summary": "", "published": (now - timedelta(days=10)).isoformat()},
    ])
    with db.conn() as c:  # 예전에 본 글: 처음 본 시각이 구간 전
        c.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?)", ("https://c/seen", "Claude 블로그", "공식 발표",
                  "지난번에 본 글", "", day0.isoformat(), (since - timedelta(hours=1)).isoformat()))
    titles = [r["title"] for r in briefing.rss_items({"rss": []}, db, since.isoformat())]
    assert titles == ["어제 0시로 찍힌 새 글"]


def test_rss_items_skips_saved_prereleases(tmp_path):
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc).isoformat()
    db.upsert_items([{"url": f"https://codex/{t}", "source": "Codex 릴리스", "grp": "공식 변경내역", "title": t,
                      "summary": "", "published": now} for t in ("0.163.0-alpha.10.2", "0.163.0")])
    cfg = {"rss": [{"name": "Codex 릴리스", "skip": "alpha|beta|-rc"}]}
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert [r["title"] for r in briefing.rss_items(cfg, db, since)] == ["0.163.0"]


def test_rss_items_future_dated_post_only_once(tmp_path):
    """앞날짜로 찍힌 글(Databricks 변경내역 등)은 처음 본 날 한 번만 — 날짜가 될 때까지 매일 다시 들어가지 않는다."""
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    db.upsert_items([{"url": "https://dbx/1", "source": "Databricks", "grp": "공식 발표", "title": "앞날짜 글",
                      "summary": "", "published": (now + timedelta(days=4)).isoformat()}])
    seen_in = [d for d in range(4)
               if briefing.rss_items({"rss": []}, db, (now + timedelta(days=d) - timedelta(hours=18)).isoformat())]
    assert seen_in == [0]


def test_fetch_reads_gzip_body(monkeypatch):
    """묻지 않아도 gzip으로 주는 피드(Google DeepMind 블로그 등)도 읽는다."""
    import gzip
    body = gzip.compress(b'<rss><channel><item><title>Gemma 4</title><link>https://g/1</link></item></channel></rss>')

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return body
    monkeypatch.setattr(rss.urllib.request, "urlopen", lambda req, timeout=20: R())
    assert [i["title"] for i in rss.fetch({"name": "DeepMind", "url": "https://example.com/rss.xml"})] == ["Gemma 4"]
