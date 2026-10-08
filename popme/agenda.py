"""일정 날짜 계산: 종일 일정은 날짜 그대로(UTC 자정 = 그 날짜), 시간 일정은 한국시간으로. 반복 일정은 펼친다."""
import json
import re
from datetime import date, datetime, timedelta, timezone

from dateutil.rrule import rrulestr

KST = timezone(timedelta(hours=9))


def _span(e):
    """일정의 (시작 날짜, 끝 날짜, 시작 시각 or None) — 날짜는 한국 달력 기준."""
    s = datetime.fromisoformat(e["start_at"])
    en = datetime.fromisoformat(e["end_at"] or e["start_at"])
    if e["all_day"]:
        return s.astimezone(timezone.utc).date(), en.astimezone(timezone.utc).date(), None
    s, en = s.astimezone(KST), en.astimezone(KST)
    end_day = (en - timedelta(seconds=1)).date() if en > s else s.date()  # 자정에 끝나면 전날까지
    return s.date(), max(end_day, s.date()), s.strftime("%H:%M")


def occurrences(e, first: date, last: date):
    """[first, last] 기간에 걸리는 (날짜, 시각, 여러날 표시) 목록."""
    d0, d1, hm = _span(e)
    length = (d1 - d0).days
    starts = [d0]
    rules = json.loads(e.get("recurrences") or "[]")
    if rules:
        base = datetime.combine(d0, datetime.min.time())
        rr = rrulestr("\n".join(rules), dtstart=base, forceset=True, ignoretz=True)
        lo = datetime.combine(first - timedelta(days=length), datetime.min.time())
        hi = datetime.combine(last, datetime.max.time())
        starts = [x.date() for x in rr.between(lo, hi, inc=True)]
    out = []
    for st in starts:
        for i in range(length + 1):
            day = st + timedelta(days=i)
            if first <= day <= last:
                note = f" ({i + 1}/{length + 1}일차)" if length else ""
                out.append((day, hm if i == 0 else None, note))
    return out


def upcoming(db, days_ahead=2):
    """오늘부터 days_ahead일 뒤까지의 일정 (날짜·시각 순)."""
    today = datetime.now(KST).date()
    last = today + timedelta(days=days_ahead)
    since = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    rows = db.q("SELECT * FROM events WHERE deleted=0 AND (start_at >= ? OR recurrences != '[]')", since)
    items = []
    for e in rows:
        for day, hm, note in occurrences(e, today, last):
            if not hm and not e["id"].startswith("holiday:") and (t := parse_time(e["title"])):
                hm = f"{t[0]:02d}:{t[1]:02d}"  # 종일 일정 제목에 적힌 시각 ('11시 회의')
            items.append({**e, "day": day, "time": hm, "note": note})
    items.sort(key=lambda x: (x["day"], x["time"] or "", x["title"]))
    return today, items


TIME_RE = re.compile(r"(오전|오후|아침|점심|저녁|밤)?\s*(\d{1,2})\s*시\s*(반|\d{1,2})?")


def parse_time(title):
    """종일 일정 제목에 적힌 시각 추정: '11시 회의' → 11:00, '점심 1시' → 13:00, '스터디 8시' → 08:00.
    오전/오후 표시가 없으면 1~7시는 오후로 본다."""
    m = TIME_RE.search(title or "")
    if not m:
        return None
    period, h, mm = m.group(1), int(m.group(2)), m.group(3)
    if h > 24:
        return None
    minute = 30 if mm == "반" else int(mm) if mm and int(mm) < 60 else 0
    if period in ("오후", "저녁", "밤") and h < 12:
        h += 12
    elif period == "점심" and h <= 4:
        h += 12
    elif period is None and 1 <= h <= 7:
        h += 12
    return h % 24, minute


def imminent(db, within_min=30):
    """지금부터 within_min분 안에 시작하는 오늘 일정 (시간 일정 + 제목에 시각이 적힌 종일 일정)."""
    now = datetime.now(KST)
    today, items = upcoming(db, 0)
    out = []
    for e in items:
        if e["id"].startswith("holiday:"):
            continue
        if e["time"]:
            hh, mi = map(int, e["time"].split(":"))
        else:
            t = parse_time(e["title"])
            if not t:
                continue
            hh, mi = t
        start = now.replace(hour=hh, minute=mi, second=0, microsecond=0)
        mins = (start - now).total_seconds() / 60
        if 0 <= mins <= within_min:
            out.append({"key": f"{e['id']}:{today}", "title": e["title"], "at": start.strftime("%H:%M"),
                        "mins": int(mins)})
    return out


def day_label(day, today):
    diff = (day - today).days
    name = {0: "오늘", 1: "내일", 2: "모레"}.get(diff, "")
    wd = "월화수목금토일"[day.weekday()]
    return f"{name} {day.month}/{day.day}({wd})".strip()
