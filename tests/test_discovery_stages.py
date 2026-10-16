"""발굴 단계 (popme/discovery.py): 후보 풀 → 1단계 팔로워 → 2단계 꾸준함 → 화면, 승인 계정 정리 추천."""
from datetime import datetime, timedelta, timezone

from popme import discovery
from popme.db import DB

NOW = datetime.now(timezone.utc)


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()


class Maker:
    def __init__(self, db):
        self.db, self.n = db, 0

    def tweet(self, author, days, kind="post", ref=None, text="Claude Code 에이전트 팁", likes=20,
              platform="x", via="user:x", lang=None):
        self.n += 1
        self.db.upsert_tweets([{"id": f"t{self.n}", "author": author, "created_at": ago(days), "kind": kind,
                                "ref_author": ref, "text": text, "likes": likes, "via": via,
                                "platform": platform, "lang": lang}])

    def profile(self, handle, followers, platform="x", org=None, lang="en", verified=""):
        self.db.upsert_profiles([{"platform": platform, "handle": handle, "name": handle, "followers": followers,
                                  "org": org, "lang": lang, "verified": verified}])


def setup(tmp_path):
    db = DB(tmp_path / "t.db")
    m = Maker(db)
    m.profile("hwchase17", 100000, org="LangChain")
    m.profile("sydneyrunkle", 20000, org="LangChain")
    m.profile("simonw", 100000)
    m.profile("swyx", 100000)
    return db, m


def stage(db, handle):
    return db.q("SELECT stage, kind FROM candidates WHERE handle=?", handle)[0]


def test_same_company_counts_once(tmp_path):
    db, m = setup(tmp_path)
    m.profile("dev", 40000)
    m.tweet("hwchase17", 1, "retweet", "dev")
    m.tweet("sydneyrunkle", 2, "retweet", "dev")  # 둘 다 LangChain → 1곳
    discovery.update(db, ["hwchase17", "sydneyrunkle", "simonw"])
    assert not db.q("SELECT 1 FROM candidates WHERE handle='dev'")
    m.tweet("simonw", 1, "quote", "dev")  # 다른 출처 → 2곳
    discovery.update(db, ["hwchase17", "sydneyrunkle", "simonw"])
    assert stage(db, "dev")["kind"] == "global"


def test_own_company_staff_ignored(tmp_path):
    db, m = setup(tmp_path)
    m.profile("caspar", 40000, org="LangChain")  # LangChain 직원
    m.tweet("hwchase17", 1, "retweet", "caspar")
    m.tweet("sydneyrunkle", 1, "retweet", "caspar")
    m.tweet("simonw", 1, "retweet", "caspar")
    discovery.update(db, ["hwchase17", "sydneyrunkle", "simonw"])
    assert not db.q("SELECT 1 FROM candidates WHERE handle='caspar'")  # 독립 출처는 simonw 1곳뿐


def test_company_needs_three_orgs_over_three_weeks(tmp_path):
    db, m = setup(tmp_path)
    m.profile("acme", 50000, verified="Business")
    for who in ("hwchase17", "simonw", "swyx"):  # 3곳이지만 같은 주
        m.tweet(who, 1, "retweet", "acme")
    core = ["hwchase17", "simonw", "swyx"]
    discovery.update(db, core)
    assert not db.q("SELECT 1 FROM candidates WHERE handle='acme'")
    m.tweet("simonw", 10, "quote", "acme")
    m.tweet("swyx", 20, "quote", "acme")
    discovery.update(db, core)
    assert stage(db, "acme")["kind"] == "company"


def test_followers_by_kind_and_exemption(tmp_path):
    db, m = setup(tmp_path)
    for h, f in (("small", 20000), ("big", 40000)):
        m.profile(h, f)
        m.tweet("simonw", 1, "retweet", h)
        m.tweet("swyx", 1, "retweet", h)
    discovery.update(db, ["simonw", "swyx"])
    discovery.evaluate(db)
    assert stage(db, "small")["stage"] == "watch"  # 세계 개인 3만 미만, 1.5만 이상
    assert stage(db, "big")["stage"] == "pass"  # 세계 개인은 꾸준함 면제
    assert [c["handle"] for c in discovery.candidates(db)] == ["big"]


def test_korean_account_needs_consistency_check(tmp_path):
    db, m = setup(tmp_path)
    m.profile("kodev", 20000, lang="ko")
    for _ in range(2):
        m.tweet("kodev", 1, via="search:0", lang="ko", likes=300)
    discovery.update(db, ["simonw"])
    discovery.evaluate(db)
    assert stage(db, "kodev")["stage"] == "pool"
    assert discovery.needs_check(db, None, "x") == ["kodev"]
    for d in (3, 9, 16, 23):  # 프로필 확인으로 받은 최근 글: 4주 동안 매주
        m.tweet("kodev", d, via="check:kodev")
    db.mark_checked(["kodev"])
    discovery.evaluate(db)
    assert stage(db, "kodev")["stage"] == "pass"
    assert discovery.needs_check(db, None, "x") == []


def test_consistency_fails_on_dormant_and_offtopic(tmp_path):
    db, m = setup(tmp_path)
    s = discovery.settings(None)
    for d in (2, 9, 16, 23):
        m.tweet("food", d, text="오늘 점심 맛집")
    ok, why = discovery.consistency(db, "x", "food", s)
    assert not ok and "AI·개발 글 0%" in why
    m.tweet("gone", 60)
    ok, why = discovery.consistency(db, "x", "gone", s)
    assert not ok and "최근 30일 글 없음" in why


def test_review_flags_dormant_approved(tmp_path):
    db, m = setup(tmp_path)
    db.upsert_candidate("threads/quiet", "q", "r", 1, "threads", "threads")
    db.upsert_candidate("threads/busy", "b", "r", 1, "threads", "threads")
    db.set_candidate_status("threads/quiet", "added")
    db.set_candidate_status("threads/busy", "added")
    m.tweet("quiet", 40, platform="threads", via="th-user:quiet")
    for d in (2, 9, 16, 23):
        m.tweet("busy", d, platform="threads", via="th-user:busy")
    discovery.review(db)
    assert [c["handle"] for c in discovery.to_review(db)] == ["threads/quiet"]  # 마지막 글이 4주보다 오래됨


def test_dropped_then_requalified(tmp_path):
    db, m = setup(tmp_path)
    m.profile("dev", 40000)
    m.tweet("simonw", 1, "retweet", "dev")
    m.tweet("swyx", 1, "retweet", "dev")
    discovery.update(db, ["simonw", "swyx"])
    discovery.update(db, ["simonw"])  # swyx가 핵심 계정에서 빠지면 1곳 → 풀에서 빠짐
    assert stage(db, "dev")["stage"] == "gone"
    discovery.update(db, ["simonw", "swyx"])
    assert stage(db, "dev")["stage"] == "pool"


def test_briefing_verdicts_show_on_candidates(tmp_path):
    """브리핑 발굴 카드의 판단(추천·비추천·보류)이 후보에 붙어 발굴 탭에 같은 이유로 보인다."""
    db = DB(tmp_path / "t.db")
    db.upsert_candidate("charliermarsh", "Charlie", "서로 다른 회사 2곳의 핵심 계정(@a, @b)이 리포스트·인용·답글", 10, "x", "global")
    db.upsert_candidate("pequityresearch", "P", "", 9, "x", "global")
    db.upsert_candidate("threads/fofr.ai", "F", "", 8, "threads", "threads")
    items = [
        {"cat": "발굴", "title": "@charliermarsh — Python 도구 ty 소식 볼 만함", "body": "개발 도구를 만드는 쪽",
         "links": [{"label": "X 글", "url": "https://x.com/zeeg/status/1"}]},
        {"cat": "발굴", "title": "@pequityresearch — 비추천: 주식 리서치 위주", "body": "투자 정보 위주"},
        {"cat": "발굴", "title": "@fofr.ai — 보류: 대표글이 없어 판단 불가", "body": ""},
        {"cat": "발굴", "title": "@nobody — 모르는 사람", "body": "후보에 없으면 무시"},
        {"cat": "업데이트", "title": "@charliermarsh — 발굴 카드가 아니면 무시", "body": ""},
    ]
    discovery.remember_verdicts(db, items, "2026-10-16")
    rows = {c["handle"]: discovery.for_view(c) for c in db.q("SELECT * FROM candidates")}
    assert rows["charliermarsh"]["ai"] == {"verdict": "추천", "title": "Python 도구 ty 소식 볼 만함", "body": "개발 도구를 만드는 쪽",
                                           "date": "2026-10-16", "link": "https://x.com/zeeg/status/1"}
    assert (rows["pequityresearch"]["ai"]["verdict"], rows["pequityresearch"]["ai"]["title"]) == ("비추천", "주식 리서치 위주")
    assert (rows["threads/fofr.ai"]["ai"]["verdict"], rows["threads/fofr.ai"]["ai"]["title"]) == ("보류", "대표글이 없어 판단 불가")


def test_backfill_uses_latest_briefing(tmp_path):
    import json
    db = DB(tmp_path / "t.db")
    db.upsert_candidate("someone", "S", "", 1, "x", "global")
    for date, title in [("2026-10-14", "@someone — 비추천: 옛 판단"), ("2026-10-16", "@someone — 새 판단")]:
        db.save_briefing(date, "", json.dumps({"items": [{"cat": "발굴", "title": title, "body": ""}]}, ensure_ascii=False))
    discovery.backfill_verdicts(db)
    c = discovery.for_view(db.q("SELECT * FROM candidates")[0])
    assert (c["ai"]["verdict"], c["ai"]["title"], c["ai"]["date"]) == ("추천", "새 판단", "2026-10-16")


def test_tags_shorten_criteria():
    texts = lambda c: [t["text"] for t in discovery.tags(c)[0]]
    assert texts({"check_note": "세계 개인 팔로워 62,177명 · 꾸준함 검사 면제",
                  "reason": "서로 다른 회사 2곳의 핵심 계정(@USAnt_IDEA, @elonmusk)이 리포스트·인용·답글"}) == \
        ["세계 개인", "팔로워 6.2만", "꾸준함 면제", "회사 2곳이 언급"]
    assert texts({"check_note": "국내 X 팔로워 8,100명 · 꾸준함 통과: 30일 글 12개 · 4주 활동 · AI·개발 글 80%",
                  "reason": "한국어 글 3개, 반응 합계 ♥1,234"}) == \
        ["국내 X", "팔로워 8,100", "꾸준함 ✓", "30일 글 12개", "4주 활동", "AI·개발 글 80%", "한국어 글 3개 ♥1,234"]
    assert texts({"check_note": "세계 기업 팔로워 120,000명 · 꾸준함 검사 면제",
                  "reason": "서로 다른 회사 3곳의 핵심 계정(@a, @b, @c)이 4주에 걸쳐 7번 리포스트·인용 · 떠오르는 중(30일 팔로워 +15%)"}) == \
        ["세계 기업", "팔로워 12만", "꾸준함 면제", "회사 3곳이 4주간 7번 언급", "떠오르는 중 +15%"]
    t, quote = discovery.tags({"check_note": "꾸준함 미달: 30일 글 2개, 최근 4주 중 1주 활동",
                               "reason": "Threads 검색·태그 3곳 등장(MCP, 바이브코딩, AI), 글 5개, ♥1,234 · 대표글: 클로드 코드 팁 모음"})
    assert [(x["text"], x["tone"]) for x in t] == [("꾸준함 미달", "bad"), ("30일 글 2개", "bad"), ("최근 4주 중 1주 활동", "bad"),
                                                  ("검색·태그 3곳 등장", ""), ("글 5개 ♥1,234", "")]
    assert t[3]["tip"] == "MCP, 바이브코딩, AI" and quote == "클로드 코드 팁 모음"
    assert texts({"check_note": "처음 보는 모양의 아주 긴 판정 문장이 여기에 들어가면 잘린다", "reason": ""})[0].endswith("…")
