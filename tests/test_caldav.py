"""CalDAV 수집 (popme/collectors/caldav.py) — 가짜 iCloud 서버로 로그인 → 캘린더 찾기 → 일정 받기."""
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from popme.collectors import caldav

AUTH = "Basic " + base64.b64encode(b"me@example.com:abcd-efgh-ijkl-mnop").decode()
MS = '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="http://apple.com/ns/ical/">{}</d:multistatus>'


def resp(href, prop):
    return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{prop}</d:prop></d:propstat></d:response>"


EVENT = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:{uid}
DTSTART;VALUE=DATE:20261020
DTEND;VALUE=DATE:20261021
SUMMARY:{title}
LOCATION:한강공원
END:VEVENT
END:VCALENDAR
"""

ROUTES = {
    # 루트는 principal을 알려 준다 (iCloud처럼 상대 주소)
    ("PROPFIND", "/"): MS.format(resp("/", "<d:current-user-principal><d:href>/123/principal/</d:href></d:current-user-principal>")),
    # principal → 캘린더 집은 다른 주소 (iCloud는 pNN-caldav.icloud.com 절대 주소로 준다)
    ("PROPFIND", "/123/principal/"): MS.format(resp("/123/principal/",
        "<c:calendar-home-set><d:href>{base}/123/calendars/</d:href></c:calendar-home-set>")),
    ("PROPFIND", "/123/calendars/"): MS.format(
        resp("/123/calendars/", "<d:resourcetype><d:collection/></d:resourcetype>")
        + resp("/123/calendars/home/", "<d:displayname>집</d:displayname><d:resourcetype><d:collection/><c:calendar/>"
               "</d:resourcetype><c:supported-calendar-component-set><c:comp name=\"VEVENT\"/></c:supported-calendar-component-set>"
               "<a:calendar-color>#FF2968FF</a:calendar-color>")
        + resp("/123/calendars/work/", "<d:displayname>직장</d:displayname><d:resourcetype><d:collection/><c:calendar/>"
               "</d:resourcetype>")
        + resp("/123/calendars/tasks/", "<d:displayname>미리 알림</d:displayname><d:resourcetype><d:collection/><c:calendar/>"
               "</d:resourcetype><c:supported-calendar-component-set><c:comp name=\"VTODO\"/></c:supported-calendar-component-set>")
        + resp("/123/calendars/inbox/", "<d:resourcetype><d:collection/><c:schedule-inbox/></d:resourcetype>")),
    ("REPORT", "/123/calendars/home/"): MS.format(
        resp("/123/calendars/home/a.ics", "<c:calendar-data>" + EVENT.format(uid="a1", title="치과") + "</c:calendar-data>")
        + resp("/123/calendars/home/b.ics", "<c:calendar-data>" + EVENT.format(uid="b2", title="3시 회의") + "</c:calendar-data>")),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _dav(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Authorization") != AUTH:
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/old-home/":  # 리디렉션도 따라가야 한다
            self.send_response(301)
            self.send_header("Location", "/123/calendars/home/")
            self.end_headers()
            return
        body = ROUTES.get((self.command, self.path))
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.replace("{base}", f"http://127.0.0.1:{self.server.server_port}").encode()
        self.send_response(207)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_PROPFIND = do_REPORT = _dav


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_discover_lists_event_calendars_only(server):
    cals = caldav.discover("me@example.com", "abcd-efgh-ijkl-mnop", server)
    assert [(c["name"], c["url"], c["color"]) for c in cals] == [
        ("집", f"{server}/123/calendars/home/", "#FF2968"),
        ("직장", f"{server}/123/calendars/work/", ""),  # 지원 목록이 없으면 일정 캘린더로 본다
    ]  # 미리 알림(VTODO)·받은편지함은 빠진다


def test_discover_wrong_password(server):
    with pytest.raises(ValueError, match="앱 암호"):
        caldav.discover("me@example.com", "wrong", server)


def test_collect_reads_chosen_calendars(server, monkeypatch):
    monkeypatch.setattr(caldav.ics, "holiday_events", lambda cfg: [])
    home = {"name": "집", "url": f"{server}/old-home/"}  # 301 → /123/calendars/home/
    cfg = {"caldav": {"user": "me@example.com", "password": " abcd-efgh-ijkl-mnop ",  # 앞뒤 빈칸은 뺀다
                      "calendars": [home, {"name": "사라진", "url": f"{server}/123/calendars/gone/"}]}}
    warnings = []
    events, synced = caldav.collect(cfg, warnings=warnings)
    cid = caldav.calendar_id(home)
    assert synced == [cid]  # 실패한 캘린더는 synced에서 빠져서 기존 일정이 '지워짐'이 되지 않는다
    assert sorted(e["title"] for e in events) == ["3시 회의", "치과"]
    e = next(e for e in events if e["title"] == "치과")
    assert e["calendar_id"] == cid and e["id"] == cid.split(":")[0] + ":a1"
    assert e["all_day"] == 1 and e["start_at"].startswith("2026-10-20") and e["location"] == "한강공원"
    assert len(warnings) == 1 and "사라진" in warnings[0]


def test_calendar_id_is_stable_by_url():
    a = caldav.calendar_id({"name": "집", "url": "https://p1-caldav.icloud.com/1/calendars/home/"})
    assert a.endswith(":집") and a.startswith("cd")
    assert a.split(":")[0] == caldav.calendar_id({"name": "우리집", "url": "https://p1-caldav.icloud.com/1/calendars/home/"}).split(":")[0]
