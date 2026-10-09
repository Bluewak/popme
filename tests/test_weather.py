"""날씨 (popme/weather.py): Open-Meteo 응답 처리·캐시·판단 기준, 일정 장소 다시 뽑기."""
import io
import json
from datetime import datetime, timedelta, timezone

from popme import event_weather, weather
from popme.db import DB

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date().isoformat()  # 앱처럼 한국 시간 기준 (CI 서버는 UTC)


def daily_json(days):
    d = {"time": [], "weathercode": [], "temperature_2m_max": [], "temperature_2m_min": [],
         "precipitation_probability_max": []}
    for row in days:
        for k, v in zip(d, row):
            d[k].append(v)
    return {"daily": d}


def serve(monkeypatch, daily=None, hourly=None, air=None):
    """URL 종류별로 가짜 응답. 없는 종류는 실패로."""
    def urlopen(url, timeout=None):
        kind = "air" if "air-quality" in url else "hourly" if "hourly=" in url else "daily"
        data = {"daily": daily, "hourly": hourly, "air": air}[kind]
        if data is None:
            raise OSError("no data")
        return io.BytesIO(json.dumps(data).encode())
    weather._cache.clear()
    monkeypatch.setattr(weather.urllib.request, "urlopen", urlopen)


def w(code=3, hi=20, lo=15, rain=0):
    return {"code": code, "max": hi, "min": lo, "gap": hi - lo, "rain": rain}


def test_null_day_is_skipped_not_whole_forecast(monkeypatch):
    # 2026-10-10 실제 응답: 16일째 값이 비어 와서 16일치 전체가 버려졌던 버그
    serve(monkeypatch, daily=daily_json([(TODAY, 3, 25.2, 12.1, 0), ("2026-10-25", None, None, None, 14)]))
    days = weather.daily(37.57, 126.98)
    assert list(days) == [TODAY] and days[TODAY]["max"] == 25 and days[TODAY]["gap"] == 13


def test_failure_is_cached_briefly(monkeypatch):
    serve(monkeypatch)
    assert weather.daily(1, 2) == {}
    assert weather._cache[("daily", 1, 2)][0] - weather.time.time() <= 600  # 실패는 10분만


def test_today_uses_todays_date_not_first_key(monkeypatch):
    yesterday = (datetime.now(KST).date() - timedelta(days=1)).isoformat()
    serve(monkeypatch, daily=daily_json([(yesterday, 61, 10, 5, 90), (TODAY, 0, 22, 18, 0)]))  # 자정 넘긴 캐시처럼
    monkeypatch.setattr(weather.location, "current", lambda cfg: {"lat": 1, "lon": 2, "name": "서울"})
    t = weather.today({})
    assert t["code"] == 0 and t["place"] == "서울" and "air" not in t  # 대기질 실패해도 날씨는 나옴


def test_rain_needs_probability():
    assert weather.category(w(code=51, rain=20)) == "weather_cloudy"  # 잠깐 이슬비 코드만으론 비 소식 아님
    assert weather.category(w(code=61, rain=40)) == "weather_rain"
    assert weather.category(w(code=3, rain=70)) == "weather_rain"
    assert weather.category(w(code=71, hi=1, lo=-3, rain=50)) == "weather_snow"


def test_diurnal_range():
    assert weather.category(w(code=1, hi=25, lo=12)) == "weather_gap"
    assert weather.category(w(code=1, hi=22, lo=15)) == "weather_nice"
    assert weather.category(w(code=1, hi=31, lo=18)) == "weather_hot"  # 더위가 먼저


def test_air_grade_korean_standard():
    assert weather.air_grade(30, 15) == "좋음"
    assert weather.air_grade(81, 10) == "나쁨"       # 미세먼지만 나빠도
    assert weather.air_grade(20, 76) == "매우나쁨"   # 초미세먼지가 더 나쁘면 그쪽


def test_timed_event_uses_hours_around_it(monkeypatch):
    hours = [f"{TODAY}T{h:02d}:00" for h in range(24)]
    rain = [80 if 18 <= h <= 21 else 0 for h in range(24)]  # 저녁에만 비
    serve(monkeypatch, daily=daily_json([(TODAY, 61, 20, 10, 80)]),
          hourly={"hourly": {"time": hours, "weathercode": [61 if r else 1 for r in rain],
                             "temperature_2m": [15] * 24, "precipitation_probability": rain}},
          air={"hourly": {"time": hours, "pm10": [100] * 24, "pm2_5": [20] * 24}})
    morning = weather.on(1, 2, TODAY, "09:00")
    evening = weather.on(1, 2, TODAY, "19:00")
    assert weather.category(morning) != "weather_rain" and weather.category(evening) == "weather_rain"
    assert weather.chip(evening) == "19시 ☂ 80% 15° 😷나쁨"
    assert weather.on(1, 2, TODAY)["min"] == 10  # 종일 일정은 하루 예보


def test_event_place_reasked_when_location_changes(tmp_path, monkeypatch):
    db = DB(tmp_path / "t.db")
    db.upsert_events([{"id": "e1", "calendar_id": "c", "title": "모임", "start_at": f"{TODAY}T10:00:00+09:00",
                       "end_at": None, "all_day": 0, "updated_at": "1", "deleted": 0, "recurrences": "[]",
                       "location": None}])
    asked = []
    monkeypatch.setattr(event_weather.location, "current", lambda cfg: None)
    monkeypatch.setattr(event_weather.llm, "ask", lambda *a, **k: (asked.append(a[2]), '[{"id": "e1", "place": null, "query": null}]')[1])
    assert event_weather.refresh({}, db) == 1
    assert event_weather.refresh({}, db) == 0  # 그대로면 다시 안 물음
    db.upsert_events([{"id": "e1", "calendar_id": "c", "title": "모임", "start_at": f"{TODAY}T10:00:00+09:00",
                       "end_at": None, "all_day": 0, "updated_at": "2", "deleted": 0, "recurrences": "[]",
                       "location": "해운대"}])
    assert event_weather.refresh({}, db) == 1 and "해운대" in asked[-1]  # 장소 칸만 바뀌어도 다시 물음
