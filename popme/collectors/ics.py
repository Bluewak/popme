"""ICS(iCal) 주소로 일정 읽기 (읽기 전용). 구글·iCloud·네이버·아웃룩 등 '비공개 주소'를 주는 캘린더 전부.

미니캘처럼 iCloud·구글과 동기화하는 앱도, 그 원본 캘린더의 ICS 주소를 넣으면 된다.
매번 전체를 받으므로 사라진 일정은 '지워짐'으로 표시된다. 공휴일은 구글 대한민국 공휴일 캘린더에서 받는다.
"""
import hashlib
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta, timezone

import icalendar

log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
HOLIDAY_URL = ("https://calendar.google.com/calendar/ical/"
               "ko.south_korea.official%23holiday%40group.v.calendar.google.com/public/basic.ics")


def fetch(url, timeout=30):
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 POPME"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _utc_iso(dt):
    if dt.tzinfo is None:  # 시간대 없는 시각은 한국 시간으로 본다
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).isoformat()


def _day_iso(d):
    """종일 일정 날짜 = 그 날짜의 UTC 자정 (TimeTree와 같은 약속, popme/agenda.py)."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


def _local_date(v):
    if isinstance(v, datetime):
        return (v.replace(tzinfo=KST) if v.tzinfo is None else v).astimezone(KST).date()
    return v


def _rules(comp, extra_ex=()):
    """RRULE·EXDATE → agenda.py가 펼칠 수 있는 문자열들. 반복은 날짜 단위로 펼치므로 날짜만 남긴다."""
    out = []
    rr = comp.get("RRULE")
    for r in (rr if isinstance(rr, list) else [rr] if rr else []):
        r = icalendar.vRecur(r)
        if "UNTIL" in r:
            r["UNTIL"] = [_local_date(u) for u in r["UNTIL"]]
        out.append("RRULE:" + r.to_ical().decode())
    exs = []
    raw = comp.get("EXDATE")
    for ex in (raw if isinstance(raw, list) else [raw] if raw else []):
        exs += [_local_date(x.dt) for x in ex.dts]
    exs += list(extra_ex)
    if out and exs:
        out.append("EXDATE:" + ",".join(f"{d:%Y%m%d}T000000" for d in sorted(set(exs))))
    return out


def _event(comp, uid, calendar_id, extra_ex=()):
    s = comp.get("DTSTART").dt
    e = comp.get("DTEND").dt if comp.get("DTEND") else None
    all_day = not isinstance(s, datetime)
    if all_day:
        last = (e - timedelta(days=1)) if isinstance(e, date) and e > s else s  # ICS 끝 날짜는 '다음 날'
        start_at, end_at = _day_iso(s), _day_iso(last)
    else:
        if e is None and comp.get("DURATION"):
            e = s + comp.get("DURATION").dt
        start_at, end_at = _utc_iso(s), _utc_iso(e or s)
    title = str(comp.get("SUMMARY") or "(제목 없음)").strip()
    location = str(comp.get("LOCATION") or "").strip() or None
    rec = _rules(comp, extra_ex)
    status = str(comp.get("STATUS") or "").upper()
    # DTSTAMP는 받을 때마다 바뀌는 서비스가 있어서, 내용으로 '바뀜'을 판단한다
    sig = hashlib.sha1(json.dumps([title, start_at, end_at, rec, location, status], ensure_ascii=False)
                       .encode()).hexdigest()[:16]
    return {"id": uid, "calendar_id": calendar_id, "title": title, "start_at": start_at, "end_at": end_at,
            "all_day": int(all_day), "updated_at": sig, "deleted": int(status == "CANCELLED"),
            "recurrences": json.dumps(rec), "location": location}


def parse(data, calendar_id, id_prefix):
    cal = icalendar.Calendar.from_ical(data)
    comps = [c for c in cal.walk("VEVENT") if c.get("DTSTART")]
    # 반복 일정 중 '이번 회차만 수정'한 것(RECURRENCE-ID)은 따로 넣고, 원래 회차는 빼 준다
    moved = {}
    for c in comps:
        if c.get("RECURRENCE-ID"):
            moved.setdefault(str(c.get("UID")), []).append(_local_date(c.get("RECURRENCE-ID").dt))
    events = []
    for c in comps:
        uid = str(c.get("UID") or hashlib.sha1(c.to_ical()).hexdigest())
        rid = c.get("RECURRENCE-ID")
        if rid:
            ev = _event(c, f"{id_prefix}{uid}@{_local_date(rid.dt):%Y%m%d}", calendar_id)
        else:
            ev = _event(c, f"{id_prefix}{uid}", calendar_id, moved.get(uid, ()))
        events.append(ev)
    return events


def collect(cfg, progress=lambda s: None, warnings=None):
    """반환: (일정 리스트, 전체를 다시 읽은 calendar_id 목록) — timetree.collect와 같은 모양.
    한 캘린더가 실패해도 나머지는 읽는다. 실패한 캘린더는 synced에서 빠지므로 기존 일정이 '지워짐'이 되지 않는다."""
    events, synced = [], []
    for i, c in enumerate(cfg.get("calendar", {}).get("ics", [])):
        name = c.get("name") or f"캘린더{i + 1}"
        if not (c.get("url") or "").strip():
            log.info("캘린더 %s: 주소가 비어 있어 건너뜀", name)
            continue
        cid = f"ics{i}:{name}"
        progress(f"캘린더 읽는 중: {name}")
        try:
            events += parse(fetch(c["url"]), cid, f"ics{i}:")
            synced.append(cid)
        except Exception as e:
            log.warning("캘린더 %s 읽기 실패: %s", name, e)
            if warnings is not None:
                warnings.append(f"'{name}' 캘린더를 못 읽었어요 ({e})")
    if cfg.get("calendar", {}).get("holidays", True):
        try:
            hol = parse(fetch(HOLIDAY_URL), "holiday:공휴일", "holiday:")
            for h in hol:
                h["all_day"] = 1
            events += hol
        except Exception as e:  # 공휴일은 없어도 됨
            log.warning("공휴일 캘린더 읽기 실패: %s", e)
    log.info("ICS: 캘린더 %d개, 일정 %d개", len(synced), len(events))
    return events, synced
