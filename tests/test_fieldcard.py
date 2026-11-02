"""G1 분야 카드 확인 (popme/fieldcard.py): 사실마다 뉴스 → 사실 확인 페르소나 → 어긋나면 빼고, 모르면 미확인. 설정 흐름에선 뒤에서."""
import json

from popme import domains, fieldcard, interests, persona

DRAFT = domains.clean({"persona": {"name": "윤서진"}, "categories": [{"name": "신작", "desc": "새 게임"}],
                       "must_know": [{"name": "보드게임긱", "why": "평점 기준"}, {"name": "한별보드", "why": "유통사"}, {"name": "별빛상회", "why": "카페"}],
                       "calendar": [{"when": "11월", "what": "보드게임콘"}]}, "custom-board")


def fake_ai(monkeypatch, verdicts, seen=None):
    def ask(cfg, system, user, timeout=0):
        if seen is not None:
            seen.append(json.loads(user.rsplit("<input>", 1)[1].split("</input>")[0]))
        return json.dumps({"verdicts": verdicts}, ensure_ascii=False)
    monkeypatch.setattr(persona.llm, "ask", ask)


def test_check_drops_contradicted_and_marks_unknown(monkeypatch):
    queries = []
    monkeypatch.setattr(interests, "research", lambda q, limit=0, days=0: queries.append((q, days)) or ([f"{q} 소식"] if "콘" not in q else []))
    seen = []
    fake_ai(monkeypatch, [{"id": "m0", "status": "supported"}, {"id": "m1", "status": "contradicted", "note": "폐업"},
                          {"id": "m2", "status": "unknown"}, {"id": "c0", "status": "maybe-wrong"}][:3], seen)
    d = fieldcard.build({}, "보드게임", domain=DRAFT)
    assert [(x["name"], x["ok"]) for x in d["must_know"]] == [("보드게임긱", True), ("별빛상회", False)]  # 한별보드는 빠짐
    assert d["calendar"][0]["ok"] is False and d["checked"]  # 답이 없던 c0 → 모름
    assert ("보드게임긱 보드게임", fieldcard.DAYS) in queries and ("보드게임 보드게임콘", fieldcard.YEAR) in queries  # 이름 + 분야
    assert fieldcard.key_words("PGA 챔피언십·US오픈·디오픈 등 남자 메이저") == "PGA 챔피언십"
    assert fieldcard.key_words("마스터스 (남자 메이저 첫 대회)") == "마스터스"
    assert fieldcard.key_words("가을 성수기와 단풍 라운드 수요") == "가을 성수기"
    assert seen[0]["claims"][3] == {"id": "c0", "text": "11월 보드게임콘", "evidence": []}
    b = domains.brief(d, "people")
    assert "보드게임긱(평점 기준)," in b and "별빛상회(카페) (미확인)" in b and "한별보드" not in b and "단정하지 않는다" in b
    assert d == domains.clean(d, d["id"])  # 확인 표시가 정리를 거쳐도 남는다


def test_build_returns_none_when_ai_fails(monkeypatch):
    monkeypatch.setattr(interests, "research", lambda *a, **k: [])
    monkeypatch.setattr(persona.llm, "ask", lambda *a, **k: "JSON 아님")
    assert fieldcard.build({}, "보드게임", domain=DRAFT) is None


def test_runs_in_background_and_suggest_uses_checked_card(monkeypatch):
    monkeypatch.setattr(interests, "research", lambda *a, **k: ["근거"])
    monkeypatch.setattr(fieldcard, "BACKGROUND", True)
    fake_ai(monkeypatch, [{"id": "m0", "status": "supported"}])
    fieldcard.start({}, "보드게임", DRAFT)
    fieldcard.start({}, "보드게임", DRAFT)  # 같은 분야는 한 번만
    assert len(fieldcard._jobs) == 1
    checked = fieldcard.verified("보드게임", wait=5)
    assert checked and checked["checked"] and fieldcard.verified("없는 분야") is None

    prompts = []
    monkeypatch.setattr(interests.llm, "ask", lambda cfg, system, prompt, timeout=0: prompts.append(prompt) or "{}")
    out = interests.suggest({}, "보드게임", "kr", domain=DRAFT)
    assert out["domain"]["checked"] and "(미확인)" in prompts[0]  # 확인된 카드로 바꿔서 추천
