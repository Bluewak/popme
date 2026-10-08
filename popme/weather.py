"""날씨 (Open-Meteo, 키 불필요). 좌표별 16일 예보를 3시간 캐시. 실패하면 None — 날씨 대사만 빠진다."""
import json
import logging
import time
import urllib.request

from popme import location

log = logging.getLogger(__name__)
_cache = {}  # (lat, lon) → (시각, {날짜: 날씨})

URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat:.2f}&longitude={lon:.2f}"
       "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
       "&timezone=Asia%2FSeoul&forecast_days=16")


def daily(lat, lon):
    key = (round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 3 * 3600:
        return hit[1]
    try:
        with urllib.request.urlopen(URL.format(lat=lat, lon=lon), timeout=10) as r:
            d = json.load(r)["daily"]
        days = {t: {"code": d["weathercode"][i], "max": round(d["temperature_2m_max"][i]),
                    "min": round(d["temperature_2m_min"][i]), "rain": d["precipitation_probability_max"][i] or 0}
                for i, t in enumerate(d["time"])}
    except Exception as e:
        log.warning("날씨 가져오기 실패: %s", e)
        days = {}
    _cache[key] = (time.time(), days)
    return days


def on(lat, lon, date_iso):
    return daily(lat, lon).get(date_iso)


def today(cfg):
    """지금 있는 곳의 오늘 날씨 + place."""
    pos = location.current(cfg)
    if not pos:
        return None
    days = daily(pos["lat"], pos["lon"])
    if not days:
        return None
    return {**days[min(days)], "place": pos["name"]}


def category(w):
    """날씨 → 캐릭터 파일 대사 종류. WMO 코드: 71~77·85~86 눈, 51~67·80~82·95~99 비."""
    if not w:
        return None
    c = w["code"]
    if c in range(71, 78) or c in (85, 86):
        return "weather_snow"
    if c in range(51, 68) or c in range(80, 83) or c >= 95 or w["rain"] >= 60:
        return "weather_rain"
    if w["max"] >= 30:
        return "weather_hot"
    if w["min"] <= 0:
        return "weather_cold"
    if c <= 2:
        return "weather_nice"
    return "weather_cloudy"


# (아이콘, 설명). 날씨별 조언 문구는 캐릭터 파일 [weather_advice]에
DESC = {"weather_snow": ("❄", "눈 소식"), "weather_rain": ("☂", "비 소식"), "weather_hot": ("☀", "많이 더움"),
        "weather_cold": ("🧣", "추움"), "weather_nice": ("☀", "맑음"), "weather_cloudy": ("☁", "흐림")}


def chip(w):
    """일정 옆에 붙일 짧은 표시: '☂ 60% 12~18°'"""
    icon, desc = DESC[category(w)]
    rain = f" {w['rain']}%" if category(w) == "weather_rain" else ""
    return f"{icon}{rain} {w['min']}~{w['max']}°"
