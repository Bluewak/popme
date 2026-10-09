"""ICS 캘린더 해석 → 일정 펼치기 (popme/collectors/ics.py, popme/agenda.py)."""
from datetime import date

from popme import agenda
from popme.collectors import ics

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Seoul
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0900
TZOFFSETTO:+0900
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:timed
DTSTART;TZID=Asia/Seoul:20261009T100000
DTEND;TZID=Asia/Seoul:20261009T113000
SUMMARY:치과
LOCATION:강남역
END:VEVENT
BEGIN:VEVENT
UID:trip
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261013
SUMMARY:여행
END:VEVENT
BEGIN:VEVENT
UID:weekly
DTSTART;TZID=Asia/Seoul:20260921T190000
DTEND;TZID=Asia/Seoul:20260921T200000
RRULE:FREQ=WEEKLY;UNTIL=20261102T095959Z;BYDAY=MO
EXDATE;TZID=Asia/Seoul:20261005T190000
SUMMARY:운동
END:VEVENT
BEGIN:VEVENT
UID:weekly
RECURRENCE-ID;TZID=Asia/Seoul:20261012T190000
DTSTART;TZID=Asia/Seoul:20261013T200000
DTEND;TZID=Asia/Seoul:20261013T210000
SUMMARY:운동 (옮김)
END:VEVENT
BEGIN:VEVENT
UID:gone
DTSTART:20261009T010000Z
DTEND:20261009T020000Z
STATUS:CANCELLED
SUMMARY:취소됨
END:VEVENT
BEGIN:VEVENT
UID:utc
DTSTART:20261008T230000Z
SUMMARY:UTC 늦은 밤
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n").encode("utf-8")

FIRST, LAST = date(2026, 10, 1), date(2026, 11, 10)


def parsed():
    return {e["id"]: e for e in ics.parse(ICS, "ics0:테스트", "ics0:")}


def days(e):
    return [(str(d), t) for d, t, _ in agenda.occurrences(e, FIRST, LAST)]


def test_timed_event_in_korean_time():
    e = parsed()["ics0:timed"]
    assert e["start_at"] == "2026-10-09T01:00:00+00:00" and e["all_day"] == 0
    assert e["location"] == "강남역"
    assert days(e) == [("2026-10-09", "10:00")]


def test_all_day_end_is_inclusive():
    e = parsed()["ics0:trip"]  # ICS DTEND는 '다음 날' → 10/10~10/12 사흘
    assert [d for d, _ in days(e)] == ["2026-10-10", "2026-10-11", "2026-10-12"]


def test_weekly_with_exdate_moved_and_until():
    evs = parsed()
    assert days(evs["ics0:weekly"]) == [("2026-10-19", "19:00"), ("2026-10-26", "19:00"), ("2026-11-02", "19:00")]
    assert days(evs["ics0:weekly@20261012"]) == [("2026-10-13", "20:00")]


def test_cancelled_is_deleted():
    assert parsed()["ics0:gone"]["deleted"] == 1


def test_utc_converts_to_next_day_in_korea():
    assert days(parsed()["ics0:utc"]) == [("2026-10-09", "08:00")]


def test_same_content_same_signature():
    a, b = parsed(), parsed()
    assert all(a[k]["updated_at"] == b[k]["updated_at"] for k in a)  # 다시 받아도 '바뀜'으로 안 잡힘


def test_empty_url_skipped_and_failure_reported(monkeypatch):
    calls = []

    def fake_fetch(url, timeout=30):
        calls.append(url)
        if "broken" in url:
            raise OSError("연결 실패")
        return ICS

    monkeypatch.setattr(ics, "fetch", fake_fetch)
    cfg = {"calendar": {"holidays": False, "ics": [
        {"name": "빈칸", "url": ""}, {"name": "고장", "url": "https://broken"}, {"name": "정상", "url": "https://ok"}]}}
    warnings = []
    events, synced = ics.collect(cfg, warnings=warnings)
    assert calls == ["https://broken", "https://ok"]
    assert synced == ["ics2:정상"]  # 실패한 캘린더는 빠짐 → 기존 일정이 '지워짐'이 되지 않음
    assert len(warnings) == 1 and "고장" in warnings[0]
    assert events
