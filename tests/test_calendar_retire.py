"""일정 출처를 바꾸거나 캘린더를 빼거나 이름을 고쳤을 때 (popme/db.py upsert_events·retire_calendars)."""
from popme import agenda, clock
from popme.db import DB

DAY = clock.today().isoformat()


def ev(i, cal, title="모임", deleted=0):
    return {"id": i, "calendar_id": cal, "title": title, "start_at": f"{DAY}T10:00:00+09:00",
            "end_at": f"{DAY}T11:00:00+09:00", "all_day": 0, "updated_at": "1", "deleted": deleted,
            "recurrences": "[]", "location": None}


def titles(db):
    return sorted(x["id"] for x in agenda.upcoming(db)[1])


def test_renamed_calendar_still_notices_deleted_events(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_events([ev("a", "ics0:집"), ev("b", "ics0:집")], ["ics0:집"])
    db.upsert_events([ev("a", "ics0:우리집")], ["ics0:우리집"])  # 이름을 고친 뒤 b를 원본에서 지움
    assert titles(db) == ["a"]
    assert db.q("SELECT calendar_id FROM events WHERE id='a'")[0]["calendar_id"] == "ics0:우리집"


def test_switching_source_drops_old_source_and_its_holidays(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_events([ev("123", "555:가족"), ev("holiday:9", "holiday:공휴일")], ["555:가족"])
    db.upsert_events([ev("ics0:x@g", "ics0:구글"), ev("holiday:2026_a@example.com", "holiday:공휴일")], ["ics0:구글"])
    assert db.retire_calendars("ics", {"ics0"}) == 2
    assert titles(db) == ["holiday:2026_a@example.com", "ics0:x@g"]


def test_removed_ics_calendar_and_none(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_events([ev("ics0:a", "ics0:첫째"), ev("ics1:b", "ics1:둘째")], ["ics0:첫째", "ics1:둘째"])
    assert db.retire_calendars("ics", {"ics0"}) == 1 and titles(db) == ["ics0:a"]
    assert db.retire_calendars("ics", {"ics0"}, holidays=False) == 0
    assert db.retire_calendars("none") == 1 and titles(db) == []


def test_timetree_keeps_its_calendars(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_events([ev("1", "555:가족"), ev("2", "777:회사"), ev("cd1:x", "cd1a2b3c4d:아이클라우드")], ["555:가족", "777:회사"])
    assert db.retire_calendars("timetree") == 1 and titles(db) == ["1", "2"]
