"""화면 구석의 캐릭터 → 누르면 말풍선 → 한 번 더 누르면 자세한 패널. 트레이 아이콘 포함.

창 두 개:
- 캐릭터: Win32 레이어드 창 (popme/pet.py) — 픽셀 단위 투명, 끌어서 이동
- 말풍선·패널: pywebview 창 — 캐릭터 바로 위에 둥근 모서리로 잘라 띄움
"""
import ctypes
import ctypes.wintypes as wt
import html
import json
import logging
import os
import random
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime

import markdown
import pystray
import webview
from PIL import Image, ImageDraw

from popme import agenda, briefing, chrome, config, discovery, event_weather, planner, character, weather
from popme.collectors import timetree
from popme.jobs import Jobs, calendar_source
from popme.pet import PetWindow

log = logging.getLogger(__name__)
UI = config.ROOT / "popme" / "ui" / "index.html"
ASSETS = config.ROOT / "assets"
UI_STATE = config.DATA_DIR / "ui.json"
SIZES = {"bubble": (330, 200), "panel": (420, 640)}  # 논리 px. 말풍선 높이는 내용에 맞춰 바뀜
# 창이 열려 있어도 (옆으로) 말하는 '사건' 대사. 나머지(인사·잡담·잠꼬대)는 창이 닫혀 있을 때만
EVENT_LINES = {"collecting", "warn", "petted", "return_from_away", "event_added", "event_failed"}
PET_FILES = {"idle": "pet", "busy": "pet_busy", "happy": "pet_happy", "alert": "pet_alert", "sleepy": "pet_sleepy"}

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND] + [ctypes.c_int] * 4 + [ctypes.c_uint]
HWND_TOPMOST = wt.HWND(-1)


def _icon_image():
    if (ASSETS / "pet.ico").exists():  # 트레이 아이콘 = 캐릭터 얼굴
        return Image.open(ASSETS / "pet.ico").convert("RGBA").resize((64, 64), Image.LANCZOS)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(217, 119, 87))
    d.text((24, 18), "P", fill="white", font_size=28)
    return img


def _pet_images():
    imgs = {}
    for state, name in PET_FILES.items():
        for ext in ("png", "webp", "gif"):
            p = ASSETS / f"{name}.{ext}"
            if p.exists():
                imgs[state] = Image.open(p).convert("RGBA")
                break
    if "idle" not in imgs:  # 캐릭터 이미지가 없으면 주황 방울
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((20, 30, 180, 190), fill=(217, 119, 87, 255), outline=(60, 30, 20, 255), width=6)
        d.ellipse((65, 90, 85, 115), fill=(40, 25, 20, 255))
        d.ellipse((115, 90, 135, 115), fill=(40, 25, 20, 255))
        imgs["idle"] = img
    return imgs


def _inline_md(text):
    return re.sub(r"^<p>|</p>$", "", markdown.markdown(text).strip())


def _load_ui_state():
    try:
        return json.loads(UI_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _work_area():
    r = wt.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA (작업표시줄 제외)
    return r


def _hwnd(window):
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        pid, found = os.getpid(), []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(h, _):
            p = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(h, buf, 64)
            if p.value == pid and buf.value == "POPME":
                found.append(h)
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else None


class Api:
    """pywebview JS에서 부르는 메서드 (밑줄로 시작하면 JS에 노출되지 않음)."""

    def __init__(self):
        self._jobs = None
        self._window = None
        self._tray = None
        self._pet = None
        self._hwnd = None
        self._mode = "pet"
        self._fit_h = {"bubble": SIZES["bubble"][1]}
        self._unseen = False
        self._happy_until = self._alert_until = self._pet_cool_until = 0.0
        self._char = character.Character()
        self._said_today = {}  # 하루 한 번 대사 종류 → 마지막으로 말한 날짜

    # --- 모드 전환·배치 ---
    def set_mode(self, mode):
        if mode in ("bubble", "panel"):
            self._unseen = False
        if self._pet:  # 말하던 중이면 말풍선을 옆(창 열림) ↔ 위(창 닫힘)로 옮긴다
            self._pet.set_side(mode != "pet")
        self._mode = mode
        self._apply()
        return mode

    def fit(self, mode, height):
        """말풍선 높이를 내용에 맞춘다 (CSS px)."""
        self._fit_h[mode] = max(40, min(int(height) + 2, 420))
        if self._mode == mode:
            self._apply()

    def _apply(self):
        if not self._hwnd:
            return
        if self._mode == "pet":
            self._window.hide()
            self._pet.set_tail(None)
            return
        s = user32.GetDpiForWindow(self._hwnd) / 96
        w = int(SIZES[self._mode][0] * s)
        h = int(self._fit_h.get(self._mode, SIZES["panel"][1]) * s)
        left, top, right, bottom = self._pet.rect()
        px = right - left
        # 캐릭터 머리 위로 띄우고, 그 사이를 캐릭터 창이 꼬리로 잇는다 (pet.set_tail)
        work = _work_area()
        head_x, head_y = int(right - px * 0.40), int(bottom - px * 0.80)
        x = max(work.left, min(head_x + int(40 * s) - w, work.right - w))
        y = max(work.top, head_y - int(16 * s) - h)
        self._window.evaluate_js(f"setMode('{self._mode}')")
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, x, y, w, h, 0x0010)
        self._window.show()
        tail_x = min(max(head_x - int(6 * s), x + int(30 * s)), x + w - int(30 * s))
        self._pet.set_tail((tail_x, y + h), (255, 246, 222) if self._mode == "bubble" else (255, 251, 241))

    def _on_pet_click(self):
        if self._mode == "pet":
            self._window.evaluate_js("fillBubble()")
            self.set_mode("bubble")
        elif self._mode == "bubble":
            self.set_mode("panel")
        else:
            self.set_mode("pet")

    def _on_pet_moved(self, anchor):
        try:
            UI_STATE.write_text(json.dumps({"anchor": anchor}), encoding="utf-8")
        except OSError:
            pass
        if self._mode != "pet":
            self._apply()

    def _pet_anchor(self, scale):
        saved = _load_ui_state().get("anchor")
        if saved:
            return tuple(saved)
        work = _work_area()
        return work.right - int(8 * scale), work.bottom

    def _behavior_loop(self):
        """캐릭터의 표정·멘트 (캐릭터 파일 규칙). 0.5초마다 상태를 보고 정한다."""
        self._pet.ready.wait(15)
        beh = self._char.beh
        now = time.time()
        next_expr = now + random.uniform(*beh["expression_every_sec"])
        next_chat = now + 60 * random.uniform(*beh["chatter_every_min"])
        expr, was_away, was_running, last_warn = "idle", False, False, ()
        active_since, break_said, greeted = now, False, set()
        next_sleeptalk = now + 60 * random.uniform(3, 6)
        while True:
            try:
                now = time.time()
                st = self._jobs.state
                idle = character.idle_seconds()
                away = idle >= 60 * beh["sleepy_after_idle_min"]

                # 자리 비움 → 복귀
                if was_away and not away:
                    self._say("return_from_away")
                    active_since, break_said = now, False
                was_away = away
                if idle > 300:  # 5분 이상 쉬면 연속 작업 시간 리셋
                    active_since, break_said = now, False

                # 수집 시작 한마디
                if st["running"] and not was_running:
                    self._say("collecting")
                was_running = st["running"]
                # 수집이 경고와 함께 끝남 → 떨면서 한마디. RSS 피드 몇 개 실패 같은 사소한 경고는 넘어감
                serious = [w for w in st["warnings"] if not w.startswith("RSS 실패")]
                if serious and tuple(st["warnings"]) != last_warn and not st["running"]:
                    last_warn, self._alert_until = tuple(st["warnings"]), now + 15
                    first = serious[0]
                    self._say("warn", msg=first if len(first) <= 40 else first[:40] + "…")

                # 하품하는 동안 가끔 잠꼬대
                if away and now >= next_sleeptalk and self._mode == "pet" and not character.quiet_now():
                    self._say("sleepy")
                    next_sleeptalk = now + 60 * random.uniform(8, 15)
                elif not away:
                    next_sleeptalk = now + 60 * random.uniform(3, 6)  # 자리를 비우고 몇 분 뒤부터

                # 표정 우선순위: 기쁨(쓰다듬기·새 브리핑, 몇 초) > 수집 중 > 새 경고(15초) > 졸기 > 무작위 순환
                # (기쁨이 짧아서 맨 앞에 둬야 수집·경고에 묻히지 않는다)
                if now >= next_expr:
                    expr = self._char.expression()
                    next_expr = now + random.uniform(*beh["expression_every_sec"])
                if now < self._happy_until:
                    mood = "happy"
                elif st["running"]:
                    mood = "busy"
                elif now < self._alert_until:
                    mood = "alert"
                elif idle >= 60 * beh["asleep_after_idle_min"]:
                    mood = "asleep"
                elif away:
                    mood = "sleepy"
                else:
                    mood = expr
                if self._pet:
                    if mood != self._pet.state:
                        self._pet.set_state(mood)
                    self._pet.set_badge(self._unseen and self._mode == "pet")

                # 평소 멘트: 자리에 있고, 조용히 할 때가 아니고, 캐릭터만 떠 있을 때
                if not away and self._mode == "pet" and not self._pet.speaking and not character.quiet_now():
                    band = self._char.time_band()
                    key = (datetime.now().date(), band)
                    if band and key not in greeted:
                        greeted.add(key)
                        self._say(band)
                    elif now - active_since > 60 * beh["break_after_active_min"] and not break_said:
                        break_said = True
                        self._say("take_break")
                    elif now >= next_chat:
                        next_chat = now + 60 * random.uniform(*beh["chatter_every_min"])
                        self._chatter()
            except Exception:
                log.exception("캐릭터 행동 루프 오류")
            time.sleep(0.5)

    def _chatter(self):
        """상황에 맞는 멘트 종류를 가중치로 골라 한 줄 말한다."""
        db = self._jobs.db
        today_lines = briefing.today_character_lines(db)
        items, _ = briefing.events_block(db, self._jobs.cfg)
        today = [e for e in items if e["day"].startswith("오늘") and not e["holiday"]]
        tomorrow = [e for e in items if e["day"].startswith("내일") and not e["holiday"]]
        heads = self.get_briefing()["headlines"]
        options = [("cheer", 1, {})]
        # 하루 한 번씩만 하는 특별 대사: 요일, 날씨, 공휴일, 월초 (가중치 높게 → 그날 앞쪽에 나옴)
        d = datetime.now()
        fresh = lambda cat: self._said_today.get(cat) != d.date()
        wd = {0: "monday", 4: "friday", 5: "weekend", 6: "weekend"}.get(d.weekday())
        if wd and fresh(wd):
            options.append((wd, 3, {}))
        w = weather.today(self._jobs.cfg)
        wcat = weather.category(w)
        if wcat and fresh("weather"):
            options.append((wcat, 3, w))
        for e in items:
            if e["holiday"] and e["day"][:2] in ("오늘", "내일"):
                cat = "holiday_today" if e["day"].startswith("오늘") else "holiday_tomorrow"
                if fresh(cat):
                    options.append((cat, 4, {"name": e["title"]}))
        if self._char.drowsy():  # 졸린 시간대엔 하품 대사도 가끔
            options.append(("sleepy", 2, {}))
        if d.day == 1 and fresh("month_start"):
            options.append(("month_start", 4, {"month": d.month}))
        # 장소가 있는 앞으로 3일 일정 → 그날 그곳 날씨 (일정마다 하루 한 번, 비·눈이면 더 자주 뽑힘)
        try:
            today_d, ups = event_weather.upcoming_with_weather(db, 3)
        except Exception:
            ups = []
        for e in ups:
            w, key = e.get("weather"), (e["id"], e["day"])
            if not w or self._said_today.get(key) == d.date():
                continue
            icon, desc = weather.DESC[w["cat"]]
            advice = self._char.advice.get(w["cat"].removeprefix("weather_"), "")
            label = {0: "오늘", 1: "내일", 2: "모레"}.get((e["day"] - today_d).days) or agenda.day_label(e["day"], today_d)
            kw = {"day": label, "title": e["title"], "place": w["place"], "place_eun": character.eun(w["place"]),
                  "desc": desc, "min": w["min"], "max": w["max"], "rain": w["rain"], "advice": advice, "_key": key}
            options.append(("event_weather", 5 if w["cat"] in ("weather_rain", "weather_snow") else 3, kw))
            break
        if today_lines:
            options.append(("today", 3, {}))
        if today:
            e = random.choice(today)
            options.append(("schedule_today", 2, {"title": e["title"], "n": len(today)}))
        if tomorrow and datetime.now().hour >= 17:
            options.append(("schedule_tomorrow", 1, {"title": tomorrow[0]["title"]}))
        if heads:
            options.append(("briefing_tease", 2, {"headline": character.plain(random.choice(heads)["html"])}))
        if discovery.candidates(db, limit=1):
            options.append(("discovery", 1, {}))
        cat, _, kw = random.choices(options, weights=[o[1] for o in options])[0]
        key = kw.pop("_key", None) if isinstance(kw, dict) else None
        text = self._char.today_line(today_lines) if cat == "today" else self._char.line(cat, **kw)
        if text:
            self._said_today[key or ("weather" if cat.startswith("weather_") else cat)] = d.date()
            self._show_chat(text)

    def _say(self, cat, **kw):
        """창(큰 말풍선·패널)이 열려 있으면 사건 대사만 옆으로 말하고, 잡담은 건너뛴다."""
        if self._mode != "pet" and cat not in EVENT_LINES:
            return
        text = self._char.line(cat, **kw)
        if text:
            self._show_chat(text)

    def _show_chat(self, text):
        """한 줄 말풍선 — 캐릭터 창이 직접 그린다 (꼬리·그림자·타이핑). 누르면 큰 말풍선으로."""
        log.info("%s: %s", self._char.name, text)
        if self._pet:
            self._pet.say(text, self._char.beh["chatter_bubble_sec"], side=self._mode != "pet")

    def _on_bubble_click(self):
        if self._mode != "pet":  # 옆 말풍선을 누른 거면 그냥 닫기만
            return
        self._window.evaluate_js("fillBubble()")
        self.set_mode("bubble")

    def _on_petted(self):
        now = time.time()
        if now < self._pet_cool_until:  # 연속 쓰다듬기 방지
            return
        self._pet_cool_until = now + self._char.beh.get("pet_cooldown_sec", 3)
        self._happy_until = now + 3
        self._say("petted")  # 사건 대사라 창이 열려 있으면 옆으로

    def hide(self):
        self.set_mode("pet")

    def log(self, msg):
        log.info("ui: %s", msg)

    def _hide_all(self):
        self.set_mode("pet")
        self._pet.hide()

    def _show_all(self):
        self._pet.unhide()

    # --- 데이터 ---
    def get_state(self):
        return {**self._jobs.state, "mode": self._mode}

    def get_briefing(self):
        items, _ = briefing.events_block(self._jobs.db, self._jobs.cfg)
        agenda_items = [e for e in items if not e["day"].startswith("모레")]
        b = self._jobs.db.latest_briefing()
        if not b:
            return {"title": "아직 브리핑이 없어요", "agenda": agenda_items, "headlines": [], "details": ""}
        title, _, body = briefing.strip_schedule(b["markdown"]).partition("\n")
        payload = briefing.load_payload(b)
        if payload:  # 카드 형식: 화면이 카드를 직접 그림
            heads = briefing.headlines_from(payload["items"])
            return {
                "title": title.lstrip("# ").strip(),
                "agenda": agenda_items,
                "headlines": [{**h, "html": html.escape(h["md"])} for h in heads],
                "items": payload["items"],
                "details": "",
            }
        heads, details = briefing.split_headlines(body)  # 예전 글 형식
        _, details = briefing.pop_section(details, "캐릭터 멘트")  # 캐릭터 대사 목록은 화면에서 숨김
        return {
            "title": title.lstrip("# ").strip(),
            "agenda": agenda_items,
            "headlines": [{**h, "html": _inline_md(h["md"])} for h in heads],
            "details": markdown.markdown(details, extensions=["tables", "sane_lists"]),
        }

    def get_events(self):
        items, _ = briefing.events_block(self._jobs.db, self._jobs.cfg)
        return items

    def get_persona(self):
        """화면 문구용: 캐릭터 이름·호칭·문구 (캐릭터 파일 [ui])."""
        # can_add_event: TimeTree일 때만 일정 부탁(쓰기) 가능 — ICS는 읽기 전용
        return {"name": self._char.name, "user": self._char.user, "ui": self._char.ui,
                "can_add_event": calendar_source(self._jobs.cfg) == "timetree"}

    def plan_event(self, text):
        """부탁 문장 → 승인 카드용 일정 초안 (아직 TimeTree에 안 씀)."""
        try:
            return planner.plan(self._jobs.cfg, self._jobs.db, text)
        except Exception as e:
            log.exception("일정 해석 실패")
            return {"error": str(e)}

    def add_event(self, calendar_id, title, date):
        """승인 카드에서 [추가]를 눌렀을 때만 TimeTree에 쓴다."""
        from playwright.sync_api import sync_playwright
        port = int(self._jobs.cfg.get("chrome", {}).get("port", 9333))
        try:
            chrome.ensure_chrome(port)
            with sync_playwright() as p:
                ctx = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}").contexts[0]
                ev = timetree.create_event(ctx, self._jobs.cfg, calendar_id, title, date)
            name = next((c["name"] for c in planner.calendar_names(self._jobs.db) if c["id"] == str(calendar_id)), "")
            self._jobs.db.upsert_events([timetree._event(ev, calendar_id, name)])
            self._say("event_added", title=title, date=f"{int(date[5:7])}/{int(date[8:10])}")
            return {"ok": True}
        except Exception as e:
            log.exception("일정 추가 실패")
            self._say("event_failed", msg=str(e)[:40])
            return {"ok": False, "error": str(e)}

    def get_candidates(self):
        return discovery.candidates(self._jobs.db, limit=15)

    def set_candidate(self, handle, status):
        self._jobs.db.set_candidate_status(handle, status)
        return True

    def run_now(self):
        return self._jobs.run_full()

    def rebuild_briefing(self):
        return self._jobs.run_full(collect=False)

    def ask(self, question):
        try:
            md = briefing.answer(self._jobs.cfg, self._jobs.db, question)
            return markdown.markdown(md, extensions=["tables", "sane_lists"])
        except Exception as e:
            log.exception("질문 실패")
            return f"<p class='err'>실패: {e}</p>"

    def open_url(self, url):
        webbrowser.open(url)

    def open_chrome_login(self):
        threading.Thread(target=chrome.open_for_login,
                         args=(int(self._jobs.cfg.get("chrome", {}).get("port", 9333)),
                               calendar_source(self._jobs.cfg) == "timetree"), daemon=True).start()

    def open_folder(self):
        os.startfile(config.BRIEFING_DIR)

    # --- 캐릭터가 먼저 말 걸기 ---
    def _alert(self, kind, text, lead=None):
        self._happy_until = time.time() + 5
        if kind == "briefing" or kind == "urgent":
            self._unseen = True
        if self._pet and self._pet.hidden:  # 숨겨 둔 동안은 Windows 알림으로만
            if self._tray:
                self._tray.notify(re.sub(r"<[^>]+>", " ", (lead or "") + text).strip(), "POPME")
            return
        try:
            self._window.evaluate_js(f"showAlert({json.dumps({'kind': kind, 'text': text, 'lead': lead})})")
            if self._mode != "panel":
                self.set_mode("bubble")
        except Exception:
            log.exception("알림 표시 실패")

    def _on_job(self, kind, msg):
        if kind == "briefing":
            data = self.get_briefing()
            urgent = [h for h in data["headlines"] if h["urgent"]]
            pick = (urgent or data["headlines"] or [{"html": msg}])[0]
            done = self._char.line("collected")  # "다 모았어요~! 브리핑… 보실래요~?"
            self._alert("urgent" if urgent else "briefing", pick["html"],
                        lead=None if urgent or not done else done + "<br>")
        else:
            self._alert("warn", "", lead=self._char.line("warn", msg=msg) or self._char.ui["warn_lead"] + msg)
            if self._tray:
                self._tray.notify(msg, "POPME")

    def _alert_loop(self):
        notified = set()
        while True:
            try:
                for e in agenda.imminent(self._jobs.db, 30):
                    if e["key"] not in notified:
                        notified.add(e["key"])
                        self._alert("schedule", f"{e['mins']}분 뒤 <b>{e['at']}</b> · {e['title']}")
            except Exception:
                log.exception("일정 알림 확인 실패")
            time.sleep(30)


def _single_instance():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 47321))
    except OSError:
        sys.exit(0)
    return s  # 프로세스가 살아 있는 동안 포트를 잡아 둔다


def main():
    _lock = _single_instance()
    api = Api()
    jobs = Jobs(notify=lambda kind, msg: threading.Thread(target=api._on_job, args=(kind, msg), daemon=True).start())
    api._jobs = jobs

    window = webview.create_window(
        "POPME", url=str(UI), js_api=api, width=SIZES["panel"][0], height=SIZES["panel"][1],
        frameless=True, easy_drag=False, on_top=True, resizable=False, hidden=True, min_size=(80, 80),
    )
    api._window = window

    # 펫 콜백은 펫 창 메시지 루프를 막지 않게 별도 스레드에서
    bg = lambda f: (lambda *a: threading.Thread(target=f, args=a, daemon=True).start())
    name = api._char.name

    def quit_app(*_):
        tray.stop()
        window.destroy()

    # 캐릭터 우클릭 메뉴
    pet_menu = lambda: [
        ("브리핑 열기", lambda: api.set_mode("panel")),
        ("지금 수집", jobs.run_full),
        None,
        (f"{name} 숨기기 (트레이에서 다시 보이기)", api._hide_all),
        ("종료", quit_app),
    ]
    pet = PetWindow(_pet_images(), api._pet_anchor, on_click=bg(api._on_pet_click), on_moved=bg(api._on_pet_moved),
                    menu_items=pet_menu, on_petted=bg(api._on_petted), on_bubble_click=bg(api._on_bubble_click))
    pet.name = name  # 말풍선 이름표
    api._pet = pet

    tray = pystray.Icon("POPME", _icon_image(), "POPME", menu=pystray.Menu(
        pystray.MenuItem(f"{name} 보이기", lambda i, _: api._show_all(), default=True),
        pystray.MenuItem(f"{name} 숨기기", lambda i, _: api._hide_all()),
        pystray.MenuItem("브리핑 열기", lambda i, _: (api._show_all(), api.set_mode("panel"))),
        pystray.MenuItem("지금 수집", lambda i, _: jobs.run_full()),
        pystray.MenuItem("브리핑만 다시 만들기", lambda i, _: jobs.run_full(collect=False)),
        pystray.MenuItem("전용 Chrome 열기 (로그인)", lambda i, _: api.open_chrome_login()),
        pystray.MenuItem("브리핑 폴더 열기", lambda i, _: api.open_folder()),
        pystray.MenuItem("종료", quit_app),
    ))
    api._tray = tray

    def background():
        window.events.loaded.wait(15)
        for _ in range(50):
            api._hwnd = _hwnd(window)
            if api._hwnd:
                break
            time.sleep(0.1)
        # Windows 11이 그려주는 둥근 모서리 + 캐릭터 외곽선 색 테두리 (WebView2는 창 모양 자르기가 안 먹음)
        dwm = ctypes.windll.dwmapi
        for attr, val in ((33, 2), (34, 59 | 34 << 8 | 25 << 16)):  # CORNER_PREFERENCE=ROUND, BORDER_COLOR
            v = ctypes.c_int(val)
            dwm.DwmSetWindowAttribute(wt.HWND(api._hwnd), attr, ctypes.byref(v), 4)
        pet.start()

        def warm_up():  # 시작할 때 위치·일정 장소를 미리 확인 (없으면 수집 후에 됨)
            try:
                event_weather.refresh(jobs.cfg, jobs.db)
            except Exception:
                log.exception("일정 장소 확인 실패")

        for target in (tray.run, jobs.morning_loop, api._alert_loop, api._behavior_loop, warm_up):
            threading.Thread(target=target, daemon=True).start()

    webview.start(background)
