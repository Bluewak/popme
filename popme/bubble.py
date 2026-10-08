"""캐릭터의 한 줄 말풍선 그리기 (PIL).

디자인: 게임 대화창 틀(크림색 상자 + 캐릭터 이름표 + ▼) + 손그림 외곽선(캐릭터 선과 같은 갈색).
몸통과 꼬리를 한 모양(mask)으로 합친 뒤 넓혀서 외곽선을 만들므로 꼬리 이음새가 자연스럽다.
3배로 크게 그려 줄여서 가장자리를 매끄럽게 한다.
"""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from popme import config

FONT_PATH = config.ROOT / "assets" / "fonts" / "Jua-Regular.ttf"
CREAM, BROWN, ORANGE, INK = (255, 246, 222), (59, 34, 25), (232, 134, 90), (82, 56, 42)
SS = 3  # 슈퍼샘플링 배율


def _font(px):
    try:
        return ImageFont.truetype(str(FONT_PATH), px)
    except OSError:
        return ImageFont.truetype("malgun.ttf", px)


class MixFont:
    """주아체에 없는 글자(「」, · 등)는 맑은 고딕으로 대신 그린다."""

    def __init__(self, px):
        self.main, self.alt = _font(px), ImageFont.truetype("malgun.ttf", px)
        self._px = px
        self._notdef = self._glyph("\U0010fffd")  # 없는 글자는 이 모양(□)으로 그려진다
        self._has = {}

    def _glyph(self, ch):
        im = Image.new("L", (self._px * 2, self._px * 2), 0)
        ImageDraw.Draw(im).text((0, 0), ch, font=self.main, fill=255)
        return im.tobytes()

    def pick(self, ch):
        if ch not in self._has:
            self._has[ch] = ch.isspace() or self._glyph(ch) != self._notdef
        return self.main if self._has[ch] else self.alt

    def runs(self, text):
        out = []
        for ch in text:
            f = self.pick(ch)
            if out and out[-1][1] is f:
                out[-1][0] += ch
            else:
                out.append([ch, f])
        return out

    def getlength(self, text):
        return sum(f.getlength(t) for t, f in self.runs(text))

    def draw(self, d, xy, text, fill):
        """폰트마다 윗선이 달라서, 주아체 기준선(baseline)에 맞춰 그린다."""
        x, y = xy
        base = y + self.main.getmetrics()[0]
        for t, f in self.runs(text):
            d.text((x, base), t, font=f, fill=fill, anchor="ls")
            x += f.getlength(t)


def wrap(text, font, max_w):
    """띄어쓰기 기준으로 줄바꿈, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in text.split(" "):
        cand = f"{cur} {word}".strip()
        if font.getlength(cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if font.getlength(cur + ch) > max_w:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _rrect(x0, y0, x1, y1, r, n=14):
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts += [(cx + r * math.cos(math.radians(a0 + 90 * i / n)), cy + r * math.sin(math.radians(a0 + 90 * i / n)))
                for i in range(n + 1)]
    return pts


def _wobble(pts, amp, step):
    """손그림처럼 외곽을 살짝 흔든다."""
    dense = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        k = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        dense += [(x0 + (x1 - x0) * t / k, y0 + (y1 - y0) * t / k) for t in range(k)]
    cx = sum(p[0] for p in dense) / len(dense)
    cy = sum(p[1] for p in dense) / len(dense)
    out = []
    for i, (x, y) in enumerate(dense):
        w = amp * (0.6 * math.sin(i * 0.21 + 1.7) + 0.4 * math.sin(i * 0.053 + 5.1))
        dx, dy = x - cx, y - cy
        l = math.hypot(dx, dy) or 1
        out.append((x + dx / l * w, y + dy / l * w))
    return out


class Bubble:
    """text를 담은 말풍선. size = 최종 픽셀 크기, tip = 꼬리 끝 좌표(캔버스 기준)."""

    def __init__(self, text, scale, tail="down", name=""):
        """tail="down": 아래로 꼬리 (머리 위 말풍선), "right": 오른쪽으로 꼬리 (패널이 열려 있을 때 옆 말풍선)"""
        s = scale
        self.text, self.tail, self.name = text, tail, name
        self.font = MixFont(int(15 * s))
        self.lines = wrap(text, self.font, int(210 * s))
        self.total = sum(len(l) for l in self.lines)
        self.line_h = int(22 * s)
        text_w = max(self.font.getlength(l) for l in self.lines)
        self.m = int(9 * s)  # 그림자·외곽선 여유
        tag_h, tail_h = int(22 * s), int(18 * s)
        bw = max(int(text_w + 32 * s), int(110 * s))
        bh = int(len(self.lines) * self.line_h + 30 * s)
        self.box = (self.m, self.m + tag_h // 2, self.m + bw, self.m + tag_h // 2 + bh)
        x0, y0, x1, y1 = self.box
        if tail == "right":
            self.size = (bw + tail_h + 2 * self.m, tag_h // 2 + bh + 2 * self.m)
            self.tip = (x1 + tail_h, y0 + int(bh * 0.62))
        else:
            self.size = (bw + 2 * self.m, tag_h // 2 + bh + tail_h + 2 * self.m)
            self.tip = (x1 - int(14 * s), y1 + tail_h)
        self.text_xy = (x0 + int(16 * s), y0 + int(17 * s))
        self.arrow_xy = (x1 - int(16 * s), y1 - int(11 * s))
        self.s = s
        self.bg = self._background(tag_h)

    def _background(self, tag_h):
        s, k = self.s, SS
        W, H = self.size[0] * k, self.size[1] * k
        x0, y0, x1, y1 = (v * k for v in self.box)
        body = _wobble(_rrect(x0, y0, x1, y1, 20 * s * k), 1.3 * s * k, 5 * k)
        if self.tail == "right":
            ty = self.tip[1] * k
            tail = [(x1 - 6 * k, ty - 14 * s * k), (x1 - 6 * k, ty + 4 * s * k), (self.tip[0] * k, ty)]
        else:
            tail = [(x1 - 46 * s * k, y1 - 6 * k), (x1 - 24 * s * k, y1 - 6 * k), (self.tip[0] * k, self.tip[1] * k)]
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        md.polygon(body, fill=255)
        md.polygon(tail, fill=255)
        # 외곽선: 몸통·꼬리 테두리를 굵게 그린 영역 (안쪽은 크림색이 다시 덮으므로 바깥 절반만 보임)
        line = max(3, int(2.4 * s * k))
        outer = mask.copy()
        od = ImageDraw.Draw(outer)
        for poly in (body, tail):
            od.line(poly + poly[:1], fill=255, width=line * 2, joint="curve")

        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shadow = Image.new("L", (W, H), 0)
        shadow.paste(outer, (0, int(3 * s * k)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(5 * s * k)).point(lambda v: v * 60 // 255)
        img.paste((40, 20, 10, 255), (0, 0), shadow)
        img.paste((*BROWN, 255), (0, 0), outer)
        img.paste((*CREAM, 255), (0, 0), mask)

        # 이름표 (캐릭터 이름, 없으면 생략)
        if self.name:
            d = ImageDraw.Draw(img)
            tag_font = _font(int(13 * s * k))
            tx0, ty0 = x0 + 14 * s * k, self.m * k
            tx1, ty1 = tx0 + max(48 * s * k, tag_font.getlength(self.name) + 24 * s * k), ty0 + tag_h * k
            d.rounded_rectangle((tx0, ty0, tx1, ty1), radius=tag_h * k // 2, fill=(*ORANGE, 255),
                                outline=(*BROWN, 255), width=max(2, int(2 * s * k)))
            d.text(((tx0 + tx1) / 2, (ty0 + ty1) / 2 + s * k), self.name, font=tag_font,
                   fill=(255, 255, 255, 255), anchor="mm")
        return img.resize(self.size, Image.LANCZOS)

    def frame(self, shown_chars, t):
        """글자를 shown_chars개까지 쓴 말풍선. 다 쓰면 ▼가 깜빡인다."""
        img = self.bg.copy()
        d = ImageDraw.Draw(img)
        left = shown_chars
        x, y = self.text_xy
        for i, line in enumerate(self.lines):
            if left <= 0:
                break
            self.font.draw(d, (x, y + i * self.line_h), line[:left], (*INK, 255))
            left -= len(line)
        if shown_chars >= self.total and int(t * 2) % 2 == 0:
            ax, ay = self.arrow_xy
            r = 4 * self.s
            d.polygon([(ax - r, ay - r * 0.6), (ax + r, ay - r * 0.6), (ax, ay + r * 0.7)], fill=(*ORANGE, 255))
        return img
