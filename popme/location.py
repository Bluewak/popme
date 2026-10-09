"""위치: Windows 위치 서비스(Wi-Fi 기반) → 지역 이름. 지명 → 좌표.

- 현재 위치는 PowerShell의 System.Device.Location으로 읽는다 (설정 > 개인정보 > 위치 허용 필요).
- 좌표↔지명 변환은 OpenStreetMap Nominatim (무료, 초당 1회, User-Agent 필수). 보낼 때 좌표는 약 1km로 반올림.
- 실패하면 config.toml [weather]의 고정 지역을 쓴다.
"""
import json
import logging
import subprocess
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)
UA = {"User-Agent": "POPME-personal/0.1 (personal desktop widget)"}
_lock = threading.Lock()
_last_call = [0.0]
_cache = {"t": 0.0, "pos": None}
UNKNOWN_NAME = "현재 위치"  # 좌표는 받았는데 지역 이름을 못 받았을 때

PS = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher
if ($w.TryStart($false, [TimeSpan]::FromSeconds(10))) {
  for ($i = 0; $i -lt 30 -and $w.Position.Location.IsUnknown; $i++) { Start-Sleep -Milliseconds 300 }
  $l = $w.Position.Location
  if (-not $l.IsUnknown) { Write-Output ("{0},{1}" -f $l.Latitude, $l.Longitude) }
}
$w.Stop()
"""


def _nominatim(path, params):
    with _lock:  # 사용 정책: 초당 1회
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        url = f"https://nominatim.openstreetmap.org/{path}?" + urllib.parse.urlencode(
            {**params, "format": "json", "accept-language": "ko"})
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return json.load(r)


def windows_position():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True, text=True,
                             timeout=30, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        lat, lon = (float(v) for v in out.splitlines()[-1].split(","))
        return lat, lon
    except Exception as e:
        log.info("Windows 위치 못 받음: %s", e)
        return None


def reverse_name(lat, lon):
    """좌표 → '서울 마포구' 같은 짧은 지역 이름."""
    a = _nominatim("reverse", {"lat": round(lat, 2), "lon": round(lon, 2), "zoom": 12}).get("address", {})
    city = (a.get("city") or a.get("county") or a.get("state") or "")
    for suffix in ("특별자치시", "특별자치도", "광역시", "특별시"):
        city = city.replace(suffix, "")
    return " ".join(x for x in (city, a.get("borough") or a.get("city_district")) if x) or None


def current(cfg):
    """지금 있는 곳 {lat, lon, name}. 1시간 캐시."""
    if _cache["pos"] and time.time() - _cache["t"] < 3600:
        return _cache["pos"]
    w = cfg.get("weather", {})
    pos = None
    if w.get("use_windows_location", True):
        p = windows_position()
        if p:
            try:
                name = reverse_name(*p)
            except Exception as e:
                log.info("지역 이름 못 받음: %s", e)
                name = None
            # 이름을 못 받았을 때 설정 지역 이름을 붙이면, 출장 중엔 좌표와 이름이 어긋난다
            pos = {"lat": p[0], "lon": p[1], "name": name or UNKNOWN_NAME}
    if not pos and "lat" in w:
        pos = {"lat": w["lat"], "lon": w["lon"], "name": w.get("name", "")}
    _cache.update(t=time.time(), pos=pos)
    return pos


def geocode(db, query):
    """'서울 한강공원' → {lat, lon}. DB에 캐시 (못 찾은 것도 캐시해서 다시 묻지 않음)."""
    rows = db.q("SELECT lat, lon FROM geocache WHERE query=?", query)
    if rows:
        return rows[0] if rows[0]["lat"] is not None else None
    try:
        res = _nominatim("search", {"q": query, "countrycodes": "kr", "limit": 1})
    except Exception as e:
        log.info("지명 검색 실패 %s: %s", query, e)
        return None
    lat, lon = (float(res[0]["lat"]), float(res[0]["lon"])) if res else (None, None)
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?)", (query, lat, lon))
    return {"lat": lat, "lon": lon} if lat is not None else None
