"""④ 취향 문답 (popme/taste.py): 편집자가 서로 다른 종류 10개 → 몰리면 다시 → 누른 것으로 취향 분석가 → 브리핑 지시문·저장."""
import json
import tomllib

from popme import briefing, catalog, interests, persona, taste
from popme import setup_window as sw

HEADS = [f"헤드라인 {i}" for i in range(12)]
DOMAIN = {"id": "sports-soccer", "persona": {"name": "가명"}, "categories": [{"name": n, "desc": ""} for n in ("경기", "이적", "부상", "행정")]}


def fake_ai(monkeypatch, answers, seen):
    answers = iter(answers)

    def ask(cfg, system, user, timeout=0):
        seen.append((system, json.loads(user.rsplit("<input>", 1)[1].split("</input>")[0])))
        return json.dumps(next(answers), ensure_ascii=False)
    monkeypatch.setattr(persona.llm, "ask", ask)


def test_pool_dedupes_and_adds_subs(monkeypatch):
    asked = []
    monkeypatch.setattr(interests, "research", lambda q, limit=0, days=7: asked.append(q) or [f"{q} 소식", "같은 제목"])
    got = taste.pool("축구", ["K리그", "해외축구"], extra=["받아 둔 것"], exclude=["축구 K리그 소식"])
    assert asked == ["축구", "축구 K리그", "축구 해외축구"]
    assert got == ["받아 둔 것", "축구 소식", "같은 제목", "축구 해외축구 소식"]  # 같은 제목·이미 보여 준 것은 뺀다


def test_editor_asked_again_when_crowded(monkeypatch):
    monkeypatch.setattr(taste, "pool", lambda *a, **k: HEADS)
    crowded = {"picks": [{"id": i, "category": "경기"} for i in range(5)]}
    good = {"picks": [{"id": i, "category": c} for i, c in enumerate(["경기", "이적", "부상", "행정", "경기"])]
            + [{"id": 99, "category": "이적"}, {"id": 1, "category": "이적"}]}
    seen = []
    fake_ai(monkeypatch, [crowded, good], seen)
    out = taste.headlines({}, "축구", DOMAIN, "kr", count=5)
    assert len(seen) == 2 and "한 종류에 몰렸다: 경기" in seen[1][1]["feedback"]
    assert "이 분야를 오래 본 사람의 눈으로" in seen[0][0] and "소식 종류: 경기" in seen[0][0]  # 분야 페르소나의 눈으로
    assert [h["title"] for h in out] == HEADS[:5]  # 없는 번호(99)·같은 번호(1)는 걸러진다
    assert out[1] == {"title": "헤드라인 1", "category": "이적"}


def test_problems():
    cats = ["경기", "이적", "부상", "행정"]
    assert taste.problems([{"id": 0, "category": "경기"}, {"id": 1, "category": "이적"}], HEADS[:2], cats, 10) == []
    assert "같은 헤드라인을 두 번 골랐다" in taste.problems([{"id": 0, "category": ""}] * 2, HEADS[:2], cats, 2)
    assert any("개뿐" in x for x in taste.problems([{"id": 0, "category": ""}], HEADS, cats, 10))


def test_editor_failure_falls_back(monkeypatch):
    monkeypatch.setattr(taste, "pool", lambda *a, **k: HEADS)
    monkeypatch.setattr(persona.llm, "ask", lambda *a, **k: "JSON 아님")
    assert [h["title"] for h in taste.headlines({}, "축구", count=3)] == HEADS[:3]
    monkeypatch.setattr(taste, "pool", lambda *a, **k: [])
    assert taste.headlines({}, "축구") == []


def test_analyze_and_brief_line(monkeypatch):
    shown = [{"title": "A", "category": "이적"}, {"title": "B", "category": "행정"}]
    assert taste.analyze({}, "축구", None, shown, []) == {"likes": [], "less": [], "summary": "전반을 고르게"}  # 안 누르면 AI 없이
    seen = []
    fake_ai(monkeypatch, [{"likes": ["이적"], "less": [], "summary": "이적 확정 소식을 먼저"}], seen)
    t = taste.analyze({}, "축구", DOMAIN, shown, ["A"])
    assert seen[0][1] == {"field": "축구", "liked": [shown[0]], "skipped": [shown[1]]}
    assert t == {"likes": ["이적"], "less": [], "summary": "이적 확정 소식을 먼저"}
    assert taste.line(t) == "\n- 취향: 이적 확정 소식을 먼저 먼저 챙길 종류: 이적."
    assert taste.line(None) == "" and taste.line({"summary": "", "likes": []}) == ""


def test_pack_taste_saved_to_picks_and_used_in_brief(monkeypatch):
    it = sw.validate_interests({"chosen": ["sports-soccer"], "customs": [],
                                "picks": {"sports-soccer": {"scope": "kr", "taste": {"summary": "이적 먼저", "likes": ["이적·계약"], "less": "x"}}}})
    pk = it["interests"]["picks"]["sports-soccer"]
    assert pk == {"scope": "kr", "taste": "이적 먼저", "likes": ["이적·계약"], "less": []}
    text = sw.apply_interests(sw.base_text(), ["sports-soccer"], it["interests"]["picks"])  # conftest: 내 config 대신 example
    cfg = tomllib.loads(text)
    p = next(x for x in catalog.profiles() if x["id"] == "sports-soccer")
    assert catalog.scope(cfg, p) == "kr"  # ②에서 고른 범위가 세부보다 먼저
    assert "- 취향: 이적 먼저 먼저 챙길 종류: 이적·계약." in briefing.rules_for(cfg)["criteria"]
    view = interests.preset_view("sports-soccer", cfg)
    assert view["domain"]["id"] == "sports-soccer" and view["taste"]["summary"] == "이적 먼저"
    assert interests.preset_view("ai-coding", cfg)["domain"] is None  # AI 관심사는 지금 흐름 그대로


def test_custom_taste_saved_in_file():
    pid = interests.save_custom("보드게임", "kr", [], [], {"name": "보드게임", "tag": "보드게임", "summary": "", "discovery": "",
                                                       "criteria": ["광고는 버린다."]}, taste={"summary": "신작 먼저", "likes": ["신작"], "less": []})
    p = next(x for x in catalog.profiles() if x["id"] == pid)
    assert p["taste"] == {"summary": "신작 먼저", "likes": ["신작"], "less": []}
    assert taste.line(p["taste"]).startswith("\n- 취향: 신작 먼저")
