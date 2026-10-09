"""캐릭터의 말과 표정 고르기. 이름·말투·멘트·수치는 캐릭터 파일(TOML)에서 읽는다.

캐릭터 파일: config.toml [character] file (기본 character.toml) → 없으면 character.example.toml.
개인 캐릭터 파일은 .gitignore로 저장소에서 빠진다.
"""
import ctypes
import random
import re
import tomllib
from datetime import date, datetime

from popme import config

# 화면 문구 기본값 (캐릭터 파일 [ui]에 없을 때). {name} 캐릭터 이름, {user} 사용자 호칭, {n} 개수, {msg} 내용
UI_DEFAULTS = {
    "briefing_lead": "새 브리핑이 왔어요!<br>",
    "urgent_lead": "놓치면 안 되는 소식이에요!<br>",
    "schedule_lead": "",
    "warn_lead": "문제가 생겼어요.<br>",
    "today_events": "오늘 일정 <b>{n}개</b>예요.",
    "no_events": "오늘은 일정이 없어요.",
    "no_briefing": "브리핑은 아직이에요.",
    "panel_title": "의 브리핑",
    "plan_placeholder": "{name}에게 부탁: 금요일 3시 치과",
    "plan_thinking": "날짜를 확인하는 중이에요…",
    "plan_confirm": "이렇게 넣을까요?",
    "plan_fail": "잘 모르겠어요: {msg}",
    "add_fail": "일정을 못 넣었어요: {msg}",
}
# 캐릭터 파일에 없으면 쓰는 대사 (나중에 생긴 종류라 예전 캐릭터 파일엔 없을 수 있음)
LINE_DEFAULTS = {
    "ask_start": ["「{q}」… 생각해볼게요."],
    "ask_done": ["답을 찾아왔어요. 질문 탭을 봐주세요."],
    "ask_none": ["모아둔 자료에서는 못 찾았어요."],
    "ask_failed": ["답을 못 했어요: {msg}"],
    "weather_gap": ["오늘 일교차가 {gap}도예요. 아침 {min}도, 낮 {max}도래요. 겉옷 챙기세요."],
    "air_bad": ["오늘 {place} 미세먼지 {grade}이래요. 마스크 챙기세요."],
}
WEATHER_ADVICE_DEFAULTS = {"snow": "미끄럼 조심하세요.", "rain": "우산 챙기세요.", "hot": "물 챙기세요.",
                           "cold": "따뜻하게 입으세요.", "gap": "겉옷 챙기세요.", "nice": "좋은 날이에요.", "cloudy": ""}


def character_path():
    name = config.load().get("character", {}).get("file", "character.toml")
    p = config.ROOT / name
    return p if p.exists() else config.ROOT / "character.example.toml"


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """키보드·마우스 입력이 없었던 시간 (초)."""
    li = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000


def quiet_now():
    """전체 화면(게임·발표)이나 방해 금지 모드면 True. 5 = 알림 받을 수 있음."""
    state = ctypes.c_int(5)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:
        return False
    return state.value != 5


def eun(word):
    """받침 있으면 '은', 없으면 '는' (한강은 / 부산은 / 제주는)."""
    ch = (word or " ")[-1]
    if "가" <= ch <= "힣":
        return word + ("은" if (ord(ch) - 0xAC00) % 28 else "는")
    return word + "은"


def plain(html, limit=34):
    text = re.sub(r"<[^>]+>", "", html or "")
    text = re.sub(r"\s*—.*$", "", text).strip()  # '— 출처' 꼬리 제거
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


class Character:
    def __init__(self):
        with open(character_path(), "rb") as f:
            self.cfg = tomllib.load(f)
        self.lines = self.cfg["lines"]
        self.beh = self.cfg["behavior"]
        p = self.cfg.get("persona", {})
        self.name, self.user = p.get("name", "포피"), p.get("call_user", "")
        self.ui = {k: v.format(name=self.name, user=self.user, n="{n}", msg="{msg}")
                   for k, v in {**UI_DEFAULTS, **self.cfg.get("ui", {})}.items()}
        self.advice = {**WEATHER_ADVICE_DEFAULTS, **self.cfg.get("weather_advice", {})}
        self._used, self._used_day = set(), date.today()

    def _fresh(self, pool):
        if self._used_day != date.today():
            self._used, self._used_day = set(), date.today()
        left = [l for l in pool if l not in self._used] or pool
        return random.choice(left) if left else None

    def line(self, cat, **kw):
        pool = self.lines.get(cat) or LINE_DEFAULTS.get(cat, [])
        pick = self._fresh(pool)
        if not pick:
            return None
        self._used.add(pick)
        try:
            return pick.format(**kw)
        except (KeyError, IndexError):
            return None

    def today_line(self, today_lines):
        pick = self._fresh(today_lines)
        if pick:
            self._used.add(pick)
        return pick

    def drowsy(self, now=None):
        """점심 직후·밤늦게처럼 졸린 시간대인가 (캐릭터 파일 drowsy_times)."""
        t = (now or datetime.now()).strftime("%H:%M")
        for span in self.beh.get("drowsy_times", []):
            a, b = span.split("-")
            if (a <= t < b) if a <= b else (t >= a or t < b):  # 자정을 넘는 구간
                return True
        return False

    def expression(self):
        w = self.beh["expression_weights_drowsy"] if self.drowsy() and "expression_weights_drowsy" in self.beh \
            else self.beh["expression_weights"]
        return random.choices(list(w), weights=list(w.values()))[0]

    def time_band(self, now=None):
        h = (now or datetime.now()).hour
        if 5 <= h < 11:
            return "greet_morning"
        if 11 <= h < 14:
            return "greet_lunch"
        if 18 <= h < 22:
            return "greet_evening"
        if h >= 23 or h < 3:
            return "greet_night"
        return None
