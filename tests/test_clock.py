"""시간 기준은 PC 시간대 (popme/clock.py): 해외에 가서 PC 시간대가 바뀌면 일정·'오늘'·검색 기간이 현지 시간으로."""
from datetime import timedelta, timezone

import pytest

from popme import agenda, clock, search
from popme.collectors import ics
from test_ics import FIRST, LAST, parsed


@pytest.mark.parametrize("offset, expected", [
    (9, ("2026-10-09", "10:00")),    # 한국·도쿄
    (0, ("2026-10-09", "01:00")),    # UTC (CI 서버)
    (-7, ("2026-10-08", "18:00")),   # 미국 서부 (서머타임)
])
def test_timed_event_follows_pc_timezone(monkeypatch, offset, expected):
    monkeypatch.setattr(clock, "tz", lambda: timezone(timedelta(hours=offset)))
    e = parsed()["ics0:timed"]  # 한국 시간 10:00 일정
    assert [(str(d), t) for d, t, _ in agenda.occurrences(e, FIRST, LAST)] == [expected]


def test_all_day_event_keeps_its_date_anywhere(monkeypatch):
    monkeypatch.setattr(clock, "tz", lambda: timezone(timedelta(hours=-7)))
    e = parsed()["ics0:trip"]  # 종일 일정은 시간대와 상관없이 그 날짜
    assert [str(d) for d, _, _ in agenda.occurrences(e, FIRST, LAST)] == ["2026-10-10", "2026-10-11", "2026-10-12"]


def test_today_and_search_range_use_pc_timezone(monkeypatch):
    monkeypatch.setattr(clock, "tz", lambda: timezone.utc)
    assert clock.now().utcoffset() == timedelta(0)
    assert search._range("2026-09-01", "2026-09-30") == ("2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")


def test_floating_ics_time_is_pc_local(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(clock, "tz", lambda: timezone(timedelta(hours=9)))
    assert ics._utc_iso(datetime(2026, 10, 9, 10, 0)) == "2026-10-09T01:00:00+00:00"
