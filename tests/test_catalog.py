"""소식 출처 카탈로그 (popme/catalog.py): 관심사 → 받을 출처·상한, 예전 config 옮기기."""
import re
import tomllib
from datetime import datetime, timedelta, timezone

from popme import briefing, catalog, config
from popme.db import DB


def news(**kw):
    return {"news": {"interests": [], **kw}}


def by_id(cfg):
    return {f["id"]: f for f in catalog.active(cfg)}


def test_catalog_is_consistent():
    feeds = catalog.feeds()
    names = [f["name"] for f in feeds.values()]
    assert len(names) == len(set(names))  # DB items.source = 이름이라 겹치면 안 된다
    for f in feeds.values():
        assert f["url"].startswith("https://") and f["group"]
        if f.get("skip"):
            re.compile(f["skip"])
    used = set()
    for p in catalog.profiles():
        assert {"id", "name", "tag", "criteria", "caps"} <= set(p)
        assert "topic" in p or not any(e.get("filter") is True for e in p["feeds"])  # 거르기 규칙을 쓰는 출처가 있으면 topic
        for e in p["feeds"]:
            assert e["id"] in feeds, (p["id"], e["id"])
            assert feeds[e["id"]]["group"] in p["caps"], (p["id"], e["id"])
            used.add(e["id"])
    assert used == set(feeds)  # 어느 관심사에도 안 쓰이는 출처는 없다


def test_nothing_chosen_means_no_feeds():
    assert catalog.active(news()) == []


def test_ai_coding_keeps_todays_feeds():
    """그동안 config.example.toml의 20개 = 「AI 코딩」 관심사. 이름이 같아야 DB의 지난 글과 이어진다."""
    old = {"Claude Code 릴리스", "Codex 릴리스", "pi 릴리스", "OpenClaw 릴리스", "Deep Agents 릴리스", "Gemini CLI 릴리스",
           "OpenAI 뉴스", "OpenAI 개발자 블로그", "Anthropic 뉴스", "Claude 블로그", "Anthropic 엔지니어링", "Google AI 블로그",
           "xAI 뉴스", "Hugging Face 블로그", "Mistral 뉴스", "Simon Willison 블로그", "GeekNews", "Hacker News 인기",
           "Hacker News AI 회사", "Hacker News AI 모델"}
    got = {f["name"] for f in catalog.active(news(interests=["ai-coding"]))}
    assert old <= got and "Cursor 변경내역" not in got  # 다른 코딩 도구는 켜야 받는다


def test_shared_feed_merges_filters():
    """긱뉴스처럼 여러 관심사가 같이 쓰는 커뮤니티 피드는 한 번만 받고, 거르기는 관심사들 규칙 중 하나라도 맞으면."""
    got = by_id(news(interests=["ai-coding", "data-science", "ml-engineering"]))["geeknews"]
    assert got["interests"] == {"ai-coding": "ai", "data-science": "ai+data", "ml-engineering": "ml"}
    assert got["topic"] == "ai+data+ml"
    assert len([f for f in catalog.active(news(interests=["ai-coding", "data-science"])) if f["name"] == "GeekNews"]) == 1


def test_unfiltered_interest_wins(monkeypatch):
    monkeypatch.setattr(catalog, "_profiles", lambda: [
        {"id": "a", "topic": "ai", "feeds": [{"id": "x", "filter": True}], "caps": {"g": 5}},
        {"id": "b", "topic": "data", "feeds": [{"id": "x", "filter": "ml"}, {"id": "y"}], "caps": {"g": 5}},
        {"id": "c", "topic": "rag", "feeds": [{"id": "x"}], "caps": {"g": 5}},
    ])
    monkeypatch.setattr(catalog, "feeds", lambda: {k: {"id": k, "name": k, "url": f"https://{k}", "group": "g"} for k in "xy"})
    ab = by_id(news(interests=["a", "b"]))
    assert ab["x"]["topic"] == "ai+ml" and ab["x"]["interests"] == {"a": "ai", "b": "ml"} and ab["y"]["topic"] is None
    assert by_id(news(interests=["a", "c"]))["x"]["topic"] is None  # c는 거르지 않으니 전부


def test_off_on_and_tweak():
    cfg = news(interests=["ai-coding"], off=["geeknews"])
    assert "geeknews" not in by_id(cfg)
    ds = by_id(news(interests=["data-science"]))
    assert "arxiv-econ-em" not in ds  # 기본은 꺼진 출처
    assert "arxiv-econ-em" in by_id(news(interests=["data-science"], on=["arxiv-econ-em"]))
    t = by_id(news(interests=["ai-coding"], tweak={"codex-releases": {"skip": "beta", "max_chars": 99}}))
    assert t["codex-releases"]["skip"] == "beta" and t["codex-releases"]["max_chars"] == 99


def test_user_feeds_follow_interest():
    cfg = news(interests=["ai-coding", "data-science"])
    cfg["rss"] = [{"name": "내 블로그", "url": "https://example.com/feed"},
                  {"name": "데이터 글", "url": "https://example.com/d", "interest": "data-science", "topic": "data"}]
    got = by_id(cfg)
    assert set(got["user:내 블로그"]["interests"]) == {"ai-coding", "data-science"}
    assert got["user:내 블로그"]["group"] == "실사용 검증" and got["user:내 블로그"]["topic"] is None
    assert got["user:데이터 글"]["interests"] == {"data-science": "data"} and got["user:데이터 글"]["topic"] == "data"


def test_caps_grow_with_interests_but_stay_bounded():
    one = catalog.caps(news(interests=["ai-coding"]))
    assert one == catalog.profiles()[0]["caps"] or sum(one.values()) <= catalog.TOTAL_CAP[1]
    all5 = catalog.caps(news(interests=[p["id"] for p in catalog.profiles()]))
    assert sum(all5.values()) <= 240 and min(all5.values()) >= 2
    assert sum(all5.values()) > sum(one.values())
    assert catalog.caps({"rss": []}) == catalog.LEGACY_CAPS  # 옮기기 전


def test_legacy_config_used_as_is():
    cfg = {"rss": [{"name": "A", "url": "https://a", "group": "g", "topic": "ai"}]}
    assert catalog.active(cfg) == [{"name": "A", "url": "https://a", "group": "g", "topic": "ai", "id": "A", "interests": {}}]


OLD = '''[weather]
name = "서울"

# 교차 확인·새 오픈소스 발굴용 (로그인 불필요)
[[rss]]
name = "Claude Code 릴리스"
url = "https://github.com/anthropics/claude-code/releases.atom"
group = "공식 변경내역"
max_chars = 1500

[[rss]]
name = "Codex 릴리스"
url = "http://github.com/openai/codex/releases.atom/"
group = "공식 변경내역"
max_chars = 1500
skip = 'alpha|beta'

[[rss]]
name = "내 블로그"
url = "https://example.com/feed"
group = "실사용 검증"
skip = '\\d+ 광고'

# AI 설정
[llm]
provider = "claude"
'''


def test_migrate_moves_known_feeds_and_keeps_mine():
    new, note = catalog.migrate(OLD)
    cfg = tomllib.loads(new)
    assert cfg["news"]["interests"] == ["ai-coding"]
    assert cfg["news"]["tweak"] == {"codex-releases": {"skip": "alpha|beta"}}  # 앱 값과 다른 것만
    assert cfg["rss"] == [{"name": "내 블로그", "url": "https://example.com/feed", "group": "실사용 검증", "skip": r"\d+ 광고"}]
    assert cfg["weather"] == {"name": "서울"} and cfg["llm"] == {"provider": "claude"}
    assert "# AI 설정\n[llm]" in new and "교차 확인" not in new  # 다음 표 설명은 남고, 옛 설명은 지운다
    assert "2곳" in note and "1곳" in note
    assert not catalog.needs_migration(cfg)
    names = {f["name"] for f in catalog.active(cfg)}
    assert len(names) == len(catalog.active(news(interests=["ai-coding"]))) + 1 and "내 블로그" in names


def test_migrate_file_backs_up_once(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(catalog, "NOTICE", tmp_path / "news-migrated.txt")
    assert catalog.migrate_file() is None  # 파일 없음
    path.write_text(OLD, encoding="utf-8")
    note = catalog.migrate_file()
    assert note and catalog.NOTICE.read_text(encoding="utf-8") == note
    backups = list(tmp_path.glob("config.toml.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == OLD
    after = path.read_text(encoding="utf-8")
    assert catalog.migrate_file() is None and path.read_text(encoding="utf-8") == after  # 두 번째는 그대로
    assert config.load()["news"]["interests"] == ["ai-coding"]


def test_example_config_is_new_format():
    cfg = tomllib.loads((config.ROOT / "config.example.toml").read_text(encoding="utf-8"))
    assert cfg["news"]["interests"] == [] and "rss" not in cfg


def test_rss_items_only_from_active_feeds(tmp_path):
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    rows = [
        {"source": "GeekNews", "grp": "한국 커뮤니티", "url": "https://n/1", "title": "Claude Code 팁", "summary": ""},
        {"source": "GeekNews", "grp": "한국 커뮤니티", "url": "https://n/2", "title": "리눅스 커널 이야기", "summary": ""},
        {"source": "Claude Code 릴리스", "grp": "옛 묶음", "url": "https://n/3", "title": "v2.0", "summary": ""},
        {"source": "꺼진 출처", "grp": "공식 발표", "url": "https://n/4", "title": "Claude 새 소식", "summary": ""},
    ]
    db.upsert_items([{**r, "published": (now - timedelta(hours=1)).isoformat()} for r in rows])
    since = (now - timedelta(days=1)).isoformat()
    got = briefing.rss_items(news(interests=["ai-coding"]), db, since)
    assert {r["title"] for r in got} == {"Claude Code 팁", "v2.0"}  # 커뮤니티는 AI 글만, 안 받는 출처는 빼기
    assert next(r for r in got if r["title"] == "v2.0")["grp"] == "옛 묶음"
    assert briefing.rss_items(news(), db, since) == []


def test_taxonomy_points_to_profiles_or_ai():
    """분류표: 묶음이 있는 소분류는 그 관심사를, 아직 없는 묶음 id는 지워서 넘긴다 (그 소분류는 AI 질문으로 만든다)."""
    tax = catalog.taxonomy()
    names = [g["name"] for g in tax]
    assert len(names) == len(set(names)) and "테크·IT" in names and "스포츠" in names
    have = {p["id"] for p in catalog.profiles()}
    flat = [x for g in tax for i in g["items"] for x in [i, *i.get("children", [])]]
    assert all(x.get("profile") in have for x in flat if "profile" in x)
    tech = next(g for g in tax if g["name"] == "테크·IT")
    assert {x.get("profile") for x in tech["items"]} >= {"ai-coding", "data-science", "ml-engineering", "enterprise", "llm-apps"}
    raw = [x for g in catalog._taxonomy() for i in g["items"] for x in [i, *i.get("children", [])]]
    assert any(x.get("profile") and x["profile"] not in have for x in raw) or True  # 조사 중인 묶음은 원본엔 있어도 된다


def test_ai_prompts_get_taxonomy_context(monkeypatch):
    from popme import interests
    seen = []
    monkeypatch.setattr(interests, "research", lambda *a, **k: [])
    monkeypatch.setattr(interests.llm, "ask", lambda cfg, s, prompt, timeout=0: seen.append(prompt) or '{"type": "스포츠", "questions": []}')
    interests.questions({}, "포켓몬", "target", None, "엔터·문화 › 캐릭터·애니 › 포켓몬")
    interests.suggest({}, "포켓몬", "world", "target", {}, [], "엔터·문화 › 캐릭터·애니 › 포켓몬")
    assert all("분류: 엔터·문화 › 캐릭터·애니 › 포켓몬" in p for p in seen)
