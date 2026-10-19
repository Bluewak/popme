"""관심사 설정 (popme/interests.py, catalog의 사람·기업): 후보 정리, 확인, 저장, 뉴스 출처, X 계정 돌아가며 보기."""
import json
import tomllib
from datetime import datetime, timedelta, timezone

from popme import briefing, catalog, config, interests
from popme.db import DB


def news(**kw):
    return {"news": {"interests": [], **kw}}


SOCCER = {
    "id": "custom-soccer", "name": "축구", "tag": "축구", "summary": "축구 소식", "custom": True, "scope": "world",
    "criteria": "- 이적·부상 먼저", "topics": ["이적", "부상"], "caps": {"뉴스": 20},
    "people": [{"name": "손흥민", "region": "kr", "news": "손흥민"},
               {"name": "파브리지오 로마노", "region": "world", "x": "FabrizioRomano", "news": "파브리지오 로마노",
                "news_en": "Fabrizio Romano"}],
    "companies": [{"name": "맨시티", "region": "world", "x": "ManCity", "news": "맨시티", "news_en": "Manchester City",
                   "feeds": ["https://example.com/mancity.xml"]},
                  {"name": "UEFA", "region": "world", "news": "UEFA", "default": False}],
}


def with_soccer(monkeypatch):
    base = catalog._profiles()
    monkeypatch.setattr(catalog, "_profiles", lambda: [*base, SOCCER])


def test_preset_profiles_have_people_companies_topics():
    for p in catalog.profiles():
        assert p.get("topics") and p.get("people") and p.get("companies"), p["id"]
        for e in p["people"] + p["companies"]:
            assert e["name"] and e["region"] in ("kr", "world")
            assert not e.get("x") or interests.HANDLE_RE.match(e["x"]), e
    ai = next(p for p in catalog.profiles() if p["id"] == "ai-coding")
    ex = tomllib.loads((config.ROOT / "config.example.toml").read_text(encoding="utf-8"))
    assert {a["handle"] for a in ex["x"]["accounts"]} <= {e.get("x") for e in ai["people"]}  # 예시의 핵심 계정 = AI 코딩 사람들


def test_entities_default_then_picks(monkeypatch):
    with_soccer(monkeypatch)
    assert [e["name"] for e in catalog.entities(news(), SOCCER)] == ["손흥민", "파브리지오 로마노", "맨시티"]  # UEFA는 기본 꺼짐
    cfg = news(picks={"custom-soccer": {"people": ["손흥민"], "companies": ["UEFA"],
                                        "extra": [{"name": "이강인", "region": "kr", "news": "이강인"}]}})
    assert [e["name"] for e in catalog.entities(cfg, SOCCER)] == ["손흥민", "UEFA", "이강인"]


def test_news_feeds_language_and_caps(monkeypatch):
    with_soccer(monkeypatch)
    got = {f["name"]: f for f in catalog.active(news(interests=["custom-soccer"]))}
    son, romano = got["뉴스 · 손흥민"], got["뉴스 · 파브리지오 로마노 (해외)"]
    assert "hl=ko" in son["url"] and "q=%EC%86%90" in son["url"]               # 국내 사람은 한국어 기사
    assert "hl=en-US" in romano["url"] and "q=Fabrizio+Romano" in romano["url"]  # 해외는 원어 이름으로
    assert son["group"] == catalog.NEWS_GROUP and son["cap"] == catalog.NEWS_PER_FEED
    assert got["맨시티 공식"]["url"] == "https://example.com/mancity.xml"
    import re
    assert re.search(son["skip"], "삼성전자 HBM 소식 - Calgary Roughnecks")
    kr = {f["name"] for f in catalog.active(news(interests=["custom-soccer"], picks={"custom-soccer": {"scope": "kr"}}))}
    assert "뉴스 · 파브리지오 로마노" in kr  # 「한국어만」이면 해외 사람도 한국어 기사로
    assert catalog.caps(news(interests=["custom-soccer"]))[catalog.NEWS_GROUP] == 20


def test_x_accounts_merge_and_off(monkeypatch):
    with_soccer(monkeypatch)
    cfg = {**news(interests=["custom-soccer", "ai-coding"]), "x": {"accounts": [{"handle": "simonw", "name": "Simon",
                                                                                 "group": "내 계정", "focus": "x"}]}}
    handles = [a["handle"] for a in catalog.x_accounts(cfg)]
    assert "FabrizioRomano" in handles and "ManCity" in handles and "bcherny" in handles
    cfg["news"]["off"] = ["x:fabrizioromano"]
    assert "FabrizioRomano" not in [a["handle"] for a in catalog.x_accounts(cfg)]
    core = briefing.core_accounts(cfg, DB(config.DATA_DIR / "unused.db") if False else _db())
    assert next(a for a in core if a["handle"] == "simonw")["group"] == "내 계정"  # 내 계정이 먼저 (관심사 쪽과 겹쳐도)


def _db():
    import tempfile
    from pathlib import Path
    return DB(Path(tempfile.mkdtemp()) / "t.db")


def test_x_rotation_keeps_mine_and_rotates_interest_accounts(monkeypatch):
    mine = [{"handle": f"me{i}", "name": "", "group": "", "focus": ""} for i in range(25)]
    many = [{"handle": f"i{i}", "name": "", "group": "", "focus": "", "interest": "x"} for i in range(20)]
    monkeypatch.setattr(briefing, "core_accounts", lambda cfg, db: mine + many)
    first = briefing.x_to_visit({}, None)
    assert first[:25] == [a["handle"] for a in mine] and len(first) == 35  # 남는 자리가 적어도 관심사 계정 10개는
    second = briefing.x_to_visit({}, None)
    assert set(first[25:]).isdisjoint(second[25:])  # 다음 수집은 아직 안 본 계정부터
    monkeypatch.setattr(briefing, "core_accounts", lambda cfg, db: mine[:3] + many[:5])
    assert len(briefing.x_to_visit({}, None)) == 8  # 넘지 않으면 다 본다


def test_news_capped_per_person(monkeypatch, tmp_path):
    with_soccer(monkeypatch)
    db = DB(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    db.upsert_items([{"source": "뉴스 · 손흥민", "grp": "뉴스", "url": f"https://n/{i}", "title": f"기사 {i}", "summary": "",
                      "published": (now - timedelta(minutes=i)).isoformat()} for i in range(12)])
    got = briefing.rss_items(news(interests=["custom-soccer"]), db, (now - timedelta(days=1)).isoformat())
    assert [r["title"] for r in got] == [f"기사 {i}" for i in range(catalog.NEWS_PER_FEED)]


def test_topics_line_in_criteria(monkeypatch):
    with_soccer(monkeypatch)
    rules = briefing.rules_for(news(interests=["custom-soccer"]))
    assert "특히 궁금한 것: 이적·부상" in rules["criteria"]
    ai = briefing.rules_for(news(interests=["ai-coding"]))
    assert "특히 궁금한 것" not in ai["criteria"]  # 미리 만든 관심사는 고친 적이 없으면 그대로
    picked = briefing.rules_for(news(interests=["ai-coding"], picks={"ai-coding": {"topics": ["장애·보안"]}}))
    assert "특히 궁금한 것: 장애·보안" in picked["criteria"]


def test_suggest_cleans_ai_answer(monkeypatch):
    raw = {"people": [{"name": "손흥민", "why": "국가대표 주장", "region": "kr", "x": "@bad handle!", "news": "손흥민"},
                      {"name": "", "why": "이름 없음"},
                      {"name": "Fabrizio Romano", "region": "world", "x": "@FabrizioRomano", "news_en": "Fabrizio Romano"}],
           "companies": [{"name": "맨시티", "site": "javascript:alert(1)", "x": "ManCity"},
                         {"name": "FIFA", "site": "fifa.com"}],
           "topics": ["이적", "이적", "아주아주아주아주긴세부주제이름"]}
    monkeypatch.setattr(interests.llm, "ask", lambda *a, **k: "설명 " + json.dumps(raw, ensure_ascii=False))
    s = interests.suggest({}, "축구", "world")
    assert [e["name"] for e in s["people"]] == ["손흥민", "Fabrizio Romano"]
    assert s["people"][0]["x"] == "" and s["people"][1]["x"] == "FabrizioRomano"
    assert s["companies"][0]["site"] == "" and s["companies"][1]["site"] == "fifa.com"
    assert s["topics"] == ["이적", "아주아주아주아주긴세"]
    monkeypatch.setattr(interests.llm, "ask", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert interests.suggest({}, "축구", "world")["error"]


def test_finalize_falls_back(monkeypatch):
    monkeypatch.setattr(interests.llm, "ask", lambda *a, **k: '{"name": "축구 소식", "criteria": ["하나"]}')
    f = interests.finalize({}, "축구", "world", [], [], [])
    assert f["name"] == "축구 소식" and len(f["criteria"]) == 3  # 기준이 모자라면 정해진 문장


def test_verify_and_save_custom(monkeypatch):
    monkeypatch.setattr(interests, "x_exists", lambda h: {"ManCity": True, "ghost": False}.get(h))
    monkeypatch.setattr(interests, "find_feed", lambda site: "https://example.com/feed" if site == "mancity.com" else None)
    monkeypatch.setattr(interests, "news_count", lambda e, scope: 0 if e["name"] == "조용한 사람" else 7)
    ents = [{"kind": "company", "name": "맨시티", "region": "world", "x": "ManCity", "site": "mancity.com",
             "news": "맨시티", "news_en": "Manchester City"},
            {"kind": "person", "name": "가짜", "region": "world", "x": "ghost", "news": "가짜"},
            {"kind": "person", "name": "조용한 사람", "region": "kr", "x": "", "news": "조용한 사람"}]
    v = interests.verify(ents, "world")
    assert [e["x_status"] for e in v] == ["ok", "none", ""]
    final = {"name": "축구", "tag": "축구", "summary": "축구 소식", "criteria": ["이적 먼저", "가십 빼기"], "discovery": "기자"}
    pid = interests.save_custom("축구", "world", v, ["이적"], final)
    assert pid == "custom-축구"
    p = next(x for x in catalog.profiles() if x["id"] == pid)
    city = next(e for e in p["companies"] if e["name"] == "맨시티")
    assert city["x"] == "ManCity" and city["feeds"] == ["https://example.com/feed"] and city["news_en"] == "Manchester City"
    fake = next(e for e in p["people"] if e["name"] == "가짜")
    assert "x" not in fake  # 없는 계정은 저장하지 않는다
    quiet = next(e for e in p["people"] if e["name"] == "조용한 사람")
    assert "news" not in quiet  # 기사가 안 나오는 검색어도
    cfg = news(interests=[pid])
    assert {a["handle"] for a in catalog.x_accounts(cfg)} == {"ManCity"}
    assert "- 이적 먼저" in briefing.rules_for(cfg)["criteria"]


def test_save_picks_and_preset_view():
    interests.save_picks("data-science", "kr", ["Wes McKinney"], ["DuckDB"], ["실험·인과추론"],
                         [{"name": "변성윤", "region": "kr", "news": "변성윤"}])
    cfg = config.load()
    pk = catalog.picks(cfg, "data-science")
    assert pk["scope"] == "kr" and pk["people"] == ["Wes McKinney"] and pk["extra"][0]["name"] == "변성윤"
    view = interests.preset_view("data-science", cfg)
    assert [e["name"] for e in view["people"] if e["on"]] == ["Wes McKinney", "변성윤"]
    assert [t["name"] for t in view["topics"] if t["on"]] == ["실험·인과추론"]
    fresh = interests.preset_view("ml-engineering", cfg)
    assert all(t["on"] for t in fresh["topics"]) and not all(e["on"] for e in fresh["people"])  # 기본 꺼짐은 체크 안 됨


def test_find_feed_uses_link_tag_then_paths(monkeypatch):
    page = b'<html><head><link rel="alternate" type="application/rss+xml" href="/news.xml"></head></html>'

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, n=None):
            return page
    monkeypatch.setattr(interests.urllib.request, "urlopen", lambda req, timeout=None: R())
    recent = [{"published": datetime.now(timezone.utc).isoformat(), "title": "t"}]
    old = [{"published": "2020-01-01T00:00:00+00:00", "title": "t"}]
    seen = {"https://club.example/news.xml": old, "https://club.example/rss.xml": recent}
    monkeypatch.setattr(interests.rss, "peek", lambda url, timeout=None: seen.get(url, []))
    assert interests.find_feed("club.example") == "https://club.example/rss.xml"  # 멈춘 피드(2020)는 건너뛴다


def test_wizard_save_writes_interests_picks_and_custom(monkeypatch):
    """마법사 「관심사」 단계 → 저장: 새 관심사 파일, 고른 관심사(새 id 포함), 미리 만든 관심사에서 고친 칸만."""
    from popme import setup_window as sw
    monkeypatch.setattr(sw, "geocode_place", lambda name: {"name": name, "lat": 37.5, "lon": 127.0})
    ch = {"provider": "claude", "plan": "", "tools": "Claude Code", "source": "timetree", "ics": [], "briefing_after": "09:00",
          "character": "", "use_windows_location": True, "place": "", "mail": None,
          "interests": {"chosen": ["ai-coding", "없는-관심사"],
                        "picks": {"ai-coding": {"topics": ["장애·보안"], "extra": [{"kind": "company", "name": "Zed", "x": "zeddotdev"}]}},
                        "customs": [{"field": "축구", "scope": "world", "topics": ["이적"],
                                     "verified": [{"kind": "person", "name": "손흥민", "region": "kr", "news": "손흥민",
                                                   "x_status": "", "news_count": 9}],
                                     "final": {"name": "축구", "tag": "축구", "summary": "축구 소식",
                                               "criteria": ["이적 먼저", "가십 빼기"], "discovery": "기자"}}]}}
    assert sw.Api().save(ch) == {"ok": True}
    cfg = config.load()
    assert cfg["news"]["interests"] == ["ai-coding", "custom-축구"]
    assert cfg["news"]["picks"]["ai-coding"] == {"topics": ["장애·보안"], "extra": [{"kind": "company", "name": "Zed", "x": "zeddotdev"}]}
    assert "Zed" in [e["name"] for e in catalog.entities(cfg, next(p for p in catalog.profiles() if p["id"] == "ai-coding"))]
    assert "뉴스 · 손흥민" in {f["name"] for f in catalog.active(cfg)}
    # 관심사를 하나도 안 고르면 저장하지 않는다
    bad = {**ch, "interests": {"chosen": [], "picks": {}, "customs": []}}
    assert sw.Api().save(bad)["ok"] is False
