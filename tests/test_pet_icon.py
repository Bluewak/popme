"""캐릭터 이미지 → 바로가기·트레이 아이콘 (popme/pet.py). 이미지를 바꾸면 다음에 켤 때 아이콘도 바뀐다."""
import os

import pytest
from PIL import Image

from popme import pet


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(pet, "ASSETS", tmp_path)
    monkeypatch.setattr(pet, "_refresh_shell_icons", lambda: None)
    return tmp_path


def test_default_icon_without_image(assets):
    imgs = pet.load_images()
    assert set(imgs) == {"idle"}  # 이미지가 없으면 기본 방울
    assert pet.write_icon(imgs["idle"]) is True
    ico = Image.open(assets / "pet.ico")
    assert ico.info["sizes"] == set(pet.ICON_SIZES)


def test_icon_follows_image_even_with_old_mtime(assets):
    pet.write_icon(pet.load_images()["idle"])
    before = (assets / "pet.ico").read_bytes()
    # 다른 데서 복사해 온 이미지는 수정 시각이 옛날일 수 있다 → 시각이 아니라 내용으로 판단해야 한다
    Image.new("RGBA", (120, 80), (40, 120, 220, 255)).save(assets / "pet.png")
    os.utime(assets / "pet.png", (946684800, 946684800))  # 2000-01-01
    imgs = pet.load_images()
    assert imgs["idle"].size == (120, 80)
    assert pet.write_icon(imgs["idle"]) is True
    assert (assets / "pet.ico").read_bytes() != before


def test_same_image_does_not_rewrite(assets):
    Image.new("RGBA", (64, 64), (200, 50, 50, 255)).save(assets / "pet.png")
    pet.write_icon(pet.load_images()["idle"])
    stamp = (assets / "pet.ico").stat().st_mtime_ns
    assert pet.write_icon(pet.load_images()["idle"]) is False
    assert (assets / "pet.ico").stat().st_mtime_ns == stamp
