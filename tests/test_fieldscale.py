"""분야마다 발굴 기준 (popme/fieldscale.py): 대표 계정 팔로워로 AI 기준을 늘이고 줄이기, 분야 낱말, 후보는 그 관심사 기준으로 판정."""
import json
from datetime import datetime, timedelta, timezone

from popme import discovery, domains, fieldcard, fieldscale, interests, measure, persona
from popme.db import DB

NOW = datetime.now(timezone.utc)


def test_scales_and_thresholds():
    small = fieldscale.scales([("kr", 5000), ("kr", 9000), ("kr", 2000)])  # 부동산처럼 작은 분야
    assert small["kr"] == 9000 / fieldscale.AI_REF["kr"] * 0 + 5000 / fieldscale.AI_REF["kr"] and small["world"] == 1.0  # 해외 표본이 없으면 AI 그대로
    rules = fieldscale.thresholds(small)
    assert rules["ko"][0] < discovery.DEFAULTS["followers"]["ko"][0] and rules["global"] == discovery.DEFAULTS["followers"]["global"]
    huge = fieldscale.scales([("world", 50_000_000)] * 3)
    assert huge["world"] == fieldscale.SCALE_RANGE[1]  # 거대 계정 몇 개가 기준을 터무니없이 올리지 않게
    assert fieldscale.scales([("kr", 1)])["kr"] == 1.0  # 표본이 모자라면 AI 기준
    assert fieldscale._round(37412) == 37000 and fieldscale._round(10) == fieldscale.FLOOR


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


def test_packs_ship_rules():
    for pid in ("sports-soccer", "invest-real-estate", "ent-kpop"):
        info = domains.load(pid)["info"]
        assert set(info["followers"]) == {"company", "global", "ko", "threads"} and info["topic_words"]
    assert domains.load("invest-real-estate")["info"]["followers"]["ko"][0] < domains.load("ent-kpop")["info"]["followers"]["ko"][0]


def setup(tmp_path):
    db = DB(tmp_path / "t.db")
    for h, f, lang in (("koreal", 2500, "ko"), ("bigfan", 2500, "ko")):
        db.upsert_profiles([{"platform": "x", "handle": h, "name": h, "followers": f, "lang": lang}])
    return db


def tweet(db, n, author, text, days=1, likes=20):
    db.upsert_tweets([{"id": f"t{n}", "author": author, "created_at": (NOW - timedelta(days=days)).isoformat(), "kind": "post",
                       "text": text, "likes": likes, "via": "user:x", "platform": "x", "lang": "ko"}])


def test_candidate_judged_by_its_field(monkeypatch, tmp_path):
    db = setup(tmp_path)
    rules = {"invest-real-estate": {"followers": {"ko": [2000, 900]}, "topic": __import__("re").compile("청약|전세|분양")}}
    monkeypatch.setattr(fieldscale, "rules_for", lambda pid: rules.get(pid, {"followers": None, "topic": None}))
    db.upsert_candidate("koreal", "부동산 기자", "r", 5, "x", "ko", "invest-real-estate")
    db.upsert_candidate("bigfan", "AI 쪽", "r", 5, "x", "ko", None)
    for i in range(8):
        tweet(db, i, "koreal", "이번 주 청약 경쟁률과 전세 시장 정리", days=i * 3)
        tweet(db, 100 + i, "bigfan", "이번 주 청약 경쟁률과 전세 시장 정리", days=i * 3)
    db.mark_checked(["koreal", "bigfan"])
    discovery.evaluate(db, {})
    st = {r["handle"]: r for r in db.q("SELECT handle, stage, check_note FROM candidates")}
    assert st["koreal"]["stage"] == "pass" and "분야 글" in st["koreal"]["check_note"]  # 부동산 기준 2천이면 통과, 분야 낱말로 꾸준함
    assert st["bigfan"]["stage"] == "fail" and "기준 15,000" in st["bigfan"]["check_note"]  # 관심사를 모르면 AI 기준


def test_update_remembers_interest(monkeypatch, tmp_path):
    db = setup(tmp_path)
    monkeypatch.setattr(discovery, "_interest_of", lambda cfg: {"core1": "invest-real-estate", "core2": "invest-real-estate"})
    for n, (a, org) in enumerate((("core1", "A사"), ("core2", "B사"))):
        db.upsert_profiles([{"platform": "x", "handle": a, "name": a, "followers": 1000, "org": org}])
        db.upsert_tweets([{"id": f"r{n}", "author": a, "created_at": NOW.isoformat(), "kind": "retweet", "ref_author": "koreal",
                           "text": "x", "likes": 0, "via": "user:x", "platform": "x"}])
    discovery.update(db, ["core1", "core2"], {})
    assert db.q("SELECT interest FROM candidates WHERE handle='koreal'")[0]["interest"] == "invest-real-estate"


def test_new_field_measures_representative_accounts(monkeypatch):
    monkeypatch.setattr(persona.llm, "ask", lambda *a, **k: json.dumps({"accounts": [
        {"name": "가", "x": "@realA", "region": "kr"}, {"name": "나", "x": "realB", "region": "kr"},
        {"name": "다", "x": "realC", "region": "kr"}, {"name": "없음", "x": "fakeZ", "region": "kr"}]}))
    monkeypatch.setattr(interests, "x_exists", lambda h: h != "fakeZ")
    monkeypatch.setattr(measure, "known", lambda hs: {"reala": 30000})
    asked = []
    monkeypatch.setattr(measure, "x_followers", lambda cfg, hs: asked.extend(hs) or {"realb": 10000, "realc": 20000})
    r = fieldcard.scale_rules({}, "부동산", {"must_know": [], "categories": [{"name": "청약·분양"}]})
    assert asked == ["realB", "realC"]  # DB에 없는 것만 Chrome으로, 없는 계정은 버림
    assert r["sample"] == 3 and r["scale"]["kr"] == round(20000 / fieldscale.AI_REF["kr"], 3) and "청약" in r["topic_words"]


def test_calibrate_from_collected_followers(monkeypatch, tmp_path):
    db = DB(tmp_path / "t.db")
    monkeypatch.setattr(fieldscale, "CALIBRATION", tmp_path / "field_rules.json")
    accts = [{"handle": f"h{i}", "interest": "custom-board", "region": "kr"} for i in range(5)]
    for i in range(5):
        db.upsert_profiles([{"platform": "x", "handle": f"h{i}", "name": "n", "followers": 10000 * (i + 1), "lang": "ko"}])
    monkeypatch.setattr("popme.catalog.x_accounts", lambda cfg: accts)
    assert fieldscale.calibrate(db, {}) == ["custom-board"]
    saved = json.loads((tmp_path / "field_rules.json").read_text(encoding="utf-8"))["custom-board"]
    assert saved["sample"] == 5 and saved["scale"]["kr"] == round(30000 / fieldscale.AI_REF["kr"], 3)
    assert not fieldscale.due()  # 방금 했으니 다음 주에
