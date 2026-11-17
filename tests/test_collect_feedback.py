"""수집 중 피드백 (popme/jobs.py, popme/app.py): 오류를 사람 말로, Chrome이 닫히면 알아보기, 중간 저장, 메뉴 표시."""
from types import SimpleNamespace

import pytest

from popme import jobs
from popme.collectors import NeedLogin, x


@pytest.mark.parametrize("err, want", [
    ("Target page, context or browser has been closed", "Chrome 창이 닫혔어요"),
    ("BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9333", "Chrome 창이 닫혔어요"),
    ("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://x.com/a", "인터넷 연결"),
    ("Page.goto: Timeout 45000ms exceeded.", "너무 오래"),
    ("claude 호출 실패: 5-hour limit reached", "사용 한도"),
    ("claude 호출 실패: Invalid API key · Please run /login", "AI 로그인이 풀렸어요"),
    ("X에서 연속으로 글을 못 찾았어요. X 응답 구조가 바뀐 것 같아요 (로그에 받은 API 이름 기록: a, b)", "구조가 바뀐 것 같아요"),
    ("KeyError: 'legacy'", "알 수 없는 문제"),
])
def test_why_is_plain(monkeypatch, err, want):
    monkeypatch.setattr(jobs.config, "load", lambda: {})
    text = jobs.why(RuntimeError(err))
    assert want in text and "Target page" not in text and "로그에 받은 API" not in text


@pytest.mark.parametrize("err", ["Failed to generate response", "moderate traffic", "accurate parse failed",
                                 "file size limit exceeded"])
def test_why_does_not_call_other_errors_a_limit(err):
    assert "사용 한도" not in jobs.why(RuntimeError(err))


@pytest.mark.parametrize("err", ["Rate limit exceeded", "You've hit your usage limit", "HTTP 429", "RESOURCE_EXHAUSTED: Quota exceeded"])
def test_why_names_the_chosen_ai_for_limits(err):
    assert jobs.why(RuntimeError(err), {"llm": {"provider": "gemini"}}).startswith("Gemini 사용 한도")


def test_need_login_message_kept():
    assert jobs.why(NeedLogin("X 로그인이 필요해요.")) == "X 로그인이 필요해요."


def test_fail_marks_chrome_closed():
    fail = jobs.Jobs._fail
    assert fail(None, "X", RuntimeError("Browser has been closed")) == "Chrome 창이 닫혀서 X는 이번에 못 모았어요"
    me = jobs.Jobs.__new__(jobs.Jobs)
    me._closed = []
    fail(me, "일정", RuntimeError("Target closed"))
    fail(me, "Threads", RuntimeError("Target closed"))
    assert me._closed == ["일정", "Threads"]
    assert jobs.missed(["X", "Threads"]) == "X·Threads는" and jobs.missed(["X", "일정"]) == "X·일정은"
    assert fail(None, "Threads", RuntimeError("Timeout 30000ms exceeded")) == "Threads 수집 실패: 페이지가 너무 오래 걸렸어요"


class FakePage:
    def wait_for_timeout(self, ms):
        pass

    def close(self):
        pass


class FakeCtx:
    def cookies(self, *a):
        return [{"name": "auth_token", "value": "1"}]

    def new_page(self):
        return FakePage()


def test_x_saves_each_page_even_if_cut_off(monkeypatch):
    """페이지마다 on_batch로 저장 → 세 번째 페이지에서 Chrome이 닫혀도 앞 두 페이지 글은 남는다."""
    calls = {"n": 0}

    def fake_visit(page, url, via, scrolls, out, ops_seen, owner, since, users, answered=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("Target page, context or browser has been closed")
        out.append({"id": f"t{calls['n']}", "via": via})
        return 1
    monkeypatch.setattr(x, "_visit", fake_visit)
    monkeypatch.setattr(x.random, "uniform", lambda a, b: 0)
    saved = []
    with pytest.raises(RuntimeError):
        x.collect(FakeCtx(), {"x": {}}, ["a", "b", "c", "d"], on_batch=saved.extend)
    assert [t["id"] for t in saved] == ["t1", "t2"]


def test_x_rotating_accounts_scroll_deeper_and_record_only_seen(monkeypatch):
    """돌아가며 보는 계정(since_by)은 그 시각까지·DEEP_SCROLLS번까지, 나머지는 since·설정 스크롤.
    visited에는 정상으로 연 계정만 — 글이 0개라도 X가 응답했으면(조용한 계정) 넣고, 응답이 없거나 끊기면 뺀다."""
    calls = []

    def fake_visit(page, url, via, scrolls, out, ops_seen, owner, since, users, answered):
        calls.append((owner, scrolls, since))
        if owner == "d":
            raise RuntimeError("Target page, context or browser has been closed")
        if owner != "c":  # c: 응답 없음 (막힘·구조 바뀜)
            answered.append(True)
        return 3 if owner == "A" else 0
    monkeypatch.setattr(x, "_visit", fake_visit)
    monkeypatch.setattr(x.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(x.random, "shuffle", lambda l: None)
    visited = []
    with pytest.raises(RuntimeError):
        x.collect(FakeCtx(), {"x": {}}, ["A", "b", "c", "d"], since="S", since_by={"a": "OLD"}, visited=visited)
    assert calls[0] == ("A", x.DEEP_SCROLLS, "OLD") and calls[1] == ("b", 3, "S")
    assert visited == ["A", "b"]  # b는 조용한 계정, c는 응답 없음, d는 끊김


def test_menu_label_and_busy_line():
    from popme.app import Api
    state = {"running": False, "status": ""}
    said = []
    me = SimpleNamespace(_jobs=SimpleNamespace(state=state, run_full=lambda collect=True: not state["running"]),
                         _show_chat=said.append, _char=SimpleNamespace(line=lambda cat, **kw: None))
    assert Api.collect_label(me) == "지금 수집" and Api.collect_label(me, rebuild=True) == "브리핑만 다시 만들기"
    assert Api.collect_now(me) is True and said == []
    state.update(running=True, status="X 수집 중 3/33 · Threads 수집 중 5/47")
    assert Api.collect_label(me).startswith("모으는 중… X 수집 중 3/33") and len(Api.collect_label(me)) <= 37
    assert Api.collect_now(me) is False  # 두 번 돌지 않고
    assert said == ["지금 모으는 중이에요… X 수집 중 3/33 · Threads 수집 중 5/47"]  # 진행 상황을 말한다


def test_minimize_while_collecting_is_on_by_default():
    """수집 중 Chrome 창 내려 두기: 설정이 없으면 켜짐, false로 끈다."""
    from popme.jobs import Jobs
    j = Jobs.__new__(Jobs)
    j.cfg = {}
    assert j._minimizing() is True
    j.cfg = {"chrome": {"minimize_while_collecting": False}}
    assert j._minimizing() is False


def test_timeline_ok_only_for_real_timeline():
    """429 오류 JSON·계정 정보 응답만 받은 날은 「봤다」로 남기지 않는다 (다음 수집이 그 사이 글을 건너뛰지 않게)."""
    ok = SimpleNamespace(status=200)
    timeline = {"data": {"user": {"result": {"__typename": "User", "timeline": {"timeline": {"instructions": []}}}}}}
    assert x.timeline_ok(ok, timeline)
    assert not x.timeline_ok(SimpleNamespace(status=429), timeline)
    assert not x.timeline_ok(ok, {"errors": [{"message": "Rate limit exceeded"}]})
    assert not x.timeline_ok(ok, {"data": {"user": {"result": {"__typename": "User", "legacy": {}}}}})  # UserByScreenName
    assert not x.timeline_ok(ok, None)


@pytest.mark.parametrize("err", ["KeyError: 'author'", "authority check", "char 4290"])
def test_why_does_not_call_parts_of_words_a_login_or_limit(err):
    text = jobs.why(RuntimeError(err))
    assert "로그인" not in text and "사용 한도" not in text


def test_why_names_broken_ai_json():
    import json
    try:
        json.loads('{"a" 1}')
    except json.JSONDecodeError as e:  # "Expecting ':' delimiter" — 예전엔 deLIMITer가 「사용 한도」로 보였다
        assert "형식이 깨졌어요" in jobs.why(e)


def test_saver_writes_each_profile_once_unless_changed():
    calls = []
    db = SimpleNamespace(upsert_tweets=lambda b: None, upsert_profiles=lambda ps: calls.append([p["handle"] for p in ps]))
    users = {"a": {"handle": "a", "followers": 1}, "b": {"handle": "b", "followers": 2}}
    save = jobs.Saver(db, users)
    save([])
    save([])  # 그대로면 다시 안 쓴다
    users["c"] = {"handle": "c", "followers": 3}
    users["a"] = {"handle": "a", "followers": 5}
    save([])
    save.flush()
    assert calls == [["a", "b"], ["a", "c"]]


def test_headlines_for_chatter_without_rebuilding_the_page(tmp_path):
    """캐릭터 잡담은 머리 소식만 — get_briefing(일정·메일·카드 전부)을 다시 만들지 않는다."""
    import json as _json

    from popme.app import Api
    from popme.db import DB
    db = DB(tmp_path / "t.db")
    me = SimpleNamespace(_jobs=SimpleNamespace(db=db))
    assert Api._headlines(me) == []
    items = [{"cat": "업데이트·단종", "rel": 3, "urgent": False, "status": "확인됨", "title": "<b>새 기능</b>", "links": []}]
    db.save_briefing("2026-11-18", "# 11월 18일 아침 브리핑\n", _json.dumps({"items": items}))
    heads = Api._headlines(me)
    assert heads and "&lt;b&gt;" in heads[0]["html"]
