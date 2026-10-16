"""CalDAV로 일정 읽기 (읽기 전용). iCloud(아이폰 기본 캘린더) = Apple 계정 + 앱 암호.

ICS 비공개 주소와 달리 캘린더마다 주소를 따로 얻을 필요가 없다: 계정으로 로그인하면 캘린더 목록이 나오고,
그중 고른 것만 읽는다. 받은 일정은 ICS와 같은 VCALENDAR라서 ics.parse로 바꾼다 (반복·옮긴 회차·장소 그대로).
같은 표준이라 서버 주소만 바꾸면 네이버 등 다른 CalDAV 서비스도 읽을 수 있다.

    [calendar] source = "caldav"
    [caldav] server = "https://caldav.icloud.com", user = "Apple 계정 이메일", password = "앱 암호",
             calendars = [{ name = "집", url = "https://p00-caldav.icloud.com/.../calendars/home/" }]
"""
import base64
import hashlib
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from popme.collectors import ics

log = logging.getLogger(__name__)
ICLOUD = "https://caldav.icloud.com"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "a": "http://apple.com/ns/ical/"}

PRINCIPAL = '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
HOME = ('<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        '<d:prop><c:calendar-home-set/></d:prop></d:propfind>')
LIST = ('<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="http://apple.com/ns/ical/">'
        '<d:prop><d:displayname/><d:resourcetype/><c:supported-calendar-component-set/><a:calendar-color/></d:prop>'
        '</d:propfind>')
# 전체를 받는다 (ICS처럼): 기간을 자르면 기간 밖 일정이 '지워짐'으로 보인다
EVENTS = ('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
          '<d:prop><c:calendar-data/></d:prop>'
          '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/></c:comp-filter></c:filter>'
          '</c:calendar-query>')


class AuthError(Exception):
    pass


def _request(method, url, user, password, body, depth, timeout=30):
    """WebDAV 요청 → (최종 주소, XML 루트). PROPFIND·REPORT는 urllib이 리디렉션을 안 따라가서 직접 따라간다."""
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    for _ in range(5):
        req = urllib.request.Request(url, data=body.encode(), method=method, headers={
            "Authorization": "Basic " + auth, "Depth": str(depth), "User-Agent": "Mozilla/5.0 POPME",
            "Content-Type": "application/xml; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.geturl(), ET.fromstring(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            if e.code in (401, 403):
                raise AuthError(e.code) from None
            raise
    raise RuntimeError("리디렉션이 너무 많아요")


def _href(root, base, prop):
    """응답에서 <prop><href> 하나를 절대 주소로. prop은 "d:current-user-principal"처럼 이름공간까지."""
    el = root.find(f".//{prop}/d:href", NS)
    return urllib.parse.urljoin(base, el.text.strip()) if el is not None and el.text else None


def _is_event_calendar(resp):
    if resp.find(".//d:resourcetype/c:calendar", NS) is None:
        return False  # 받은편지함·보낸편지함·폴더
    comps = [c.get("name") for c in resp.findall(".//c:supported-calendar-component-set/c:comp", NS)]
    return not comps or "VEVENT" in comps  # 미리 알림(VTODO만)은 뺀다


def discover(user, password, server=ICLOUD):
    """계정으로 로그인해 일정 캘린더 목록 [{name, url, color}]. 계정이 틀리면 ValueError(사용자에게 보일 문장)."""
    user, password = user.strip(), password.replace(" ", "")
    try:
        base, root = _request("PROPFIND", server.rstrip("/") + "/", user, password, PRINCIPAL, 0)
        principal = _href(root, base, "d:current-user-principal")
        if not principal:  # 루트가 안 알려 주는 서버는 표준 주소로
            base, root = _request("PROPFIND", server.rstrip("/") + "/.well-known/caldav", user, password, PRINCIPAL, 0)
            principal = _href(root, base, "d:current-user-principal")
        if not principal:
            raise ValueError("캘린더 계정 정보를 찾지 못했어요. 서버 주소를 확인해 주세요.")
        base, root = _request("PROPFIND", principal, user, password, HOME, 0)
        home = _href(root, base, "c:calendar-home-set")
        if not home:
            raise ValueError("캘린더 목록 위치를 찾지 못했어요.")
        base, root = _request("PROPFIND", home, user, password, LIST, 1)
    except AuthError:
        raise ValueError("로그인하지 못했어요. 이메일이 앱 암호를 만든 그 Apple 계정 이메일인지 확인해 주세요 "
                         "(평소 메일과 다를 수 있어요). 앱 암호는 Apple 계정 비밀번호가 아니라 xxxx-xxxx-xxxx-xxxx예요.") from None
    except (urllib.error.URLError, OSError, ET.ParseError) as e:
        raise ValueError(f"캘린더 서버에 연결하지 못했어요: {e}") from None
    out = []
    for resp in root.findall("d:response", NS):
        href = resp.findtext("d:href", "", NS).strip()
        if not href or not _is_event_calendar(resp):
            continue
        name = (resp.findtext(".//d:displayname", "", NS) or "").strip() or "캘린더"
        color = (resp.findtext(".//a:calendar-color", "", NS) or "").strip()[:7]
        out.append({"name": name, "url": urllib.parse.urljoin(base, href), "color": color})
    return out


def calendar_id(c):
    """DB calendar_id = "<주소로 만든 짧은 ID>:<이름>" — 순서를 바꾸거나 이름을 고쳐도 일정이 섞이지 않게 주소로."""
    return f"cd{hashlib.sha1(c['url'].encode()).hexdigest()[:8]}:{c.get('name') or '캘린더'}"


def fetch_events(c, user, password):
    """캘린더 하나의 일정 전부 → ics.parse 결과."""
    base, root = _request("REPORT", c["url"], user, password.replace(" ", ""), EVENTS, 1, timeout=60)
    cid = calendar_id(c)
    prefix = cid.split(":", 1)[0] + ":"
    events = []
    for data in root.findall(".//c:calendar-data", NS):
        if data.text and data.text.strip():
            events += ics.parse(data.text, cid, prefix)
    return events


def collect(cfg, progress=lambda s: None, warnings=None):
    """ics.collect와 같은 모양: (일정, 전체를 다시 읽은 calendar_id 목록). 실패한 캘린더는 synced에서 빠진다."""
    dav = cfg.get("caldav", {})
    user, password = dav.get("user", ""), dav.get("password", "")
    events, synced = [], []
    for c in dav.get("calendars", []):
        if not (c.get("url") or "").strip():
            continue
        progress(f"캘린더 읽는 중: {c.get('name')}")
        try:
            events += fetch_events(c, user, password)
            synced.append(calendar_id(c))
        except Exception as e:
            msg = "앱 암호가 바뀌었거나 지워졌어요. 설정에서 다시 넣어 주세요" if isinstance(e, AuthError) else str(e)
            log.warning("캘린더 %s 읽기 실패: %s", c.get("name"), msg)
            if warnings is not None:
                warnings.append(f"'{c.get('name')}' 캘린더를 못 읽었어요 ({msg})")
    events += ics.holiday_events(cfg)
    log.info("CalDAV: 캘린더 %d개, 일정 %d개", len(synced), len(events))
    return events, synced
