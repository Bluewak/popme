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
