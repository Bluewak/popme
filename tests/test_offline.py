"""인터넷이 없을 때 (popme/jobs.py): 모으지 않고 기다렸다가, 연결되면 그때. 끊기면 묵은 자료로 브리핑을 만들지 않는다.
2026-10-20 아침: 09:00에 끊겨 있었는데 그대로 모아서 경고 179개 + 「X 응답 구조가 바뀐 것 같아요」(오진) + 묵은 브리핑."""
import threading
from datetime import datetime

import pytest

from popme import jobs


class FakeDB:
    def __init__(self):
        self.saved = []

    def briefing_for(self, date):
        return None

    def upsert_items(self, items):
        pass

    def save_briefing(self, *a):
        self.saved.append(a)


def make(monkeypatch, net):
    j = jobs.Jobs.__new__(jobs.Jobs)
    j.cfg, j.db, j.lock = {"schedule": {"briefing_after": "09:00"}}, FakeDB(), threading.Lock()
    j.state = {"running": False, "status": "대기 중", "warnings": [], "last_run": None}
    j._tried, j.offline_retry, j._retries, j._closed = None, False, 0, []
    j.notes = []
    j.notify = lambda kind, msg: j.notes.append((kind, msg))
    monkeypatch.setattr(jobs, "online", lambda timeout=4: net["up"])
    return j


def test_no_collect_and_no_stale_briefing_when_offline(monkeypatch):
    net = {"up": False}
    j = make(monkeypatch, net)
    monkeypatch.setattr(j, "_browser_collect", lambda w: pytest.fail("끊겨 있으면 모으지 않는다"))
    monkeypatch.setattr(jobs.briefing, "make_briefing", lambda *a: pytest.fail("묵은 자료로 브리핑을 만들지 않는다"))
    j.lock.acquire()
    j._run(make_brief=True)
    assert j.state["warnings"] == ["인터넷이 연결돼 있지 않아서 모으지 않았어요. 연결되면 다시 모을게요"]
    assert j.offline_retry and j.notes[0][0] == "offline" and j.state["status"] == "인터넷 연결을 기다리는 중"
    assert not j.lock.locked()


def test_dropped_midway_is_one_warning(monkeypatch):
    net = {"up": True}
    j = make(monkeypatch, net)

    def browser(w):
        net["up"] = False  # X·Threads를 모으는 동안 끊김
    monkeypatch.setattr(j, "_browser_collect", browser)
    monkeypatch.setattr(jobs.catalog, "active", lambda cfg: [{"name": f"f{i}"} for i in range(20)])
    monkeypatch.setattr(jobs.sources, "collect", lambda feeds, progress: ([], [f"f{i}: getaddrinfo failed" for i in range(20)]))
    monkeypatch.setattr(jobs.briefing, "make_briefing", lambda *a: pytest.fail("거의 못 모았으면 브리핑을 만들지 않는다"))
    j.lock.acquire()
    j._run(make_brief=True)
    assert j.state["warnings"] == ["인터넷이 끊겨서 이번엔 거의 못 모았어요. 연결되면 다시 모을게요"]  # 경고 179개 대신 한 줄
    assert j.offline_retry


def test_fail_says_offline_not_x_changed(monkeypatch):
    j = make(monkeypatch, {"up": False})
    msg = j._fail("X", RuntimeError("X에서 연속으로 글을 못 찾았어요. X 응답 구조가 바뀐 것 같아요"))
    assert msg == "인터넷이 끊겨서 X는 이번에 못 모았어요"


def test_morning_waits_for_internet_then_runs_and_retries(monkeypatch):
    net = {"up": False}
    j = make(monkeypatch, net)
    runs = []
    monkeypatch.setattr(j, "run_full", lambda **k: runs.append(1) or True)
    nine = datetime(2026, 10, 20, 9, 0, 45)
    assert j.morning_tick(nine) is False and j.state["status"] == "인터넷 연결을 기다리는 중" and not runs
    assert j.morning_tick(datetime(2026, 10, 20, 9, 5)) is False  # 아직 끊김 — 하루 한 번 기회를 쓰지 않는다
    net["up"] = True
    assert j.morning_tick(datetime(2026, 10, 20, 9, 10)) is True and len(runs) == 1  # 연결되자마자
    assert j.morning_tick(datetime(2026, 10, 20, 9, 11)) is False  # 그 뒤로는 하루 한 번
    j.offline_retry = True  # 모으다 끊겼으면 연결되면 다시 (하루 몇 번까지)
    for minute in range(12, 12 + jobs.RETRY_OFFLINE + 2):
        j.morning_tick(datetime(2026, 10, 20, 9, minute))
        j.offline_retry = True
    assert len(runs) == 1 + jobs.RETRY_OFFLINE
    assert j.morning_tick(datetime(2026, 10, 20, 8, 59)) is False  # 시각 전엔 안 함
