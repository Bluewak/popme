"""분야 페르소나 (popme/domains.py, personas/profile_writer.toml): 정리·지시문 블록·새 분야에서 만들어 붙이기·저장."""
import json

from popme import catalog, domains, interests, persona

RAW = {"persona": {"name": "윤서진", "born": 1990, "home": "서울", "career": "보드게임 카페 운영 6년", "entry_story": "x",
                   "personality": "규칙이 깔끔한 게임을 좋아함", "voice": "담백"},
       "fan_types": [{"name": "파티게임파", "desc": "모임에서 가볍게", "wants": ["쉬운 신작"]}, {"desc": "이름 없음"}],
       "categories": [{"name": "신작·펀딩", "desc": "새 게임", "example": "펀딩 달성"}, {"name": "행사", "desc": "박람회"}],
       "cares": ["한글판 출시"], "dislikes": ["협찬 리뷰"], "sensitive": ["펀딩 투자 권유"],
       "vocabulary": [{"word": "유로게임", "meaning": "전략 위주"}], "calendar": [{"when": "10월", "what": "에센 슈필"}],
       "must_know": [{"name": "BGG", "why": "평점 기준"}], "personal": {"favorite": "엔진 빌딩"},
       "question_ideas": ["주로 몇 명이서 하세요?"]}


def test_clean_and_brief_by_task():
    d = domains.clean(RAW, "custom-board")
    assert d["id"] == "custom-board" and [x["name"] for x in d["fan_types"]] == ["파티게임파"]  # 이름 없는 건 버림
    q = domains.brief(d, "questions")
    assert "윤서진" in q and "한 해 흐름: 10월 에센 슈필" in q and "유로게임=전략 위주" in q and "주로 몇 명이서" in q
    assert "소식 종류" not in q and "BGG" not in q  # 질문엔 필요 없는 칸은 빼서 짧게
    e = domains.brief(d, "editor")
    assert "소식 종류: 신작·펀딩(새 게임); 행사(박람회)" in e
    assert "BGG(평점 기준)" in domains.brief(d, "people")
    assert domains.brief(None) == ""


def test_profile_writer_persona_is_well_formed():
    p = persona.load("profile_writer")
    assert set(p["output"]) == set(domains.KEYS)
    for ex in p["examples"]:
        assert set(ex["output"]) == set(domains.KEYS)


def test_new_field_questions_first_then_people_use_checked_card(monkeypatch):
    """질문은 분야 페르소나를 기다리지 않고 바로, 초안·사실 확인은 뒤에서 → ③ 사람 추천에서 받아 쓴다."""
    from popme import fieldcard
    monkeypatch.setattr(fieldcard, "BACKGROUND", True)
    seen = []
    monkeypatch.setattr(interests, "research", lambda *a, **k: ["보드게임콘 일정 발표"])
    monkeypatch.setattr(interests, "news_count", lambda e, scope: 3)

    def ask(cfg, system, prompt, timeout=0):
        seen.append(system + prompt)
        if "분야 페르소나" in system:
            return json.dumps(RAW, ensure_ascii=False)
        if "사실 확인 담당" in system:
            return json.dumps({"verdicts": [{"id": "m0", "status": "supported"}]})
        return json.dumps({"type": "취미·생활", "questions": []}, ensure_ascii=False)
    monkeypatch.setattr(persona.llm, "ask", ask)
    monkeypatch.setattr(interests.llm, "ask", ask)
    out = interests.questions({}, "보드게임", "target", None, "생활·취미 › 보드게임")
    assert out["domain"] is None  # 질문은 기다리지 않는다
    s = interests.suggest({}, "보드게임", "kr", "target", {}, [], "", out["domain"])
    assert s["domain"]["persona"]["name"] == "윤서진" and s["domain"]["checked"]  # 뒤에서 만든·확인한 카드
    assert "꼭 아는 사람·기관: BGG(평점 기준)" in seen[-1]  # 후보 추천엔 사람 칸 위주


def test_saved_with_custom_interest(monkeypatch):
    final = {"name": "보드게임", "tag": "보드게임", "summary": "보드게임 소식", "criteria": ["신작 먼저", "협찬 빼기"], "discovery": "리뷰어"}
    pid = interests.save_custom("보드게임", "kr", [], [], final, "target", "취미·생활", {}, RAW)
    assert domains.load(pid)["persona"]["name"] == "윤서진"
    assert pid in {p["id"] for p in catalog.profiles()}


def test_card_info_and_half_life():
    raw = {**RAW, "categories": [{"name": "신작·펀딩", "desc": "새 게임", "half_life": "하루"}, {"name": "행사", "desc": "박람회", "half_life": "곧"}],
           "info": {"x_share": "1.5", "login_nudge": "아주 강", "must_have": ["BGG RSS", ""],
                    "seasonality": [{"when": "10월", "intensity": "최고", "what": "에센"}, {"what": "때 없음"}]}}
    d = domains.clean(raw, "custom-board")
    assert [c["half_life"] for c in d["categories"]] == ["하루", ""]  # 정해진 값만
    i = d["info"]
    assert i["x_share"] == 1.0 and i["login_nudge"] == "weak" and i["must_have"] == ["BGG RSS"]
    assert i["seasonality"] == [{"when": "10월", "intensity": "보통", "what": "에센"}]
    assert domains.max_age(d, "신작·펀딩") == 1 and domains.max_age(d, "행사") == domains.DEFAULT_AGE == domains.max_age(None)
    assert "info" not in domains.clean(RAW)  # 카드가 아닌 페르소나엔 없음


def test_packs_ship_cards():
    for f in domains.REPO_DIR.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d == domains.clean(d, d["id"]), f.name  # 저장된 모양 = 정리한 모양
        assert d["info"]["checked"] and all(c["half_life"] for c in d["categories"]), f.name


def test_build_card_parses_research_hints():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_card", domains.config.ROOT / "tools/build_card.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    assert bc.share("25% (해외축구 이적만)") == (0.25, "해외축구 이적만") and bc.share("5~10%") == (0.075, "")
    assert bc.nudge("약 — 해외 팬에게만") == ("weak", "해외 팬에게만") and bc.nudge("중")[0] == "medium"


def test_text_cut_at_word_boundary():
    what = "전기차 보조금 지자체별 접수 시작과 국고 보조금 단가 확정 발표, 제조사별 가격 인하 경쟁과 신차 출시 일정 공개"
    d = domains.clean({"calendar": [{"when": "3월", "what": what}]})
    got = d["calendar"][0]["what"]
    assert len(got) <= 60 and what.startswith(got) and what[len(got)] == " "  # 낱말 중간에서 끊지 않는다
