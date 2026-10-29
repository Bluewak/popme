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


def test_x_rotation_keeps_mine_and_rotates_the_rest(monkeypatch):
    """내 핵심 계정은 늘, 발굴로 추가한 계정·관심사 계정은 남은 자리만큼 오래 안 본 것부터."""
    mine = [{"handle": f"me{i}", "name": "", "group": "", "focus": ""} for i in range(14)]
    found = [{"handle": f"d{i}", "name": "", "group": "발굴로 추가", "focus": ""} for i in range(40)]
    many = [{"handle": f"i{i}", "name": "", "group": "", "focus": "", "interest": "x"} for i in range(10)]
    cfg = {"x": {"accounts": mine}}
    monkeypatch.setattr(briefing, "core_accounts", lambda cfg, db: mine + found + many)
    first = briefing.x_to_visit(cfg, None)
    assert first[:14] == [a["handle"] for a in mine] and len(first) == briefing.X_VISIT_LIMIT  # 62개 → 30개
    second = briefing.x_to_visit(cfg, None)
    assert set(first[14:]).isdisjoint(second[14:])  # 다음 수집은 아직 안 본 계정부터
    big = {"x": {"accounts": mine + found[:20]}}  # 내 계정이 30개를 넘어도 돌아가는 쪽은 10개는 본다
    assert len(briefing.x_to_visit(big, None)) == 34 + 10
    monkeypatch.setattr(briefing, "core_accounts", lambda cfg, db: mine[:3] + many[:5])
    assert len(briefing.x_to_visit(cfg, None)) == 8  # 넘지 않으면 다 본다


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


# ---------------------------------------------------------------- 보는 방식 → 분야별 질문 (설계 4-6)

def test_clean_questions_follows_template():
    raw = {"type": "스포츠", "questions": [
        {"slot": "대상", "text": "응원하는 팀은?", "options": ["토트넘", {"label": "토트넘"}, {"label": "상관없어요"}],
         "options_by": {"프리미어리그": [{"label": "아스널", "region": "world"}], "빈 갈래": []}},
        {"slot": "범위", "text": "어떤 리그?", "options": [{"label": "K리그", "region": "kr"}, {"label": "프리미어리그"}]},
        {"slot": "변화", "text": "대상 위주에는 없는 칸"},
        {"slot": "범위", "text": "같은 칸 두 번"}]}
    out = interests.clean_questions(raw, "target")
    assert out["type"] == "스포츠" and [q["slot"] for q in out["questions"]] == ["범위", "대상"]  # 칸 순서는 틀대로
    rng, tgt = out["questions"]
    assert [o["label"] for o in rng["options"]] == ["K리그", "프리미어리그"] and rng["options"][0]["region"] == "kr"
    assert [o["label"] for o in tgt["options"]] == ["토트넘"]  # 중복·「상관없어요」는 코드가 다룬다
    assert list(tgt["options_by"]) == ["프리미어리그"]
    both = interests.clean_questions({"type": "없는 유형", "questions": []}, "both")
    assert both["type"] == "기타" and [q["slot"] for q in both["questions"]] == ["범위", "변화", "대상"]  # 빠진 칸은 고정 질문
    assert all(q["options"] == [] for q in both["questions"])


def test_questions_never_ask_private_things():
    raw = {"type": "기타", "questions": [{"slot": "범위", "text": "지지 정당은?", "options": ["가", "나"]},
                                        {"slot": "대상", "text": "관심 대상은?", "options": ["종교 단체 A", "B"]}]}
    out = interests.clean_questions(raw, "target")
    assert all("정당" not in q["text"] for q in out["questions"])
    assert out["questions"][1]["options"] == []  # 묻지 않는 것이 섞인 질문은 버리고 고정 질문으로


def test_questions_uses_headlines_and_checks_targets(monkeypatch):
    seen = {}
    monkeypatch.setattr(interests, "research", lambda field, limit=30: ["손흥민 결승골", "K리그 개막"])

    def ask(cfg, system, prompt, timeout=0):
        seen["prompt"] = prompt
        return json.dumps({"type": "스포츠", "questions": [
            {"slot": "범위", "text": "어떤 리그?", "options": [{"label": "K리그", "region": "kr"}]},
            {"slot": "대상", "text": "응원 팀?", "options": [{"label": "전북 현대", "region": "kr"}, {"label": "가짜 FC", "region": "kr"}],
             "options_by": {"K리그": [{"label": "울산 HD", "region": "kr"}, {"label": "없는 팀", "region": "world", "en": "Ghost FC"}]}}]},
            ensure_ascii=False)
    monkeypatch.setattr(interests.llm, "ask", ask)
    monkeypatch.setattr(interests, "news_count", lambda e, scope: 0 if e["name"] in ("가짜 FC", "없는 팀") else 4)
    out = interests.questions({}, "축구", "target")
    assert "손흥민 결승골" in seen["prompt"] and "범위" in seen["prompt"] and "대상" in seen["prompt"] and "변화" not in seen["prompt"].split("questions:")[1].split("각 질문")[0]
    tgt = out["questions"][1]
    assert [o["label"] for o in tgt["options"]] == ["전북 현대"] and [o["label"] for o in tgt["options_by"]["K리그"]] == ["울산 HD"]
    assert out["headlines"] == ["손흥민 결승골", "K리그 개막"]
    monkeypatch.setattr(interests.llm, "ask", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    failed = interests.questions({}, "축구", "overview")
    assert failed["error"] and [q["slot"] for q in failed["questions"]] == ["범위", "변화"]


def test_scope_from_answers():
    qs = [{"slot": "범위", "options": [{"label": "K리그", "region": "kr"}, {"label": "EPL", "region": "world"}], "options_by": {}}]
    assert interests.scope_from(qs, {"범위": ["K리그"]}) == "kr"
    assert interests.scope_from(qs, {"범위": ["K리그", "EPL"]}) == "world"
    assert interests.scope_from(qs, {}) == "world"  # 아무것도 안 골랐으면 해외도


def test_suggest_overview_asks_media_and_searches(monkeypatch):
    seen = {}

    def ask(cfg, system, prompt, timeout=0):
        seen["prompt"] = prompt
        return json.dumps({"people": [{"name": "Dylan Patel", "x": "dylan522p"}], "companies": [{"name": "SK하이닉스", "region": "kr"}],
                           "media": [{"name": "더일렉", "site": "thelec.kr", "region": "kr"}, {"name": "사이트 없음"}],
                           "searches": ["반도체 업계", "HBM", "D램", "네 번째"], "topics": []}, ensure_ascii=False)
    monkeypatch.setattr(interests.llm, "ask", ask)
    s = interests.suggest({}, "반도체", "world", "overview", {"범위": ["메모리"]}, ["HBM 공급 부족"])
    assert "정리해 전하는 사람" in seen["prompt"] and "범위: 메모리" in seen["prompt"] and "HBM 공급 부족" in seen["prompt"]
    assert [m["name"] for m in s["media"]] == ["더일렉"] and s["media"][0]["kind"] == "media"
    assert s["searches"] == ["반도체 업계", "HBM", "D램"]
    target = interests.suggest({}, "축구", "world", "target", {"대상": ["토트넘"]})
    assert "당사자" in seen["prompt"] and target["searches"] == []  # 대상 위주는 분야 검색어를 받지 않는다


def test_save_custom_with_lens_media_searches():
    verified = [{"kind": "media", "name": "더일렉", "region": "kr", "site": "thelec.kr", "feed": "https://example.com/elec.xml"},
                {"kind": "media", "name": "RSS 없음", "region": "world", "feed": None},
                {"kind": "search", "name": "반도체 업계", "region": "kr", "news_count": 40},
                {"kind": "search", "name": "기사 없음", "region": "kr", "news_count": 0},
                {"kind": "company", "name": "SK하이닉스", "region": "kr", "news": "SK하이닉스", "news_count": 80}]
    final = {"name": "반도체", "tag": "반도체", "summary": "반도체 업계", "criteria": ["큰 변화 먼저", "가십 빼기"], "discovery": "분석가"}
    pid = interests.save_custom("반도체", "world", verified, [], final, "overview", "산업·기업", {"범위": ["메모리(HBM·D램)"], "변화": []})
    p = next(x for x in catalog.profiles() if x["id"] == pid)
    assert p["lens"] == "overview" and p["type"] == "산업·기업" and p["answers"] == {"범위": ["메모리(HBM·D램)"]}
    got = {f["name"]: f for f in catalog.active(news(interests=[pid]))}
    assert got["더일렉"]["group"] == "실사용 검증" and "RSS 없음" not in got
    assert "뉴스 · 반도체 업계" in got and "뉴스 · 기사 없음" not in got and "뉴스 · SK하이닉스" in got
    assert "고른 것: 범위: 메모리(HBM·D램)" in briefing.rules_for(news(interests=[pid]))["criteria"]


def test_questions_reuses_headlines_from_screen(monkeypatch):
    """화면이 먼저 받아 「읽는 중」으로 보여 준 헤드라인을 그대로 쓴다 (두 번 받지 않음)."""
    monkeypatch.setattr(interests, "research", lambda *a, **k: (_ for _ in ()).throw(AssertionError("다시 받으면 안 됨")))
    monkeypatch.setattr(interests.llm, "ask", lambda *a, **k: '{"type": "스포츠", "questions": []}')
    out = interests.questions({}, "축구", "overview", ["토트넘, 새 감독 선임 임박"])
    assert out["headlines"] == ["토트넘, 새 감독 선임 임박"]


def test_interests_only_window_saves_just_interests():
    """브리핑 탭에서 「관심사」만 연 창의 저장: 고른 관심사·고친 칸만 쓰고 다른 설정은 그대로."""
    from popme import setup_window as sw
    sw.write(sw.base_text())  # 예시 설정으로 config.toml을 만들어 두고
    before = tomllib.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    api = sw.Api({"start": "edit", "pid": "ai-coding"})
    assert api.get()["mode"] == {"start": "edit", "pid": "ai-coding"}
    r = api.save_interests({"chosen": ["ai-coding", "ml-engineering"], "picks": {"ai-coding": {"topics": ["장애·보안"]}}, "customs": []})
    assert r == {"ok": True} and api._saved
    after = tomllib.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert after["news"]["interests"] == ["ai-coding", "ml-engineering"]
    assert after["news"]["picks"]["ai-coding"] == {"topics": ["장애·보안"]}
    assert {k: v for k, v in after.items() if k != "news"} == {k: v for k, v in before.items() if k != "news"}
    assert api.save_interests({"chosen": [], "picks": {}, "customs": []})["ok"] is False


# ---------------------------------------------------------------- 미리 만든 관심사의 「대상」 질문 (쓰는 도구 등)

def test_target_options_point_to_real_feeds_and_companies():
    feeds = catalog.feeds()
    for p in catalog.profiles():
        if p.get("custom"):
            continue
        assert p.get("target", {}).get("options"), p["id"]
        used = {e["id"] for e in p["feeds"]}
        names = {e["name"] for e in p.get("companies", [])}
        for o in p["target"]["options"]:
            assert set(o.get("feeds", [])) <= used & set(feeds), (p["id"], o["label"])
            assert set(o.get("companies", [])) <= names, (p["id"], o["label"])


def test_uses_default_from_my_tools_then_picks():
    ai = next(p for p in catalog.profiles() if p["id"] == "ai-coding")
    cfg = {**news(interests=["ai-coding"]), "user": {"tools": "Claude Code, Copilot"}}
    assert catalog.uses(cfg, ai) == ["Claude Code"]  # 「pi」가 「Copilot」에서 걸리지 않는다, 「GitHub Copilot」은 단어가 달라 안 걸림
    cfg["user"]["tools"] = "Claude Code, GitHub Copilot, pi"
    assert catalog.uses(cfg, ai) == ["Claude Code", "GitHub Copilot", "pi"]
    cfg["news"]["picks"] = {"ai-coding": {"uses": ["Cursor"]}}
    assert catalog.uses(cfg, ai) == ["Cursor"]


def test_chosen_tool_turns_on_its_feeds_and_company_but_keeps_others():
    cfg = {**news(interests=["ai-coding"], picks={"ai-coding": {"uses": ["Claude Code", "Cursor"]}}), "user": {"tools": "Claude Code"}}
    names = {f["name"] for f in catalog.active(cfg)}
    assert "Cursor 변경내역" in names  # 기본 꺼짐이어도 켠다
    assert "Gemini CLI 릴리스" in names  # 안 고른 도구도 그대로 받는다 (사용자 결정)
    assert "Windsurf 변경내역" not in names  # 기본 꺼짐 + 안 고름 → 그대로 꺼짐
    ai = next(p for p in catalog.profiles() if p["id"] == "ai-coding")
    assert "Cursor" in [e["name"] for e in catalog.entities(cfg, ai)]
    assert "cursor_ai" in [a["handle"] for a in catalog.x_accounts(cfg)]
    assert catalog.user_tools(cfg) == "Claude Code, Cursor"
    assert "내가 쓰는 것: Claude Code·Cursor" in briefing.rules_for(cfg)["criteria"]


def test_preset_view_and_save_uses():
    from popme import setup_window as sw
    cfg = {**news(interests=["ai-coding"]), "user": {"tools": "Claude Code"}}
    view = interests.preset_view("ai-coding", cfg)
    assert view["target"]["text"] == "쓰는 코딩 도구는?"
    assert [o["label"] for o in view["target"]["options"] if o["on"]] == ["Claude Code"]
    sw.write(sw.base_text())
    r = sw.Api({"start": "edit", "pid": "ai-coding"}).save_interests(
        {"chosen": ["ai-coding"], "picks": {"ai-coding": {"uses": ["Claude Code", "Codex", "Zed"]}}, "customs": []})
    assert r == {"ok": True}
    saved = config.load()
    assert saved["news"]["picks"]["ai-coding"]["uses"] == ["Claude Code", "Codex", "Zed"]
    view = interests.preset_view("ai-coding", saved)
    assert [o["label"] for o in view["target"]["options"] if o["on"]] == ["Claude Code", "Codex", "Zed"]  # 직접 적은 것도
