"""TimeTree 수집 (읽기 전용).

공식 API는 2023-12-22 종료. TimeTree 웹은 일정을 브라우저 내부 DB에 캐시하고
서버에는 변경분(events?since=...)만 물어서, 페이지를 열어도 일정이 네트워크로 지나가지 않는다.
그래서 로그인된 페이지 안에서, 웹 앱이 쓰는 것과 같은 헤더로 캘린더별 전체 동기화(GET)를 직접 요청한다.
공휴일은 웹 앱이 받아오는 memorialdays 응답을 가로챈다.
"""
import json
import logging
from datetime import datetime, timezone

from popme.collectors import NeedLogin, dump_raw

log = logging.getLogger(__name__)
APP_HEADERS = ("x-timetreea", "x-csrf-token")

SYNC_JS = """async ([id, h]) => {
  const out = []; let since = 0;
  for (let i = 0; i < 50; i++) {
    const r = await fetch(`/api/v1/calendar/${id}/events/sync?since=${since}`, {headers: h});
    if (!r.ok) return {error: r.status};
    const d = await r.json(); out.push(...d.events);
    if (!d.chunk) break; since = d.since;
  }
  return {events: out};
}"""


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms is not None else None


def _event(e, calendar_id, calendar_name):
    return {
        "id": str(e["id"]),
        "calendar_id": f"{calendar_id}:{calendar_name}",
        "title": e.get("title") or "(제목 없음)",
        "start_at": _iso(e.get("start_at")),
        "end_at": _iso(e.get("end_at")),
        "all_day": int(bool(e.get("all_day"))),
        "updated_at": str(e.get("updated_at") or ""),
        "deleted": int(bool(e.get("deactivated_at"))),
        "recurrences": json.dumps(e.get("recurrences") or []),
        "location": e.get("location") or None,
    }


def _session(ctx, cfg):
    """TimeTree 웹을 열고, 웹 앱이 쓰는 요청 헤더(x-timetreea, x-csrf-token)를 얻는다."""
    page = ctx.new_page()
    headers = {}
    page.on("request", lambda r: headers.update({k: v for k, v in r.headers.items() if k in APP_HEADERS})
            if "timetreeapp.com/api/" in r.url else None)
    page.goto(cfg.get("timetree", {}).get("url", "https://timetreeapp.com/calendars"),
              wait_until="domcontentloaded", timeout=45000)
    for _ in range(20):
        if "x-timetreea" in headers:
            break
        page.wait_for_timeout(500)
    if "signin" in page.url or "login" in page.url:
        page.close()
        raise NeedLogin("TimeTree 로그인이 필요해요. 전용 Chrome에서 로그인해 주세요.")
    if "x-timetreea" not in headers:
        page.close()
        raise RuntimeError("TimeTree 웹 요청 헤더를 못 찾았어요 (웹 앱 구조 변경 가능성)")
    return page, headers


FETCH_JS = """async ([url, method, h, body]) => {
  const r = await fetch(url, {method, headers: {...h, "content-type": "application/json"},
                              body: body ? JSON.stringify(body) : undefined, credentials: "include"});
  const text = await r.text();
  return {status: r.status, body: text ? JSON.parse(text) : {}};
}"""


def calendars(ctx, cfg):
    page, headers = _session(ctx, cfg)
    try:
        res = page.evaluate(FETCH_JS, ["/api/v2/calendars", "GET", headers, None])
        return [{"id": c["id"], "name": c["name"]} for c in res["body"].get("calendars", [])
                if not c.get("deactivated_at")]
    finally:
        page.close()


def create_event(ctx, cfg, calendar_id, title, date, silent=False):
    """종일 일정 추가 (사용자 습관: 시간은 제목에). date = 'YYYY-MM-DD'. 반환: 만들어진 일정 JSON."""
    start = int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp() * 1000)
    body = {"title": title, "all_day": True, "start_at": start, "end_at": start}
    if silent:
        body["silent"] = True  # 공유 멤버에게 알림 안 보냄 (웹 앱 내부 옵션)
    page, headers = _session(ctx, cfg)
    try:
        res = page.evaluate(FETCH_JS, [f"/api/v1/calendar/{calendar_id}/event", "POST", headers, body])
    finally:
        page.close()
    if res["status"] >= 300:
        raise RuntimeError(f"TimeTree 일정 추가 실패 ({res['status']}): {str(res['body'])[:200]}")
    log.info("TimeTree 일정 추가: %s %s", date, title)
    return res["body"].get("event", res["body"])


def delete_event(ctx, cfg, calendar_id, event_id):
    page, headers = _session(ctx, cfg)
    try:
        res = page.evaluate(FETCH_JS, [f"/api/v1/calendar/{calendar_id}/event/{event_id}", "DELETE", headers, None])
    finally:
        page.close()
    if res["status"] >= 300:
        raise RuntimeError(f"TimeTree 일정 삭제 실패 ({res['status']}): {str(res['body'])[:200]}")
    log.info("TimeTree 일정 삭제: %s", event_id)


def collect(ctx, cfg, progress=lambda s: None):
    """반환: (일정 리스트, 전체 동기화한 calendar_id 목록)"""
    url = cfg.get("timetree", {}).get("url", "https://timetreeapp.com/calendars")
    progress("TimeTree 확인 중")
    page = ctx.new_page()
    headers, holidays = {}, []

    def on_request(r):
        if "timetreeapp.com/api/" in r.url:
            headers.update({k: v for k, v in r.headers.items() if k in APP_HEADERS})

    def on_response(r):
        if "/api/v2/memorialdays" in r.url:
            holidays.append(r)

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(7000)
        if "signin" in page.url or "login" in page.url:
            raise NeedLogin("TimeTree 로그인이 필요해요. 전용 Chrome에서 로그인해 주세요.")
        if "x-timetreea" not in headers:
            raise RuntimeError("TimeTree 웹 요청 헤더를 못 찾았어요 (웹 앱 구조 변경 가능성)")

        cals = page.evaluate("h => fetch('/api/v2/calendars', {headers: h}).then(r => r.json())", headers)
        events, synced = [], []
        for c in cals.get("calendars", []):
            if c.get("deactivated_at"):
                continue
            res = page.evaluate(SYNC_JS, [c["id"], headers])
            if "error" in res:
                log.warning("TimeTree 캘린더 %s 동기화 실패: %s", c["name"], res["error"])
                continue
            synced.append(f"{c['id']}:{c['name']}")
            events += [_event(e, c["id"], c["name"]) for e in res["events"]]

        for r in holidays:
            try:
                data = r.json()
            except Exception:
                continue
            for h in data.get("memorialdays", []):
                ev = _event(h, "holiday", "공휴일")
                ev["id"], ev["all_day"] = f"holiday:{h['id']}", 1
                events.append(ev)
        dump_raw("timetree", "events", events[:20], keep=5)
    finally:
        page.close()
    log.info("TimeTree: 캘린더 %d개, 일정 %d개", len(synced), len(events))
    return events, synced
