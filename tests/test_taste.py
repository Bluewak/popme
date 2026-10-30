"""④ 취향 문답 (popme/taste.py): 편집자가 서로 다른 종류 10개 → 몰리면 다시 → 누른 것으로 취향 분석가 → 브리핑 지시문·저장."""
import json
import tomllib

from popme import briefing, catalog, interests, persona, taste
from popme import setup_window as sw

HEADS = [{"title": f"헤드라인 {i}", "group": "분야", "published": ""} for i in range(12)]
DOMAIN = {"id": "sports-soccer", "persona": {"name": "가명"}, "categories": [{"name": n, "desc": ""} for n in ("경기", "이적", "부상", "행정")]}


def fake_ai(monkeypatch, answers, seen):
    answers = iter(answers)

    def ask(cfg, system, user, timeout=0):
        seen.append((system, json.loads(user.rsplit("<input>", 1)[1].split("</input>")[0])))
        return json.dumps(next(answers), ensure_ascii=False)
    monkeypatch.setattr(persona.llm, "ask", ask)


def item(title, day="2026-10-29", outlet="연합뉴스"):
    return {"title": f"{title} - {outlet}", "published": f"{day}T00:00:00+00:00", "outlet": outlet}


def test_queries_put_targets_and_subs_first():
    subs = [{"label": "MLB", "news": "메이저리그", "en": "MLB", "region": "world"}, {"label": "KBO", "news": "프로야구"}, "ETF"]
    qs = taste.queries("야구", subs, [{"label": "이정후", "news": "이정후"}], "world")
    assert qs == [("대상:이정후", "이정후", None), ("세부:MLB", "메이저리그", None), ("세부:MLB", "MLB", "world"),
                  ("세부:KBO", "프로야구", None), ("세부:ETF", "야구 ETF", None), ("분야", "야구", None)]  # 짧은 세부 이름엔 분야를 붙인다
    assert ("세부:MLB", "MLB", "world") not in taste.queries("야구", subs, scope="kr")  # 한국어만이면 영어 기사는 안 찾는다


def test_pool_interleaves_dedupes_and_filters(monkeypatch):
    found = {
        "이정후": [item("이정후, 시즌 3호 홈런", "2026-10-28"), item("이정후 시즌 3호 홈런 쳤다", "2026-10-29", "스포츠조선")],
        "메이저리그": [item("다저스 월드시리즈 우승"), item("[광고] 야구 장갑 최저가")],
        "야구": [item(t, outlet="같은매체") for t in ("한화 가을야구 확정", "롯데 감독 교체", "두산 FA 계약", "삼성 신인 지명", "KIA 우승 축하")],
    }
    asked = []
    monkeypatch.setattr(interests, "research_items", lambda q, limit, days, loc=None: asked.append((q, days, loc)) or found.get(q, []))
    got = taste.pool("야구", [{"label": "MLB", "news": "메이저리그"}], [{"label": "이정후"}], "kr", exclude=["다저스, 월드시리즈 우승 - 다른매체"])
    assert ("이정후", taste.DAYS, None) in asked
    assert [(g["group"], g["title"].split(" - ")[0]) for g in got] == [
        ("대상:이정후", "이정후 시즌 3호 홈런 쳤다"),  # 최근 것 먼저, 같은 사건(매체만 다름)은 하나만
        ("분야", "한화 가을야구 확정"), ("분야", "롯데 감독 교체"), ("분야", "두산 FA 계약")]  # 이미 보여 준 사건·광고는 빼고, 한 매체는 3개까지


def test_editor_asked_again_when_crowded_or_chosen_missing(monkeypatch):
    heads = HEADS[:5] + [{"title": "이정후 홈런", "group": "대상:이정후", "published": ""}]
    monkeypatch.setattr(taste, "pool", lambda *a, **k: heads)
    crowded = {"picks": [{"id": i, "category": "경기"} for i in range(5)]}
    good = {"picks": [{"id": i, "category": c} for i, c in enumerate(["경기", "이적", "부상", "행정"])] + [{"id": 5, "category": "기타"}]
            + [{"id": 99, "category": "이적"}, {"id": 1, "category": "이적"}]}
    seen = []
    fake_ai(monkeypatch, [crowded, good], seen)
    out = taste.headlines({}, "축구", DOMAIN, "kr", count=5, exclude=["예전 헤드라인"])
    fb = seen[1][1]["feedback"]
    assert len(seen) == 2 and "한 종류에 몰렸다: 경기" in fb and "이정후 소식이 목록에 있는데 하나도 안 골랐다" in fb
    assert seen[0][1]["chosen"] == ["이정후"] and seen[0][1]["shown_before"] == ["예전 헤드라인"]
    assert seen[0][1]["headlines"][5] == {"id": 5, "title": "이정후 홈런", "group": "대상:이정후"}
    assert "이 분야를 오래 본 사람의 눈으로" in seen[0][0] and "소식 종류: 경기" in seen[0][0]  # 분야 페르소나의 눈으로
    assert [h["title"] for h in out] == ["헤드라인 0", "헤드라인 1", "헤드라인 2", "헤드라인 3", "이정후 홈런"]  # 없는 번호·같은 번호는 걸러진다
    assert out[4] == {"title": "이정후 홈런", "category": "", "group": "대상:이정후"}  # 「기타」는 꼬리표 없이


def test_problems():
    cats = ["경기", "이적", "부상", "행정"]
    two = [{"id": 0, "category": "경기"}, {"id": 1, "category": "이적"}]
    assert taste.problems(two, HEADS[:2], cats, 10) == []
    assert "같은 헤드라인을 두 번 골랐다" in taste.problems([{"id": 0, "category": ""}] * 2, HEADS[:2], cats, 2)
    assert any("개뿐" in x for x in taste.problems([{"id": 0, "category": ""}], HEADS, cats, 10))
    many = [f"종류{i}" for i in range(10)]  # 종류가 넉넉하면 둘도 몰림
    assert any("몰렸다" in x for x in taste.problems([{"id": 0, "category": "종류1"}, {"id": 1, "category": "종류1"}], HEADS, many, 2))
    assert any("기타" in x for x in taste.problems([{"id": i, "category": "기타"} for i in range(3)], HEADS, cats, 3))


def test_editor_failure_falls_back(monkeypatch):
    monkeypatch.setattr(taste, "pool", lambda *a, **k: HEADS)
    monkeypatch.setattr(persona.llm, "ask", lambda *a, **k: "JSON 아님")
    assert [h["title"] for h in taste.headlines({}, "축구", count=3)] == ["헤드라인 0", "헤드라인 1", "헤드라인 2"]
    monkeypatch.setattr(taste, "pool", lambda *a, **k: [])
    assert taste.headlines({}, "축구") == []


SHOWN = [{"title": t, "category": c} for t, c in
         (("A", "이적"), ("B", "행정"), ("C", "행정"), ("D", "경기"), ("E", "이적"), ("F", "부상"), ("G", "행정"))]


def test_analyze_needs_evidence_to_reduce(monkeypatch):
    assert taste.analyze({}, "축구", None, SHOWN, []) == {"likes": [], "less": [], "summary": "전반을 고르게"}  # 안 누르면 AI 없이
    all_liked = taste.analyze({}, "축구", None, SHOWN, [h["title"] for h in SHOWN[:6]])
    assert all_liked["summary"] == "전반을 고르게" and not all_liked["less"]  # 거의 다 누르면 AI 없이
    seen = []
    fake_ai(monkeypatch, [{"likes": ["이적"], "less": ["행정", "경기"], "summary": "이적 소식을 먼저, 행정은 큰 것만"}], seen)
    t = taste.analyze({}, "축구", DOMAIN, SHOWN, ["A"], ["K리그"])
    assert seen[0][1]["chosen"] == ["K리그"] and seen[0][1]["liked"] == [SHOWN[0]] and len(seen[0][1]["skipped"]) == 6
    assert t == {"likes": ["이적"], "less": [], "summary": "이적 소식을 먼저"}  # 하나만 눌렀으면 줄이지 않고, 줄이는 말도 뺀다
    fake_ai(monkeypatch, [{"likes": ["이적", "경기"], "less": ["행정", "경기", "부상"], "summary": "이적과 경기를 먼저"}], seen)
    t = taste.analyze({}, "축구", DOMAIN, SHOWN, ["A", "D", "E"], ["부상 소식"])
    assert t["less"] == ["행정"]  # 세 번 안 누른 행정만 (누른 경기·③에서 고른 부상은 줄이지 않는다)


def test_brief_line():
    assert taste.line({"likes": ["이적·계약", "경기 결과·순위"], "less": ["행정"], "summary": "이적 확정 소식을 먼저."}) == (
        "\n- 취향: 이적 확정 소식을 먼저. 먼저 챙길 종류: 이적·계약, 경기 결과·순위. 줄일 종류(큰 소식만): 행정.")
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
    assert "- 취향: 이적 먼저. 먼저 챙길 종류: 이적·계약." in briefing.rules_for(cfg)["criteria"]
    view = interests.preset_view("sports-soccer", cfg)
    assert view["domain"]["id"] == "sports-soccer" and view["taste"]["summary"] == "이적 먼저"
    assert interests.preset_view("ai-coding", cfg)["domain"] is None  # AI 관심사는 지금 흐름 그대로


def test_custom_taste_saved_in_file():
    pid = interests.save_custom("보드게임", "kr", [], [], {"name": "보드게임", "tag": "보드게임", "summary": "", "discovery": "",
                                                       "criteria": ["광고는 버린다."]}, taste={"summary": "신작 먼저", "likes": ["신작"], "less": []})
    p = next(x for x in catalog.profiles() if x["id"] == pid)
    assert p["taste"] == {"summary": "신작 먼저", "likes": ["신작"], "less": []}
    assert taste.line(p["taste"]).startswith("\n- 취향: 신작 먼저")


def test_tidy_titles():
    assert taste.tidy("리센느 컴백 - https://www.2news.co.kr/") == "리센느 컴백 - 2news.co.kr"
    assert taste.tidy("삼성 실적 - 머니투데이 - 머니투데이") == "삼성 실적 - 머니투데이"


def test_health_topic_options_are_kept():
    raw = {"type": "취미·생활", "questions": [{"slot": "범위", "text": "어떤 소식이 궁금하세요?",
                                              "options": [{"label": "건강·노견 관리"}, {"label": "산책·여행"}, {"label": "지지 정당"}]}]}
    q = interests.clean_questions(raw, "both")["questions"][0]
    assert [o["label"] for o in q["options"]] == ["건강·노견 관리", "산책·여행"]  # 분야 주제인 건강은 남기고, 개인 정치 성향만 뺀다
