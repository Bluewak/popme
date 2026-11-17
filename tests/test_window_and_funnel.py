"""브리핑 시간창·되풀이·검색 구획·질문 답변의 브리핑 날짜, 그리고 「얼마나 들어왔나」 재기 (popme/briefing.py, popme/funnel.py)."""
import json
from datetime import datetime, timedelta, timezone

from popme import briefing, clock, discovery, funnel
from popme.db import DB


def _db(tmp_path):
    return DB(tmp_path / "t.db")


def _brief(db, date, created, items=None):
    db.conn().execute("INSERT OR REPLACE INTO briefings (date, markdown, created_at, items) VALUES (?,?,?,?)",
                      (date, f"# {date} 브리핑\n", created.isoformat(), json.dumps(items) if items is not None else None))
    db.conn().commit()


def _item(db, url, published, collected, title="Claude Code 2.1 릴리스"):
    db.upsert_items([{"url": url, "source": "Claude Code", "grp": "공식 변경내역", "title": title, "summary": "",
                      "published": published.isoformat()}])
    db.conn().execute("UPDATE items SET collected_at=? WHERE url=?", (collected.isoformat(), url))
    db.conn().commit()


def test_rss_fetched_just_before_a_briefing_is_not_new_again_next_day(tmp_path):
    """지난 브리핑 10분 전 수집으로 받은 글은 그 브리핑에 이미 들어갔다 — 다음 날 「처음 본 글」로 또 넣지 않는다."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    prev = now - timedelta(hours=20)
    since = prev - timedelta(hours=1)
    _item(db, "https://a/before", prev - timedelta(hours=5), prev - timedelta(minutes=10))
    _item(db, "https://a/after", prev - timedelta(hours=5), now - timedelta(hours=1))
    got = [r["url"] for r in briefing.rss_items({"rss": []}, db, since.isoformat(), prev.isoformat())]
    assert got == ["https://a/after"]
    # 질문 답변(new_after 없음)은 예전 기준 그대로
    assert {r["url"] for r in briefing.rss_items({"rss": []}, db, since.isoformat())} == {"https://a/before", "https://a/after"}


def test_briefing_start_ignores_todays_earlier_version(tmp_path):
    """같은 날 다시 만들면 오늘 앞 판이 아니라 어제 브리핑을 기준으로 — 어제 브리핑 뒤 글이 빠지지 않게."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    today = clock.now().strftime("%Y-%m-%d")
    yesterday = (clock.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    y_at = now - timedelta(hours=30)
    _brief(db, yesterday, y_at)
    assert abs(briefing.briefing_start(db) - (y_at - timedelta(hours=1))) < timedelta(seconds=5)
    _brief(db, today, now - timedelta(hours=2))  # 오늘 아침 앞 판
    assert abs(briefing.briefing_start(db) - (y_at - timedelta(hours=1))) < timedelta(seconds=5)
    assert briefing.window_start(db) > briefing.briefing_start(db)  # 수집 멈춤 기준은 예전 그대로 (오늘 판 기준)


def _tw(db, i, created, collected, via, platform="x", likes=10):
    db.upsert_tweets([{"id": i, "author": f"a{i}", "text": f"글 {i}", "created_at": created.isoformat(), "lang": "ko",
                       "likes": likes, "urls": "[]", "kind": "post", "ref_author": None, "via": via, "platform": platform}])
    db.conn().execute("UPDATE tweets SET collected_at=? WHERE id=?", (collected.isoformat(), i))
    db.conn().commit()


def test_threads_section_takes_only_search_paths(tmp_path, monkeypatch):
    """Threads 검색·태그 구획은 검색·태그 경로만 — 모르는 새 경로의 글은 섞지 않는다."""
    db = _db(tmp_path)
    monkeypatch.setattr(briefing, "core_accounts", lambda cfg, db: [])
    monkeypatch.setattr(briefing, "threads_accounts", lambda cfg, db: [])
    monkeypatch.setattr(briefing, "rss_items", lambda *a, **k: [])
    monkeypatch.setattr(briefing.discovery, "candidates", lambda db, limit=5: [])
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=21)).isoformat()
    _tw(db, "t1", now - timedelta(hours=2), now - timedelta(hours=1), "th-tag:AI", "threads")
    _tw(db, "t2", now - timedelta(hours=2), now - timedelta(hours=1), "th-list:x", "threads")  # 모르는 경로
    data = briefing.build_data({}, db, since)
    assert "글 t1" in data and "글 t2" not in data


def test_verdict_found_anywhere_near_the_start():
    assert discovery.verdict_of("판단 보류: 대표글 없음") == ("대표글 없음", "보류")
    assert discovery.verdict_of("보류: 근거 부족") == ("근거 부족", "보류")
    assert discovery.verdict_of("비추천: 홍보 위주") == ("홍보 위주", "비추천")
    assert discovery.verdict_of("에이전트 실험을 꾸준히 공유") == ("에이전트 실험을 꾸준히 공유", "추천")
    assert discovery.verdict_of("추천: 실험 공유") == ("실험 공유", "추천")


def test_question_data_names_the_briefing_date(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(briefing.search, "find", lambda db, kw, since, until: ("", {}))
    monkeypatch.setattr(briefing, "build_data", lambda *a, **k: "자료")
    yesterday = (clock.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _brief(db, yesterday, datetime.now(timezone.utc) - timedelta(days=1))
    data, _ = briefing.ask_data({}, db, ["x"])
    assert f"# 가장 최근 브리핑 ({yesterday})\n(오늘 브리핑은 아직 없다)" in data and "# 오늘 브리핑" not in data
    today = clock.now().strftime("%Y-%m-%d")
    _brief(db, today, datetime.now(timezone.utc))
    data, _ = briefing.ask_data({}, db, ["x"])
    assert f"# 가장 최근 브리핑 ({today})\n# {today}" in data and "아직 없다" not in data


def test_funnel_counts_sections_and_cards(tmp_path):
    db = _db(tmp_path)
    data = "# X 핵심 계정 (돌아가며…)\n## @a (A)\n- 1\n- 2\n# RSS (공식…)\n- r1\n# 발굴 후보\n- c"
    assert funnel.sections(data) == {"재료:X 핵심 계정": 2, "재료:RSS": 1, "재료:발굴 후보": 1}
    now = datetime.now(timezone.utc)
    prev_items = [{"cat": "업데이트·단종", "links": [{"url": "https://a/1"}]}]
    _brief(db, "2026-11-01", now - timedelta(days=1), {"items": prev_items})
    items = [{"cat": "새 모델·도구", "links": [{"url": "https://x.com/u/status/1"}, {"url": "https://a/1"}]},
             {"cat": "발굴", "links": [{"url": "https://x.com/d/status/2"}]}]
    _tw(db, "n1", now - timedelta(days=3), now - timedelta(hours=1), "user:a1")
    _tw(db, "n2", now - timedelta(hours=3), now - timedelta(hours=1), "th-tag:AI", "threads")
    st = funnel.measure(db, data, items, (now - timedelta(days=1)).isoformat())
    assert st["카드:전체"] == 1 and st["카드:X·Threads 링크"] == 1 and st["카드:전날과 같은 링크"] == 1
    assert st["받음:X 계정"] == 1 and st["하루 넘어 받음:X 계정"] == 1 and st["받음:Threads 검색·태그"] == 1


def test_funnel_warns_only_after_a_week_and_when_far_from_usual(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    usual = {"재료:X 핵심 계정": 120, "재료:RSS": 100, "카드:X·Threads 링크": 2}
    for d in range(6):
        _brief(db, f"2026-11-{10 + d:02d}", now - timedelta(days=8 - d), {"items": [], "stats": usual})
    today = {"재료:X 핵심 계정": 40, "재료:RSS": 260, "카드:X·Threads 링크": 0}
    assert funnel.warnings(db, today, "2026-11-20") == []  # 6개뿐
    _brief(db, "2026-11-16", now - timedelta(days=1), {"items": [], "stats": usual})
    w = funnel.warnings(db, today, "2026-11-20")
    assert w == ["X 계정 글이 평소보다 적게 들어왔어요 (40개, 평소 120개)",
                 "RSS 글이 평소보다 많이 들어왔어요 (260개, 평소 100개)"]  # 평소 2개짜리는 흔들림이라 견주지 않음
    assert funnel.warnings(db, {**usual, "카드:전날과 같은 링크": 4}, "2026-11-20") == [
        "전날 카드와 같은 링크가 4개예요 (같은 소식이 되풀이됐을 수 있어요)"]
