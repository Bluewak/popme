"""브리핑 시간창·되풀이·검색 구획·질문 답변의 브리핑 날짜, 그리고 「얼마나 들어왔나」 재기 (popme/briefing.py, popme/funnel.py)."""
import json
from datetime import datetime, timedelta, timezone

from popme import accounts, ask, briefing, clock, discovery, funnel
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
    monkeypatch.setattr(accounts, "core_accounts", lambda cfg, db: [])
    monkeypatch.setattr(accounts, "threads_accounts", lambda cfg, db: [])
    monkeypatch.setattr(briefing, "rss_items", lambda *a, **k: [])
    monkeypatch.setattr(briefing.discovery, "candidates", lambda db, limit=5: [])
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=21)).isoformat()
    _tw(db, "t1", now - timedelta(hours=2), now - timedelta(hours=1), "th-tag:AI", "threads")
    _tw(db, "t2", now - timedelta(hours=2), now - timedelta(hours=1), "th-list:x", "threads")  # 모르는 경로
    data = briefing.build_data({}, db, since)
    assert "글 t1" in data and "글 t2" not in data


def _bare(monkeypatch, th_users=()):
    monkeypatch.setattr(accounts, "core_accounts", lambda cfg, db: [])
    monkeypatch.setattr(accounts, "threads_accounts", lambda cfg, db: list(th_users))
    monkeypatch.setattr(briefing, "rss_items", lambda *a, **k: [])
    monkeypatch.setattr(briefing.discovery, "candidates", lambda db, limit=5: [])


def test_late_threads_tag_posts_go_in_once(tmp_path, monkeypatch):
    """2026-10-31 AI TOP 100 예선 후기: Threads 태그 글을 사흘 늦게 받아 「올라온 시각」 구간 밖이라 빠졌다.
    지난 브리핑 뒤에 처음 받은 며칠 전 글은 넣는다 (지난 브리핑 전에 받은 글·7일 넘은 글은 안 넣는다)."""
    db = _db(tmp_path)
    _bare(monkeypatch)
    now = datetime.now(timezone.utc)
    prev = now - timedelta(hours=20)
    since = (prev - timedelta(hours=1)).isoformat()
    _tw(db, "late", now - timedelta(days=3), now - timedelta(hours=2), "th-tag:인공지능AI", "threads", likes=155)
    _tw(db, "shown", now - timedelta(days=3), prev - timedelta(hours=2), "th-tag:인공지능AI", "threads")
    _tw(db, "ancient", now - timedelta(days=9), now - timedelta(hours=2), "th-tag:인공지능AI", "threads")
    _tw(db, "xlate", now - timedelta(days=2), now - timedelta(hours=2), "search:0")
    data = briefing.build_data({}, db, since, late_since=prev.isoformat())
    assert "글 late" in data and "글 shown" not in data and "글 ancient" not in data and "글 xlate" in data
    assert "늦게 받은 며칠 전 글도 있다" in data
    assert "글 late" not in briefing.build_data({}, db, since)  # 질문 답변(late_since 없음)은 예전 그대로


def test_late_threads_account_posts_only_for_accounts_seen_before(tmp_path, monkeypatch):
    """새로 더한 Threads 계정의 지난 글이 쏟아지지 않게, 지난 브리핑 전에도 글을 받은 계정만."""
    db = _db(tmp_path)
    _bare(monkeypatch, ["a1", "a2"])
    now = datetime.now(timezone.utc)
    prev = now - timedelta(hours=20)
    since = (prev - timedelta(hours=1)).isoformat()
    _tw(db, "1", now - timedelta(days=2), now - timedelta(hours=2), "th-user:a1", "threads")  # author a1
    db.upsert_tweets([{"id": "old1", "author": "a1", "text": "예전 글", "created_at": (now - timedelta(days=5)).isoformat(),
                       "lang": "ko", "likes": 1, "urls": "[]", "kind": "post", "via": "th-user:a1", "platform": "threads"}])
    db.conn().execute("UPDATE tweets SET collected_at=? WHERE id='old1'", ((prev - timedelta(days=4)).isoformat(),))
    db.conn().commit()
    _tw(db, "2", now - timedelta(days=2), now - timedelta(hours=2), "th-user:a2", "threads")  # a2는 처음 본 계정
    data = briefing.build_data({}, db, since, late_since=prev.isoformat())
    assert "글 1" in data and "글 2" not in data


def test_stories_group_same_event_titles():
    rows = [{"title": t} for t in [
        "네이버, 고교생 로봇 대회 개최…AI 로봇 대결 - 뉴시스",
        "카카오 'AI TOP 100' 본선 개최, AI에이전트 운용 역량 확인 - 뉴스핌",
        "5시간 만에 5000명 몰렸다…카카오 ‘AI TOP 100’, 에이전트 운용 실력 겨뤄 - 이데일리",
        "LG CNS, 고교생 AI 경진대회 '챌린지데이' 개최 - 뉴스드림",
        "카카오,대국민 AI 경진대회 'AI TOP 100' 3기 성료 - 뉴스드림",
        "카카오, AI 에이전트 실전 경진대회 개최…5,000명 지원해 100명 본선 진출 - 인공지능신문",
        "카카오, AI 에이전트 활용 인재 발굴…경진대회에 5000명 몰렸다 - 파이낸셜뉴스"]]
    got = briefing.stories(rows, "AI 경진대회 개최")
    # 「경진대회에」·「5,000명」도 같은 낱말로 센다. 「실전 경진대회…본선 진출」은 겹침이 모자라 따로 (나뉘는 쪽으로 틀린다)
    assert [len(s) for s in got] == [4, 1, 1, 1] and all("카카오" in r["title"] for r in got[0])
    assert "실전" not in " ".join(r["title"] for r in got[0])


def test_news_search_keeps_biggest_story_over_latest_singles(tmp_path, monkeypatch):
    """뉴스 검색은 최신 기사 cap개가 아니라 사건 cap개, 기사 많은 사건부터 — 늦게 올라온 단발 기사에 큰 사건이 밀리지 않게."""
    from popme import catalog
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    rows = [{"title": f"{place} 경진대회 열려 - 매체{i}", "published": now - timedelta(minutes=i)}
            for i, place in enumerate(["금천구", "동작구", "세종시", "창원대", "강동대", "경운대"])]
    rows += [{"title": f"카카오 TOP 100 본선 5000명 몰려 - 매체{i}", "published": now - timedelta(hours=3, minutes=i)}
             for i in range(3)]
    db.upsert_items([{"url": f"https://n/{i}", "source": "뉴스 · AI 경진대회 개최", "grp": "뉴스", "summary": "",
                      "title": r["title"], "published": r["published"].isoformat()} for i, r in enumerate(rows)])
    feed = {"id": "news:kr:AI 경진대회 개최", "name": "뉴스 · AI 경진대회 개최", "group": "뉴스", "cap": 5, "interests": {}}
    monkeypatch.setattr(catalog, "active", lambda cfg: [feed])
    monkeypatch.setattr(catalog, "caps", lambda cfg: {"뉴스": 13})
    got = [r["title"] for r in briefing.rss_items({"news": {}}, db, (now - timedelta(days=1)).isoformat())]
    assert len(got) == 5 and got[0].startswith("카카오 TOP 100") and got[0].endswith("(같은 소식 기사 3건)")
    assert "경운대 경진대회 열려 - 매체5" not in got  # 단발 기사 중 가장 오래된 것이 빠진다


def test_verdict_found_anywhere_near_the_start():
    assert discovery.verdict_of("판단 보류: 대표글 없음") == ("대표글 없음", "보류")
    assert discovery.verdict_of("보류: 근거 부족") == ("근거 부족", "보류")
    assert discovery.verdict_of("비추천: 홍보 위주") == ("홍보 위주", "비추천")
    assert discovery.verdict_of("에이전트 실험을 꾸준히 공유") == ("에이전트 실험을 꾸준히 공유", "추천")
    assert discovery.verdict_of("추천: 실험 공유") == ("실험 공유", "추천")


def test_question_data_names_the_briefing_date(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(ask.search, "find", lambda db, kw, since, until: ("", {}))
    monkeypatch.setattr(briefing, "build_data", lambda *a, **k: "자료")
    yesterday = (clock.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _brief(db, yesterday, datetime.now(timezone.utc) - timedelta(days=1))
    data, _ = ask.ask_data({}, db, ["x"])
    assert f"# 가장 최근 브리핑 ({yesterday})\n(오늘 브리핑은 아직 없다)" in data and "# 오늘 브리핑" not in data
    today = clock.now().strftime("%Y-%m-%d")
    _brief(db, today, datetime.now(timezone.utc))
    data, _ = ask.ask_data({}, db, ["x"])
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


def _post(db, i, author, via, created, collected, kind="post", ref=None, ref_author=None, text=None, likes=10):
    db.upsert_tweets([{"id": i, "author": author, "text": text or f"{author} 글 {i}", "created_at": created.isoformat(),
                       "lang": "en", "likes": likes, "urls": "[]", "kind": kind, "ref_author": ref_author, "ref_id": ref,
                       "via": via, "platform": "x"}])
    db.conn().execute("UPDATE tweets SET collected_at=? WHERE id=?", (collected.isoformat(), i))
    db.conn().commit()


def _material(monkeypatch, db, handles, since, late_since=None):
    monkeypatch.setattr(accounts, "core_accounts",
                        lambda cfg, db: [{"handle": h, "name": h, "group": "g", "focus": "f"} for h in handles])
    monkeypatch.setattr(accounts, "threads_accounts", lambda cfg, db: [])
    monkeypatch.setattr(briefing, "rss_items", lambda *a, **k: [])
    monkeypatch.setattr(briefing.discovery, "candidates", lambda db, limit=5: [])
    return briefing.build_data({}, db, since.isoformat(), late_since=late_since)


def test_quote_carries_its_original(tmp_path, monkeypatch):
    """인용 글 아래에 원문을 붙인다 — 원문 작성자가 보는 계정이 아니어도. 「neat」만으로는 무슨 소식인지 모른다."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    _post(db, "o1", "joelniklaus", "user:badlogicgames", now - timedelta(hours=5), now - timedelta(hours=1),
          text="Releasing the Harness Leaderboard", likes=2638)
    _post(db, "q1", "badlogicgames", "user:badlogicgames", now - timedelta(hours=3), now - timedelta(hours=1),
          kind="quote", ref="o1", ref_author="joelniklaus", text="neat")
    _post(db, "q2", "badlogicgames", "user:badlogicgames", now - timedelta(hours=2), now - timedelta(hours=1),
          kind="quote", ref="nowhere", ref_author="someone", text="원문이 DB에 없는 인용")
    data = _material(monkeypatch, db, ["badlogicgames"], now - timedelta(hours=20))
    lines = data.split("\n")
    i = next(n for n, line in enumerate(lines) if "neat" in line)
    assert lines[i + 1].startswith("  ↳ 원문 @joelniklaus ♥2638: Releasing the Harness Leaderboard")
    j = next(n for n, line in enumerate(lines) if "원문이 DB에 없는 인용" in line)
    assert j + 1 == len(lines) or not lines[j + 1].startswith("  ↳")
    assert funnel.sections(data)["재료:X 핵심 계정"] == 2  # 원문 줄은 글 수에 안 센다


def test_talked_about_needs_two_watched_people_and_a_fresh_reaction(tmp_path, monkeypatch):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    prev = now - timedelta(hours=20)
    old, new = prev - timedelta(hours=5), now - timedelta(hours=1)
    _post(db, "hot", "OpenAI", "user:swyx", now - timedelta(days=2), old, text="Introducing a model")
    _post(db, "r1", "swyx", "user:swyx", now - timedelta(days=2), old, kind="quote", ref="hot", ref_author="OpenAI")
    _post(db, "r2", "theo", "user:theo", now - timedelta(hours=10), new, kind="reply", ref="hot", ref_author="OpenAI")
    # 한 명만 반응 / 보는 계정이 아닌 사람 / 다른 계정 페이지에서 딸려 온 글은 세지 않는다
    _post(db, "solo", "x1", "user:swyx", now - timedelta(days=1), new, text="한 명만")
    _post(db, "s1", "swyx", "user:swyx", now - timedelta(hours=9), new, kind="quote", ref="solo", ref_author="x1")
    _post(db, "s2", "stranger", "user:stranger", now - timedelta(hours=9), new, kind="quote", ref="solo", ref_author="x1")
    _post(db, "s3", "theo", "user:swyx", now - timedelta(hours=9), new, kind="quote", ref="solo", ref_author="x1")
    # 반응이 모두 지난 브리핑 전에 들어온 화제는 다시 올리지 않는다
    _post(db, "stale", "y1", "user:swyx", now - timedelta(days=3), old)
    for k, h in (("t1", "swyx"), ("t2", "theo")):
        _post(db, k, h, f"user:{h}", now - timedelta(days=2), old, kind="quote", ref="stale", ref_author="y1")
    got = briefing.talked_about(db, ["swyx", "theo"], prev.isoformat())
    assert [(o["id"], sorted(who)) for o, who in got] == [("hot", ["swyx", "theo"])]
    data = _material(monkeypatch, db, ["swyx", "theo"], now - timedelta(hours=21), late_since=prev.isoformat())
    assert data.startswith("# 여러 사람이 반응한 글")
    assert "[2명: " in data and "Introducing a model" in data
    assert funnel.sections(data)["재료:여러 사람이 반응한 글"] == 1


def test_briefing_rules_ask_for_official_and_talked_links():
    assert "가장 공식적인 링크 1~2개만" not in briefing.BRIEFING_RULES
    assert "공식 출처 하나와" in briefing.BRIEFING_RULES and "# 여러 사람이 반응한 글" in briefing.BRIEFING_RULES


def test_list_since_starts_from_last_list_visit(tmp_path, monkeypatch):
    since = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    cfg = {"x": {"list_url": "https://x.com/i/lists/123456789"}}
    assert accounts.list_since(cfg, since) == since  # 처음
    seen = datetime.now(timezone.utc) - timedelta(days=2)
    accounts.X_VISITS.write_text(json.dumps({"list:123456789": seen.isoformat()}), encoding="utf-8")
    assert accounts.list_since(cfg, since) == (seen - timedelta(hours=1)).isoformat()
    assert accounts.list_since({"x": {}}, since) == since


def test_talked_about_counts_reactions_seen_on_the_list(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    _post(db, "hot", "OpenAI", "list:1", now - timedelta(hours=9), now - timedelta(hours=1))
    _post(db, "r1", "swyx", "list:1", now - timedelta(hours=8), now - timedelta(hours=1), kind="quote", ref="hot", ref_author="OpenAI")
    _post(db, "r2", "theo", "user:theo", now - timedelta(hours=8), now - timedelta(hours=1), kind="quote", ref="hot", ref_author="OpenAI")
    assert [o["id"] for o, _ in briefing.talked_about(db, ["swyx", "theo"], None)] == ["hot"]
