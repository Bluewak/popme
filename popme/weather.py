"""날씨·미세먼지 (Open-Meteo, 키 불필요). 실패하면 None — 날씨 대사만 빠진다.

- 일별 예보(16일)·시간별 예보·대기질 예보(5일)를 좌표마다 3시간 캐시 (실패는 10분만).
- 시각이 있는 일정은 그 시각 앞뒤 시간별 예보를, 종일 일정은 하루 예보를 쓴다.
- 미세먼지는 환경부 등급(좋음·보통·나쁨·매우나쁨). 낮 시간(6~21시) 중 가장 나쁜 값으로 판단.
"""
import json
import logging
import time
import urllib.request
from datetime import timedelta

from popme import clock, location

log = logging.getLogger(__name__)
_cache = {}  # (종류, lat, lon) → (만료 시각, 데이터)

BASE = "latitude={lat:.2f}&longitude={lon:.2f}&timezone=auto"  # 날짜 구분은 예보 장소의 현지 시간
URLS = {
    "daily": "https://api.open-meteo.com/v1/forecast?" + BASE + "&forecast_days=16"
             "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    "hourly": "https://api.open-meteo.com/v1/forecast?" + BASE + "&forecast_days=16"
              "&hourly=weathercode,temperature_2m,precipitation_probability",
    "air": "https://air-quality-api.open-meteo.com/v1/air-quality?" + BASE + "&forecast_days=5&hourly=pm10,pm2_5",
}
RAIN_MIN = 40  # 강수확률이 이보다 낮으면 비·눈 코드가 있어도 '비 소식'이라 하지 않는다 (잠깐 이슬비 코드 때문에)
GAP_MIN = 10   # 일교차가 이 이상이면 '일교차 큼'
SNOW = set(range(71, 78)) | {85, 86}
RAIN = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))
AIR_GRADES = ["좋음", "보통", "나쁨", "매우나쁨"]
PM10_CUTS, PM25_CUTS = (30, 80, 150), (15, 35, 75)  # 환경부 기준 (㎍/㎥, 이 값 초과면 다음 등급)


def _parse_daily(j):
    d, out = j["daily"], {}
    for i, t in enumerate(d["time"]):
        code, hi, lo = d["weathercode"][i], d["temperature_2m_max"][i], d["temperature_2m_min"][i]
        if None in (code, hi, lo):
            continue  # 예보 끝자락(16일째)은 값이 비어 오기도 한다 — 그날만 빼고 나머지는 쓴다
        out[t] = {"code": code, "max": round(hi), "min": round(lo), "gap": round(hi - lo),
                  "rain": d["precipitation_probability_max"][i] or 0}
    return out


def _parse_hourly(j):
    h, out = j["hourly"], {}
    for i, t in enumerate(h["time"]):  # t = "2026-10-10T19:00"
        if h["weathercode"][i] is not None and h["temperature_2m"][i] is not None:
            out[t] = {"code": h["weathercode"][i], "temp": round(h["temperature_2m"][i]),
                      "rain": h["precipitation_probability"][i] or 0}
    return out


def air_grade(pm10, pm25):
    """미세먼지·초미세먼지 중 더 나쁜 쪽 등급."""
    idx = lambda v, cuts: sum(1 for c in cuts if v is not None and v > c)
    return AIR_GRADES[max(idx(pm10, PM10_CUTS), idx(pm25, PM25_CUTS))]


def _parse_air(j):
    h, worst = j["hourly"], {}
    for i, t in enumerate(h["time"]):
        if not 6 <= int(t[11:13]) <= 21:  # 밖에 다니는 낮 시간만
            continue
        pm10, pm25 = h["pm10"][i], h["pm2_5"][i]
        if pm10 is None and pm25 is None:
            continue
        w = worst.setdefault(t[:10], {"pm10": 0, "pm25": 0})
        w["pm10"], w["pm25"] = max(w["pm10"], round(pm10 or 0)), max(w["pm25"], round(pm25 or 0))
    return {day: {**v, "grade": air_grade(v["pm10"], v["pm25"])} for day, v in worst.items()}


PARSERS = {"daily": _parse_daily, "hourly": _parse_hourly, "air": _parse_air}


def _get(kind, lat, lon):
    key = (kind, round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    try:
        with urllib.request.urlopen(URLS[kind].format(lat=lat, lon=lon), timeout=10) as r:
            data, ttl = PARSERS[kind](json.load(r)), 3 * 3600
    except Exception as e:
        log.warning("날씨(%s) 가져오기 실패: %s", kind, e)
        data, ttl = {}, 600  # 실패는 10분만 기억 (절전에서 깨어난 직후 네트워크가 늦게 붙는 경우 등)
    _cache[key] = (time.time() + ttl, data)
    return data


def daily(lat, lon):
    return _get("daily", lat, lon)


def on(lat, lon, date_iso, hm=None):
    """그날 그곳 날씨. hm("19:00")이 있으면 그 시각 1시간 전~2시간 뒤 시간별 예보로 (at=hm)."""
    w = None
    if hm:
        hours = _get("hourly", lat, lon)
        h = int(hm[:2])
        rows = [hours[f"{date_iso}T{x:02d}:00"] for x in range(max(h - 1, 0), min(h + 3, 24))
                if f"{date_iso}T{x:02d}:00" in hours]
        if rows:
            temps = [r["temp"] for r in rows]
            w = {"code": max(r["code"] for r in rows), "max": max(temps), "min": min(temps), "gap": 0,
                 "rain": max(r["rain"] for r in rows), "at": hm}
    w = w or daily(lat, lon).get(date_iso)
    if w:
        air = _get("air", lat, lon).get(date_iso)
        if air:
            w = {**w, "air": air["grade"], "pm10": air["pm10"], "pm25": air["pm25"]}
    return w


def today(cfg):
    """지금 있는 곳의 오늘 날씨(+미세먼지) + place."""
    pos = location.current(cfg)
    if not pos:
        return None
    w = on(pos["lat"], pos["lon"], clock.today().isoformat())  # 캐시가 자정을 넘기면 맨 앞 날짜는 어제라 날짜로 고른다
    return {**w, "place": pos["name"]} if w else None


def summary(cfg):
    """화면 위쪽 날씨 칸: 지금 있는 곳의 오늘·내일 (말풍선은 오늘만 쓴다)."""
    pos = location.current(cfg)
    if not pos:
        return None
    days = []
    for i, label in enumerate(("오늘", "내일")):
        w = on(pos["lat"], pos["lon"], (clock.today() + timedelta(days=i)).isoformat())
        if w:
            cat = category(w)
            icon, desc = DESC[cat]
            days.append({"label": label, "icon": icon, "desc": desc, "min": w["min"], "max": w["max"],
                         "rain": w["rain"], "air": w.get("air"), "air_bad": air_bad(w)})
    return {"place": pos["name"], "days": days} if days else None


def category(w):
    """날씨 → 캐릭터 파일 대사 종류. WMO 코드: 71~77·85~86 눈, 51~67·80~82·95~99 비."""
    if not w:
        return None
    c, rain = w["code"], w["rain"]
    if c in SNOW and rain >= RAIN_MIN:
        return "weather_snow"
    if (c in RAIN and rain >= RAIN_MIN) or rain >= 60:
        return "weather_rain"
    if w["max"] >= 30:
        return "weather_hot"
    if w["min"] <= 0:
        return "weather_cold"
    if w.get("gap", 0) >= GAP_MIN:
        return "weather_gap"
    if c <= 2:
        return "weather_nice"
    return "weather_cloudy"


def air_bad(w):
    return bool(w) and w.get("air") in ("나쁨", "매우나쁨")


# (아이콘, 설명). 날씨별 조언 문구는 캐릭터 파일 [weather_advice]에
DESC = {"weather_snow": ("❄", "눈 소식"), "weather_rain": ("☂", "비 소식"), "weather_hot": ("☀", "많이 더움"),
        "weather_cold": ("🧣", "추움"), "weather_gap": ("🌡", "일교차 큼"), "weather_nice": ("☀", "맑음"),
        "weather_cloudy": ("☁", "흐림")}


def chip(w):
    """일정 옆에 붙일 짧은 표시: '☂ 60% 12~18°', 시각 있는 일정은 '19시 ☂ 60% 18°', 미세먼지 나쁘면 '😷나쁨'."""
    cat = category(w)
    icon, _ = DESC[cat]
    rain = f" {w['rain']}%" if cat in ("weather_rain", "weather_snow") else ""
    temp = f"{w['max']}°" if w["min"] == w["max"] else f"{w['min']}~{w['max']}°"
    at = f"{int(w['at'][:2])}시 " if w.get("at") else ""
    air = f" 😷{w['air']}" if air_bad(w) else ""
    return f"{at}{icon}{rain} {temp}{air}"
