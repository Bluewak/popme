"""캐릭터 창: Win32 레이어드 창 + 픽셀 단위 투명도(UpdateLayeredWindow).

WebView2는 GPU로 그려져 컬러키 투명이 안 먹으므로, 캐릭터만 별도 네이티브 창으로 그린다.
투명한 픽셀은 클릭이 아래로 통과하고, 캐릭터를 누르면 클릭/끌기를 구분해 콜백한다.
"""
import ctypes
import logging
import math
import threading
import time
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw

log = logging.getLogger(__name__)
u32, g32, k32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.DefWindowProcW.restype = LRESULT
u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [ctypes.c_uint]
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
g32.CreateDIBSection.restype = wintypes.HBITMAP
g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                 wintypes.HANDLE, wintypes.DWORD]
g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
u32.CreatePopupMenu.restype = wintypes.HMENU
u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.HWND, ctypes.c_void_p]
u32.DestroyMenu.argtypes = [wintypes.HMENU]
g32.CreateCompatibleDC.restype = wintypes.HDC
g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
u32.GetDC.restype = wintypes.HDC
u32.GetDC.argtypes = [wintypes.HWND]
HWND_TOPMOST = wintypes.HWND(-1)
WM_DESTROY, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_TIMER = 0x2, 0x201, 0x202, 0x200, 0x113
WM_RBUTTONUP = 0x205


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class PetWindow:
    """images: {"idle": PIL.Image, ...}. on_click(), on_moved((right, bottom)), on_right_click()."""

    def __init__(self, images, anchor_fn, on_click, on_moved, menu_items=lambda: [], on_petted=lambda: None,
                 on_bubble_click=lambda: None, size=118):
        self.images, self.anchor_fn = images, anchor_fn
        self.on_click, self.on_moved, self.menu_items = on_click, on_moved, menu_items
        self.hidden = False
        self.name = ""  # 말풍선 이름표 (app이 캐릭터 이름으로 채움)
        self.on_petted, self.on_bubble_click = on_petted, on_bubble_click
        self._speech = None      # 한 줄 말풍선 {bubble, t0, end}
        self._anchor = None      # 캐릭터 오른쪽 아래 모서리 (물리 px)
        self._surf = None        # 현재 DIB 크기
        self._win = (0, 0, 0, 0)  # 현재 창 위치·크기 (x, y, w, h)
        self._bubble_rect = None  # 창 기준 말풍선 영역 (클릭 판정)
        self.ready = threading.Event()
        self._tail = None        # 큰 말풍선·패널로 잇는 꼬리의 밑변 중심 (화면 좌표)
        self._tail_cache = None
        self._rub = {"x": None, "dir": 0, "flips": []}  # 쓰다듬기(좌우 문지르기) 감지
        self.logical = size
        self.state, self.badge, self.happy_until = "idle", False, 0.0
        self.hwnd = None
        self._drag = None
        self._cache = {}
        self._t0 = time.time()

    # --- 공개 API (아무 스레드에서나) ---
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def set_state(self, state):
        if state == "happy":
            self.happy_until = time.time() + 4
        self.state = state

    def set_badge(self, on):
        self.badge = on

    def rect(self):
        """캐릭터 영역 (말풍선 제외) 화면 좌표."""
        ax, ay = self._anchor
        return ax - self.px, ay - self.px, ax, ay

    def say(self, text, secs=6.0, side=False):
        """한 줄 말풍선: 통! 튀어나와 한 글자씩 쓰고, secs초 머문 뒤 사라진다.
        side=True면 왼쪽 옆에서 말한다 (위쪽에 패널·큰 말풍선이 열려 있을 때)."""
        from popme.bubble import Bubble
        if not self.ready.wait(10) or self.hidden:  # 창이 뜨기 전이면 잠깐 기다리고, 숨겨져 있으면 말 안 함
            return
        b = Bubble(text, self.scale, tail="right" if side else "down", name=self.name)
        typing = b.total / 28
        self._speech = {"bubble": b, "t0": time.time(), "end": time.time() + 0.2 + typing + secs, "side": side}

    def set_side(self, side):
        """말하는 도중 창이 열리고 닫히면 말풍선을 옆 ↔ 위로 옮긴다 (타이핑 진행은 유지)."""
        from popme.bubble import Bubble
        sp = self._speech
        if sp and sp["side"] != side:
            self._speech = {**sp, "bubble": Bubble(sp["bubble"].text, self.scale, tail="right" if side else "down", name=self.name),
                            "side": side, "moved": True}

    def set_tail(self, base, fill=(255, 246, 222)):
        """큰 말풍선·패널이 열렸을 때: 그 창 아래 가장자리(base, 화면 좌표)에서 머리까지 꼬리를 그린다."""
        if fill != getattr(self, "_tail_fill", None):
            self._tail_cache = None
        self._tail_fill = fill
        self._tail = base
        if base and self.hwnd:  # 꼬리가 패널 테두리를 덮도록 캐릭터 창을 맨 위로
            u32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x2 | 0x1 | 0x10)

    def _tail_layer(self):
        if not self._tail:
            return None
        ax, ay = self._anchor
        s = self.scale
        bx, by = self._tail[0] - ax, self._tail[1] - ay
        tx, ty = -self.px * 0.40, -self.px * 0.80
        key = (bx, by, round(tx), round(ty))
        if self._tail_cache and self._tail_cache[0] == key:
            return self._tail_cache[1]
        hw, lw, k = 13 * s, max(2, 2.5 * s), 3
        pts = [(bx - hw, by - 4 * s), (bx + hw, by - 4 * s), (tx, ty)]
        x0, y0 = int(min(p[0] for p in pts) - 4 * s), int(min(p[1] for p in pts) - 4 * s)
        x1, y1 = int(max(p[0] for p in pts) + 4 * s), int(max(p[1] for p in pts) + 4 * s)
        big = Image.new("RGBA", ((x1 - x0) * k, (y1 - y0) * k), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        P = [((x - x0) * k, (y - y0) * k) for x, y in pts]
        d.polygon(P, fill=(*self._tail_fill, 255))  # 열린 창의 바탕색과 같게 → 이어져 보임
        for a in (P[0], P[1]):  # 밑변은 선 없이 열어 둔다
            d.line([a, P[2]], fill=(59, 34, 25, 255), width=int(lw * k))
        img = big.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        self._tail_cache = (key, (img, (x0, y0)))
        return self._tail_cache[1]

    def hush(self):
        sp = self._speech
        if sp:
            sp["end"] = min(sp["end"], time.time() + 0.2)

    @property
    def speaking(self):
        return self._speech is not None

    # --- 내부 ---
    def _run(self):
        hinst = k32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)  # 참조 유지 (GC 방지)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="POPMEPet",
                       hCursor=u32.LoadCursorW(None, 32649))  # IDC_HAND
        u32.RegisterClassW(ctypes.byref(wc))
        ex = 0x80000 | 0x8 | 0x80  # WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW(작업표시줄에 안 뜸)
        self.hwnd = u32.CreateWindowExW(ex, "POPMEPet", "POPME pet", 0x80000000, 0, 0, 10, 10,
                                        None, None, hinst, None)  # WS_POPUP
        self.scale = u32.GetDpiForWindow(self.hwnd) / 96
        self.px = int(self.logical * self.scale)
        self.screen_dc = u32.GetDC(None)
        self.mem_dc = g32.CreateCompatibleDC(self.screen_dc)
        self._anchor = tuple(self.anchor_fn(self.scale))
        self._render()
        self.ready.set()
        u32.ShowWindow(self.hwnd, 4)
        u32.SetTimer(self.hwnd, 1, 50, None)  # 20fps
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    def _surface(self, w, h):
        """창 크기가 바뀔 때만 DIB를 새로 만든다."""
        if self._surf == (w, h):
            return
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        bits = ctypes.c_void_p()
        dib = g32.CreateDIBSection(self.screen_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        g32.SelectObject(self.mem_dc, dib)
        if self._surf:
            g32.DeleteObject(self.dib)
        self.dib, self.bits, self._surf = dib, bits, (w, h)

    def _base(self, state, h):
        key = (state, h)
        if key not in self._cache:
            img = self.images.get(state) or self.images["idle"]
            ratio = h / img.height
            self._cache[key] = img.resize((max(1, int(img.width * ratio)), h), Image.LANCZOS)
        return self._cache[key]

    def _frame(self):
        S, t = self.px, time.time() - self._t0
        state = "happy" if time.time() < self.happy_until else self.state
        canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        body_h = int(S * 0.80)
        sx = sy = 1.0
        dx = dy = 0
        angle = 0.0
        if state == "busy":
            angle = 6 * math.sin(2 * math.pi * t / 0.7)
        elif state == "happy":
            dy = -int(abs(math.sin(2 * math.pi * t / 1.0)) * S * 0.10)
        elif state == "alert":
            dx = int(2 * self.scale * math.sin(2 * math.pi * t / 0.25))
        else:  # idle, sleepy, asleep: 숨쉬기
            period = {"sleepy": 4.5, "asleep": 6.0}.get(state, 3.2)
            b = math.sin(2 * math.pi * t / period)
            sy, sx = 1 + 0.035 * b, 1 - 0.02 * b
        img = self._base("sleepy" if state == "asleep" else state, body_h)
        w, h = max(1, int(img.width * sx)), max(1, int(img.height * sy))
        if (w, h) != img.size:
            img = img.resize((w, h), Image.BILINEAR)
        if angle:
            img = img.rotate(angle, resample=Image.BICUBIC, expand=True,
                             center=(img.width / 2, img.height))
        x = (S - img.width) // 2 + dx
        y = S - img.height - int(2 * self.scale) + dy
        canvas.alpha_composite(img, (max(0, x), max(0, y)))
        d = ImageDraw.Draw(canvas)
        if self.badge:
            r = int(11 * self.scale)
            cx, cy = S - r - int(4 * self.scale), r + int(4 * self.scale)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(217, 119, 87, 255), outline=(255, 255, 255, 255),
                      width=max(1, int(2 * self.scale)))
            d.text((cx, cy), "!", fill=(255, 255, 255, 255), anchor="mm", font_size=int(15 * self.scale))
        if state == "asleep":  # Zzz가 떠오름
            for i, size in enumerate((10, 13, 16)):
                p = ((t / 3.0) + i / 3) % 1.0
                zx = int(S * (0.70 + 0.08 * i))
                zy = int(S * (0.35 - 0.25 * p) - i * 6 * self.scale)
                d.text((zx, zy), "z" if i < 2 else "Z", fill=(90, 70, 160, int(255 * (1 - p))),
                       font_size=int(size * self.scale))
        if state == "alert":
            p = (t % 1.4) / 1.4
            cx, cy = int(S * 0.2), int(S * 0.25 + p * 10 * self.scale)
            r = int(6 * self.scale)
            a = int(255 * (1 - p))
            d.polygon([(cx, cy - 2 * r), (cx - r, cy), (cx + r, cy)], fill=(120, 190, 255, a))
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(120, 190, 255, a))
        return canvas

    def _speech_layer(self):
        """(말풍선 이미지, 캐릭터 오른쪽아래 기준 좌상단 좌표) 또는 None. 등장·퇴장 애니메이션 포함."""
        sp = self._speech
        if not sp:
            return None
        now = time.time()
        if now >= sp["end"] + 0.3:
            self._speech = None
            return None
        b, t = sp["bubble"], now - sp["t0"]
        img = b.frame(int(max(0.0, t - 0.15) * 28), t)
        # 등장: 0.25초 동안 0.5배 → 살짝 커졌다(1.06) → 1배 (꼬리 끝 기준). 위↔옆으로 옮긴 경우엔 생략
        if t < 0.25 and not sp.get("moved"):
            p = t / 0.25
            sc = 0.5 + 0.5 * (1 + 2.2 * (p - 1) ** 3 + 1.2 * (p - 1) ** 2)  # ease-out-back
            img = img.resize((max(1, int(img.width * sc)), max(1, int(img.height * sc))), Image.BILINEAR)
            tip = (b.tip[0] * sc, b.tip[1] * sc)
        else:
            tip = b.tip
        if now > sp["end"]:  # 퇴장: 0.3초 페이드
            alpha = max(0.0, 1 - (now - sp["end"]) / 0.3)
            img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
        # 꼬리 끝: 위 말풍선은 머리 왼쪽 위, 옆 말풍선은 몸 왼쪽 가운데
        target = (-self.px * 0.86, -self.px * 0.45) if sp["side"] else (-self.px * 0.40, -self.px * 0.80)
        return img, (int(target[0] - tip[0]), int(target[1] - tip[1]))

    def _render(self):
        pet = self._frame()
        layer = self._speech_layer()
        tail = self._tail_layer()
        # 캐릭터 상자 (-px,-px)~(0,0)과 말풍선·꼬리 상자의 합집합 = 창 영역
        x0, y0, x1, y1 = -self.px, -self.px, 0, 0
        for extra in (layer, tail):
            if extra:
                (bx, by) = extra[1]
                x0, y0 = min(x0, bx), min(y0, by)
                x1, y1 = max(x1, bx + extra[0].width), max(y1, by + extra[0].height)
        w, h = x1 - x0, y1 - y0
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if tail:
            img.alpha_composite(tail[0], (tail[1][0] - x0, tail[1][1] - y0))
        img.alpha_composite(pet, (-self.px - x0, -self.px - y0))
        if layer:
            img.alpha_composite(layer[0], (layer[1][0] - x0, layer[1][1] - y0))
            self._bubble_rect = (layer[1][0] - x0, layer[1][1] - y0,
                                 layer[1][0] - x0 + layer[0].width, layer[1][1] - y0 + layer[0].height)
        else:
            self._bubble_rect = None
        self._surface(w, h)
        r, g, b, a = img.split()
        r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))  # premultiplied alpha
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()  # 메모리 순서 BGRA
        ctypes.memmove(self.bits, data, len(data))
        ax, ay = self._anchor
        self._win = (ax + x0, ay + y0, w, h)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        dst = wintypes.POINT(ax + x0, ay + y0)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        u32.UpdateLayeredWindow(self.hwnd, self.screen_dc, ctypes.byref(dst), ctypes.byref(size), self.mem_dc,
                                ctypes.byref(src), 0, ctypes.byref(blend), 2)  # ULW_ALPHA

    def _popup_menu(self):
        """우클릭 메뉴. menu_items(): [(이름, 함수) 또는 None(구분선)]"""
        items = self.menu_items()
        hm = u32.CreatePopupMenu()
        for i, it in enumerate(items, 1):
            if it is None:
                u32.AppendMenuW(hm, 0x800, 0, None)  # MF_SEPARATOR
            else:
                u32.AppendMenuW(hm, 0, i, it[0])
        p = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        u32.SetForegroundWindow(self.hwnd)  # 메뉴 바깥을 누르면 닫히게
        cmd = u32.TrackPopupMenu(hm, 0x100 | 0x2, p.x, p.y, 0, self.hwnd, None)  # RETURNCMD | RIGHTBUTTON
        u32.DestroyMenu(hm)
        if cmd:
            threading.Thread(target=items[cmd - 1][1], daemon=True).start()

    def hide(self):
        self.hidden = True
        self._speech = None
        u32.ShowWindow(self.hwnd, 0)

    def unhide(self):
        self.hidden = False
        self.name = ""  # 말풍선 이름표 (app이 캐릭터 이름으로 채움)
        u32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE

    def _on_bubble(self, p):
        r = self._bubble_rect
        if not r:
            return False
        x, y = p.x - self._win[0], p.y - self._win[1]
        return r[0] <= x < r[2] and r[1] <= y < r[3]

    def _detect_rub(self):
        """버튼 안 누르고 캐릭터 위에서 좌우로 4번 이상 방향을 바꾸면(1.5초 안) 쓰다듬기."""
        p = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        r = self._rub
        if r["x"] is not None:
            dx = p.x - r["x"]
            if abs(dx) >= 3 * self.scale:
                d = 1 if dx > 0 else -1
                if r["dir"] and d != r["dir"]:
                    now = time.time()
                    r["flips"] = [f for f in r["flips"] if now - f < 1.5] + [now]
                    if len(r["flips"]) >= 4:
                        r["flips"] = []
                        self.on_petted()
                r["dir"] = d
                r["x"] = p.x
        else:
            r["x"] = p.x

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_TIMER:
                if not self.hidden:
                    self._render()
                return 0
            if msg == WM_LBUTTONDOWN:
                p = wintypes.POINT()
                u32.GetCursorPos(ctypes.byref(p))
                self._drag = {"start": (p.x, p.y), "anchor": self._anchor, "moved": False,
                              "on_bubble": self._on_bubble(p)}
                u32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE and not self._drag:
                p = wintypes.POINT()
                u32.GetCursorPos(ctypes.byref(p))
                if not self._on_bubble(p):
                    self._detect_rub()
                return 0
            if msg == WM_MOUSEMOVE and self._drag:
                p = wintypes.POINT()
                u32.GetCursorPos(ctypes.byref(p))
                dx, dy = p.x - self._drag["start"][0], p.y - self._drag["start"][1]
                if self._drag["moved"] or abs(dx) + abs(dy) > 5:
                    self._drag["moved"] = True
                    self._anchor = (self._drag["anchor"][0] + dx, self._drag["anchor"][1] + dy)
                    self._render()
                return 0
            if msg == WM_LBUTTONUP and self._drag:
                u32.ReleaseCapture()
                drag, self._drag = self._drag, None
                if drag["moved"]:
                    self.on_moved(self._anchor)
                elif drag["on_bubble"]:
                    self.hush()
                    self.on_bubble_click()
                else:
                    self.on_click()
                return 0
            if msg == WM_RBUTTONUP:
                self._popup_menu()
                return 0
        except Exception:
            log.exception("펫 창 처리 실패")
        return u32.DefWindowProcW(hwnd, msg, wp, lp)
