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

    def fake_visit(page, url, via, scrolls, out, ops_seen, owner, since, users):
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
