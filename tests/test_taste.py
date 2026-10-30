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
                  ("세부:KBO", "프로야구", None), ("세부:ETF", "ETF 야구", None), ("분야", "야구", None)]  # 정해 둔 검색어가 없으면 분야를 붙인다
    assert ("세부:MLB", "MLB", "world") not in taste.queries("야구", subs, scope="kr")  # 한국어만이면 영어 기사는 안 찾는다
    custom = taste.queries("캠핑", [{"label": "할인·특가 행사"}, {"label": "캠핑장 예약 소식"}, {"label": "에깅", "q": "에깅 낚시"}],
                           [{"label": "헬리녹스", "en": "Helinox"}], names={"ko": "캠핑", "en": "camping", "aliases": ["차박"]})
    assert custom == [("대상:헬리녹스", "헬리녹스 캠핑", None), ("대상:헬리녹스", "Helinox camping", "world"),  # 영어엔 분야 영어 이름
                      ("세부:할인·특가 행사", "할인 특가 행사 캠핑", None),
                      ("세부:캠핑장 예약 소식", "캠핑장 예약", None),  # 일반어(소식)는 떼고, 이름에 분야가 있으면 붙이지 않는다
                      ("세부:에깅", "에깅 낚시", None),  # 질문 때 AI가 낸 검색어(q)
                      ("분야", "캠핑", None), ("분야", "차박", None), ("분야", "camping", "world")]  # 분야의 다른 말·영어 이름
    swift = taste.queries("Taylor Swift", [{"label": "음악·앨범", "q": "테일러 스위프트 앨범"}], names={"ko": "테일러 스위프트", "en": "Taylor Swift"})
    assert swift[0] == ("세부:음악·앨범", "테일러 스위프트 앨범", None) and ("분야", "테일러 스위프트", None) in swift  # 영어 이름 분야도 한국 기사는 한국어로
    assert taste.queries("국내주식", targets=[{"label": "삼성전자", "news": "삼성전자 주가"}])[0][1] == "삼성전자"  # 시세 자동기사만 오는 꼬리는 뗀다


def test_pool_interleaves_dedupes_and_filters(monkeypatch):
    found = {
        "이정후": [item("이정후, 시즌 3호 홈런", "2026-10-28"), item("이정후 시즌 3호 홈런 쳤다", "2026-10-29", "스포츠조선")],
        "메이저리그": [item("다저스 월드시리즈 우승"), item("[광고] 야구 장갑 최저가")],
        "야구": [item(t, outlet="같은매체") for t in ("한화 가을야구 확정", "롯데 감독 교체", "두산 FA 계약", "삼성 신인 지명", "KIA 우승 축하")],
    }
    asked = []
    monkeypatch.setattr(interests, "research_items", lambda q, limit, days, loc=None: asked.append((q, days, loc)) or found.get(q, []))
    found["이정후 야구"] = found.pop("이정후") + [item("서울 아파트 매물 30% 늘어", "2026-10-30")]
    got = taste.pool("야구", [{"label": "MLB", "news": "메이저리그"}], [{"label": "이정후"}], "kr", exclude=["다저스, 월드시리즈 우승 - 다른매체"])
    assert ("이정후 야구", taste.DAYS, None) in asked  # 정해 둔 검색어가 없는 대상은 분야를 붙여
    assert [(g["group"], g["title"].split(" - ")[0]) for g in got] == [
        ("대상:이정후", "이정후 시즌 3호 홈런 쳤다"),  # 최근 것 먼저, 같은 사건(매체만 다름)은 하나만
        ("분야", "서울 아파트 매물 30% 늘어"),  # 이름이 없는 기사는 대상이 아니라 분야로
        ("분야", "한화 가을야구 확정"), ("분야", "롯데 감독 교체"), ("분야", "두산 FA 계약")]  # 이미 보여 준 사건·광고는 빼고, 한 매체는 3개까지


def test_final_picks_drop_same_event(monkeypatch):
    heads = [{"title": t, "group": "분야", "published": ""} for t in
             ("지방직 7급 경쟁률 34.5대 1", "올해 지방직 7급 34.5대1…지원자 줄어", "국가직 9급 합격선 발표")]
    monkeypatch.setattr(taste, "pool", lambda *a, **k: heads)
    fake_ai(monkeypatch, [{"picks": [{"id": 0, "category": "시험"}, {"id": 1, "category": "통계"}, {"id": 2, "category": "합격"}]}], [])
    assert [h["title"] for h in taste.headlines({}, "공무원 시험", count=3)] == [heads[0]["title"], heads[2]["title"]]  # 숫자+이름이 겹치면 같은 사건


def test_same_event_ignores_particles_and_spacing():
    a = taste._tokens("‘데뷔 10주년’ 블랙핑크, K팝 걸그룹 첫 옥관문화훈장 - 연합뉴스")
    b = taste._tokens("10주년 블랙핑크, 옥관문화훈장 수훈에 「팬들 덕분」 - 스타뉴스")
    assert taste.same_event(a, b)
    assert taste.same_event(taste._tokens("외국인 9월 국내 주식 19조 순매도"), taste._tokens("외국인, 9월 국내주식 19조 순매도…역대 최대"))
    assert not taste.same_event(taste._tokens("삼성, KIA 6대4로 꺾고 2연승"), taste._tokens("롯데 신임 감독에 이동욱 선임"))


def test_spam_and_outlets_filtered(monkeypatch):
    junk = [item("삼성전자 주가 전망 2026: 증권가 목표주가 27만원", outlet="Mitrade"), item("KB금융 147,400원으로 1% 상승"),
            item("Chip Stocks to Buy Now", outlet="Yahoo"), item("[설명] 부동산 대책 지라시는 사실 아님"), item("반도체 수출 사상 최대")]
    monkeypatch.setattr(interests, "research_items", lambda q, limit, days, loc=None: junk if q == "주식" else [])
    assert [g["title"].split(" - ")[0] for g in taste.pool("주식")] == ["반도체 수출 사상 최대"]


NEWS5 = ["한화 가을야구 확정", "롯데 신임 감독 선임", "두산 FA 계약 발표", "삼성 신인 1순위 지명", "KIA 우승 축하 행사"]


def test_editor_asked_again_when_crowded_and_best_answer_used(monkeypatch):
    heads = [{"title": t, "group": "분야", "published": ""} for t in NEWS5] + [
        {"title": "이정후 홈런", "group": "대상:이정후", "published": "2026-10-29T00:00:00+00:00"}]
    monkeypatch.setattr(taste, "pool", lambda *a, **k: heads)
    crowded = {"picks": [{"id": i, "category": "경기"} for i in range(5)]}
    good = {"picks": [{"id": i, "category": c} for i, c in enumerate(["경기", "이적", "부상", "행정"])] + [{"id": 5, "category": "기타"}]
            + [{"id": 99, "category": "이적"}, {"id": 1, "category": "이적"}]}
    seen = []
    fake_ai(monkeypatch, [crowded, good], seen)
    out = taste.headlines({}, "축구", DOMAIN, "kr", count=5, exclude=["예전 헤드라인"])
    fb = seen[1][1]["feedback"]
    assert len(seen) == 2 and "분야 기사가 한 종류에 몰렸다: 경기" in fb
    assert seen[0][1]["chosen"] == ["이정후"] and seen[0][1]["shown_before"] == ["예전 헤드라인"]
    h5 = seen[0][1]["headlines"][5]
    assert h5["title"] == "이정후 홈런" and h5["group"] == "대상:이정후" and h5["ago"].endswith(("전", "오늘"))  # 며칠 전인지
    assert "이 분야를 오래 본 사람의 눈으로" in seen[0][0] and "소식 종류: 경기" in seen[0][0]  # 분야 페르소나의 눈으로
    assert [h["title"] for h in out] == NEWS5[:4] + ["이정후 홈런"]  # 없는 번호·같은 번호는 걸러진다
    assert out[4] == {"title": "이정후 홈런", "category": "", "group": "대상:이정후"}  # 「기타」는 꼬리표 없이


def test_problems():
    cats = ["경기", "이적", "부상", "행정"]
    two = [{"id": 0, "category": "경기"}, {"id": 1, "category": "이적"}]
    assert taste.problems(two, HEADS[:2], cats, 10) == []  # 모자람은 묻지 않고 코드가 채운다
    assert "같은 헤드라인을 두 번 골랐다" in taste.problems([{"id": 0, "category": ""}] * 2, HEADS[:2], cats, 2)
    many = [f"종류{i}" for i in range(10)]  # 종류가 넉넉하면 둘도 몰림
    assert any("몰렸다" in x for x in taste.problems([{"id": 0, "category": "종류1"}, {"id": 1, "category": "종류1"}], HEADS, many, 2))
    chosen = [{"title": f"K리그 {i}", "group": "세부:K리그", "published": ""} for i in range(3)]
    assert taste.problems([{"id": i, "category": "경기"} for i in range(3)], chosen, many, 3) == []  # 고른 세부가 둘 이상은 괜찮다
    assert any("기타" in x for x in taste.problems([{"id": i, "category": "기타"} for i in range(3)], HEADS, cats, 3))


def test_fill_from_chosen_first():
    heads = [{"title": t, "group": g, "published": ""} for t, g in
             (("한화 가을야구 확정", "분야"), ("롯데 감독 교체", "분야"), ("한화, 가을야구 확정 지었다", "분야"), ("이정후 홈런", "대상:이정후"))]
    got = taste.fill([{"title": "한화 가을야구 확정", "category": "경기", "group": "분야"}], heads, 3)
    assert [g["title"] for g in got] == ["한화 가을야구 확정", "이정후 홈런", "롯데 감독 교체"]  # 고른 대상 기사부터, 같은 사건은 빼고


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
    assert all_liked == {"likes": [], "less": [], "summary": "전반을 고르게"}  # 거의 다 누르면 AI 없이, 우선 종류도 자르지 않는다
    seen = []
    fake_ai(monkeypatch, [{"likes": ["이적"], "less": ["행정", "경기"], "summary": "이적 소식을 먼저, 분석 글은 줄여서"}], seen)
    t = taste.analyze({}, "축구", DOMAIN, SHOWN, ["A"], ["K리그"])
    assert seen[0][1]["chosen"] == ["K리그"] and seen[0][1]["liked"] == [SHOWN[0]] and len(seen[0][1]["skipped"]) == 6
    assert t == {"likes": ["이적"], "less": [], "summary": "이적 소식을 먼저"}  # 하나만 눌렀으면 줄이지 않고, 줄이는 말도 뺀다
    fake_ai(monkeypatch, [{"likes": ["이적", "경기"], "less": ["행정", "경기", "부상"], "summary": "이적과 경기를 먼저"}], seen)
    t = taste.analyze({}, "축구", DOMAIN, SHOWN, ["A", "D", "E"], ["부상 소식"])
    assert t["less"] == ["행정"]  # 세 번 안 누른 행정만 (누른 경기·③에서 고른 부상은 줄이지 않는다)


def test_summary_never_contradicts_likes(monkeypatch):
    fake_ai(monkeypatch, [{"likes": ["신제품·공개"], "less": ["레고랜드"], "summary": "레고랜드 소식은 줄이고"}], [])
    t = taste.analyze({}, "레고", None, SHOWN, ["A", "D"])
    assert t["summary"] == "신제품·공개 소식을 먼저" and t["less"] == []  # 줄이는 말을 지워 비면 누른 종류로
    assert "우선 종류" not in taste.line({"summary": "전반을 고르게", "likes": ["이적"]})


def test_brief_line():
    assert taste.line({"likes": ["이적·계약", "경기 결과·순위"], "less": ["행정"], "summary": "이적 확정 소식을 먼저."}) == (
        "\n- 취향: 이적 확정 소식을 먼저. 우선 종류: 이적·계약, 경기 결과·순위. 줄일 종류(큰 소식만): 행정.")
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
    assert "- 취향: 이적 먼저. 우선 종류: 이적·계약." in briefing.rules_for(cfg)["criteria"]
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


def test_new_field_needs_field_word_when_query_lacks_it(monkeypatch):
    found = {"메리 베리": [item("지창욱 로코 ‘메리 베리 러브’ 평점 9.6"), item("메리 베리의 베이킹 쇼 새 시즌")],
             "레고 테크닉": [item("레고 테크닉 신제품 공개")]}
    monkeypatch.setattr(interests, "research_items", lambda q, limit, days, loc=None: found.get(q, []))
    names = {"ko": "홈베이킹", "en": "home baking", "aliases": ["베이킹", "제빵"]}
    got = taste.pool("홈베이킹", targets=[{"label": "메리 베리", "q": "메리 베리"}], names=names)
    assert [g["title"].split(" - ")[0] for g in got] == ["메리 베리의 베이킹 쇼 새 시즌"]  # 다른 뜻(드라마)은 뺀다
    got = taste.pool("레고", targets=[{"label": "레고 테크닉", "q": "레고 테크닉"}], names={"ko": "레고"})
    assert len(got) == 1  # 검색어에 분야 낱말이 있으면 그대로
