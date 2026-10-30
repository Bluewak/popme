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


def test_new_field_gets_persona_then_questions_use_it(monkeypatch):
    seen = []
    monkeypatch.setattr(interests, "research", lambda *a, **k: ["보드게임콘 일정 발표"])
    monkeypatch.setattr(interests, "news_count", lambda e, scope: 3)

    def ask(cfg, system, prompt, timeout=0):
        seen.append(system + prompt)
        if "분야 페르소나" in system:
            return json.dumps(RAW, ensure_ascii=False)
        return json.dumps({"type": "취미·생활", "questions": []}, ensure_ascii=False)
    monkeypatch.setattr(persona.llm, "ask", ask)
    monkeypatch.setattr(interests.llm, "ask", ask)
    out = interests.questions({}, "보드게임", "target", None, "생활·취미 › 보드게임")
    assert out["domain"]["persona"]["name"] == "윤서진"
    assert "너는 이 분야를 오래 본 사람의 눈으로 일한다: 윤서진" in seen[-1]  # 질문 지시문에 붙음
    interests.suggest({}, "보드게임", "kr", "target", {}, [], "", out["domain"])
    assert "꼭 아는 사람·기관: BGG" in seen[-1]  # 후보 추천엔 사람 칸 위주


def test_saved_with_custom_interest(monkeypatch):
    final = {"name": "보드게임", "tag": "보드게임", "summary": "보드게임 소식", "criteria": ["신작 먼저", "협찬 빼기"], "discovery": "리뷰어"}
    pid = interests.save_custom("보드게임", "kr", [], [], final, "target", "취미·생활", {}, RAW)
    assert domains.load(pid)["persona"]["name"] == "윤서진"
    assert pid in {p["id"] for p in catalog.profiles()}
