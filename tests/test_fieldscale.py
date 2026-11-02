"""분야마다 발굴 기준 (popme/fieldscale.py): 다른 분야(AI) 숫자를 빌리지 않고, 그 분야 유명 계정에서 타고 내려간 그 분야 자체 분포로."""
import json
from datetime import datetime, timedelta, timezone

from popme import discovery, domains, fieldcard, fieldscale, interests, measure, persona
from popme.db import DB

NOW = datetime.now(timezone.utc)


def user(f, lang="ko", verified=""):
    return {"followers": f, "lang": lang, "verified": verified}


def test_field_pool_follows_famous_accounts_down():
    core = ["seed1", "seed2"]
    edges = [("seed1", "reporter", "청약 경쟁률 정리"), ("seed2", "reporter", "전세 시장"),  # 둘이 반응 → 분야 계정
             ("seed1", "analyst", "이번 주 분양 일정"),                                    # 하나지만 분야 낱말 → 분야 계정
             ("seed1", "idol", "콘서트 너무 좋았다"),                                       # 하나·분야 밖 → 뺌
             ("other", "x9", "청약")]                                                     # 출발 계정이 아닌 반응 → 뺌
    users = {"seed1": user(90000), "seed2": user(40000), "reporter": user(3000), "analyst": user(1200),
             "idol": user(5_000_000), "x9": user(10)}
    pool = fieldscale.field_pool(core, edges, users, ["청약", "분양", "전세"])
    assert sorted(p["handle"] for p in pool) == ["analyst", "reporter", "seed1", "seed2"]


def test_thresholds_from_the_fields_own_distribution():
    small = [{"followers": f, "kind": "ko"} for f in (100, 300, 500, 800, 1000, 1500, 2000, 9000)]
    t = fieldscale.thresholds(small)
    assert t["ko"] == [fieldscale._round(fieldscale._quantile([p["followers"] for p in small], 0.9)),
                       fieldscale._round(fieldscale._quantile([p["followers"] for p in small], 0.75))]
    assert t["ko"][0] < discovery.DEFAULTS["followers"]["ko"][0]  # 작은 분야는 그 분야 분포대로 (AI 1.5만이 아니라)
    assert "global" not in t and "company" not in t  # 해외 계정이 없으면 해외 기준은 비워 둔다 (국내 분포를 빌리지 않음)
    assert fieldscale.thresholds(small[:5]) is None  # 표본이 모자라면 조사 중
    abroad = [{"followers": f, "kind": "global"} for f in (1000, 5000, 9000, 20000, 50000, 80000, 120000, 400000)]
    t = fieldscale.thresholds(abroad)
    assert "ko" not in t and t["company"] == t["global"]  # 국내 계정이 없으면 국내 기준은 조사 중, 기업은 같은 분야 해외 분포로


def test_topic_words_from_card():
    d = {"categories": [{"name": "신작·펀딩"}, {"name": "한글판 재판"}], "vocabulary": [{"word": "유로게임"}],
         "must_know": [{"name": "보드게임긱(BGG)"}]}
    words = fieldscale.topic_words(d, {"ko": "보드게임", "aliases": ["보드 게임"]})
    assert {"신작", "펀딩", "한글판", "유로게임", "보드게임긱", "보드게임", "보드 게임"} <= set(words)


def test_rules_for_prefers_calibration(monkeypatch, tmp_path):
    monkeypatch.setattr(fieldscale, "CALIBRATION", tmp_path / "field_rules.json")
    monkeypatch.setattr(domains, "load", lambda pid: {"info": {"followers": {"ko": [2000, 900]}, "topic_words": ["청약", "전세"]}})
    r = fieldscale.rules_for("invest-real-estate")
    assert r["followers"] == {"ko": [2000, 900]} and r["topic"].search("전세 사기 대책")
    (tmp_path / "field_rules.json").write_text(json.dumps({"invest-real-estate": {"followers": {"ko": [3000, 1000]}}}), encoding="utf-8")
    assert fieldscale.rules_for("invest-real-estate")["followers"] == {"ko": [3000, 1000]}  # 매주 보정한 값이 먼저


def test_packs_ship_topic_words_but_no_borrowed_thresholds():
    for pid in ("sports-soccer", "invest-real-estate", "ent-kpop"):
        info = domains.load(pid)["info"]
        assert info["topic_words"] and "followers" not in info  # 팔로워 기준은 쓰면서 그 분야 반응 관계로


def setup(tmp_path):
    db = DB(tmp_path / "t.db")
    for h in ("koreal", "aifan"):
        db.upsert_profiles([{"platform": "x", "handle": h, "name": h, "followers": 2500, "lang": "ko"}])
    return db


def tweet(db, n, author, text, days=1, likes=20):
    db.upsert_tweets([{"id": f"t{n}", "author": author, "created_at": (NOW - timedelta(days=days)).isoformat(), "kind": "post",
                       "text": text, "likes": likes, "via": "user:x", "platform": "x", "lang": "ko"}])


def test_candidate_judged_by_its_field(monkeypatch, tmp_path):
    db = setup(tmp_path)
    rules = {"invest-real-estate": {"followers": {"ko": [2000, 900]}, "topic": __import__("re").compile("청약|전세|분양")}}
    monkeypatch.setattr(fieldscale, "rules_for", lambda pid: rules.get(pid, {"followers": None, "topic": None}))
    db.upsert_candidate("koreal", "부동산 기자", "r", 5, "x", "ko", "invest-real-estate")
    db.upsert_candidate("aifan", "AI 쪽", "r", 5, "x", "ko", None)
    db.upsert_candidate("newfield", "새 분야", "r", 5, "x", "ko", "custom-board")
    for i in range(8):
        tweet(db, i, "koreal", "이번 주 청약 경쟁률과 전세 시장 정리", days=i * 3)
        tweet(db, 100 + i, "aifan", "이번 주 청약 경쟁률과 전세 시장 정리", days=i * 3)
    db.mark_checked(["koreal", "aifan"])
    discovery.evaluate(db, {})
    st = {r["handle"]: r for r in db.q("SELECT handle, stage, check_note FROM candidates")}
    assert st["koreal"]["stage"] == "pass" and "분야 글" in st["koreal"]["check_note"]  # 부동산 기준 2천이면 통과, 분야 낱말로 꾸준함
    assert st["aifan"]["stage"] == "fail" and "기준 15,000" in st["aifan"]["check_note"]  # 관심사를 모르면(AI 발굴) AI 기준
    assert st["newfield"]["stage"] == "pool" and "분야 기준 조사 중" in st["newfield"]["check_note"]  # AI 기준을 빌리지 않고 미룸


def test_update_remembers_interest(monkeypatch, tmp_path):
    db = setup(tmp_path)
    monkeypatch.setattr(discovery, "_interest_of", lambda cfg: {"core1": "invest-real-estate", "core2": "invest-real-estate"})
    for n, (a, org) in enumerate((("core1", "A사"), ("core2", "B사"))):
        db.upsert_profiles([{"platform": "x", "handle": a, "name": a, "followers": 1000, "org": org}])
        db.upsert_tweets([{"id": f"r{n}", "author": a, "created_at": NOW.isoformat(), "kind": "retweet", "ref_author": "koreal",
                           "text": "x", "likes": 0, "via": "user:x", "platform": "x"}])
    discovery.update(db, ["core1", "core2"], {})
    assert db.q("SELECT interest FROM candidates WHERE handle='koreal'")[0]["interest"] == "invest-real-estate"


def test_new_field_crawls_down_from_famous_accounts(monkeypatch):
    monkeypatch.setattr(persona.llm, "ask", lambda *a, **k: json.dumps({"accounts": [
        {"name": "가", "x": "@seedA", "region": "kr"}, {"name": "나", "x": "seedB", "region": "kr"}, {"name": "없음", "x": "fakeZ"}]}))
    monkeypatch.setattr(interests, "x_exists", lambda h: h != "fakeZ")
    monkeypatch.setattr(measure, "known", lambda hs: {})
    crawled = []
    reps = {f"rep{i}": user(1000 * (i + 1)) for i in range(8)}

    def crawl(cfg, seeds):
        crawled.extend(seeds)
        return {"users": {"seeda": user(90000), "seedb": user(50000), **reps},
                "edges": [(s, r, "청약 소식") for s in ("seedA", "seedB") for r in reps]}
    monkeypatch.setattr(measure, "crawl", crawl)
    r = fieldcard.scale_rules({}, "부동산", {"must_know": [], "categories": [{"name": "청약·분양"}]})
    assert crawled == ["seedA", "seedB"]  # 없는 계정은 버리고, 유명 계정에서 출발
    assert r["sample"] == 10 and r["followers"]["ko"][1] < 90000 and "타고 내려감" in r["how"] and r["seeds"] == ["seedA", "seedB"]


def test_calibrate_from_collected_relations(monkeypatch, tmp_path):
    db = DB(tmp_path / "t.db")
    monkeypatch.setattr(fieldscale, "CALIBRATION", tmp_path / "field_rules.json")
    monkeypatch.setattr(domains, "load", lambda pid: {"info": {"topic_words": ["보드게임"]}})
    monkeypatch.setattr("popme.catalog.x_accounts", lambda cfg: [{"handle": "seed", "interest": "custom-board"},
                                                                {"handle": "ai1", "interest": "ai-coding"}])
    db.upsert_profiles([{"platform": "x", "handle": "seed", "name": "s", "followers": 80000, "lang": "ko"}])
    for i in range(9):
        db.upsert_profiles([{"platform": "x", "handle": f"r{i}", "name": "n", "followers": 500 * (i + 1), "lang": "ko"}])
        db.upsert_tweets([{"id": f"e{i}", "author": "seed", "created_at": NOW.isoformat(), "kind": "retweet", "ref_author": f"r{i}",
                           "text": "보드게임 신작", "likes": 0, "via": "user:x", "platform": "x"}])
    assert fieldscale.calibrate(db, {}) == ["custom-board"]  # AI 관심사는 지금 기준 그대로
    saved = json.loads((tmp_path / "field_rules.json").read_text(encoding="utf-8"))["custom-board"]
    assert saved["sample"] == 10 and saved["how"] == "수집한 반응 관계" and not fieldscale.due()
