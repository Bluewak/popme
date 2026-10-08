"""캐릭터 원본(흰 배경) → 배경 투명 + 여백 자르기 + 320px.

사용: python assets/prepare.py <원본.png> <pet|pet_busy|pet_happy|pet_sleepy|pet_alert>
바깥 흰 배경만 모서리에서부터 채워 지우므로, 외곽선 안쪽의 밝은 색(몸통)은 그대로 남는다.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent


def prepare(src, name, size=320):
    img = Image.open(src).convert("RGBA")
    w, h = img.size
    mask_src = img.convert("RGB")
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(mask_src, corner, (255, 0, 255), thresh=40)
    bg = Image.eval(mask_src.convert("RGB"), lambda v: v).load()
    alpha = Image.new("L", (w, h), 255)
    a = alpha.load()
    for y in range(h):
        for x in range(w):
            if bg[x, y] == (255, 0, 255):
                a[x, y] = 0
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.8))  # 외곽 계단 현상 완화
    img.putalpha(alpha)
    img = img.crop(img.getbbox())
    img.thumbnail((size, size), Image.LANCZOS)
    out = HERE / f"{name}.png"
    img.save(out)
    print(out, img.size)


if __name__ == "__main__":
    prepare(sys.argv[1], sys.argv[2])
