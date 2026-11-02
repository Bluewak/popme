"""기본 캐릭터 (character*.example.toml) — 파일마다 앱이 켜지고, 자기 이미지를 쓰고, 공개 검사를 통과한다."""
import sys
from pathlib import Path

import pytest
from PIL import Image

from popme import character, charmaker as cm, config, pet
from popme import setup_window as sw

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted(p.name for p in config.DEFAULTS_DIR.glob("character*.example.toml"))
sys.path.insert(0, str(ROOT / "tools"))
import check_private as cp  # noqa: E402


def test_three_defaults():
    assert FILES == ["character.example.toml", "character.muk.example.toml", "character.tori.example.toml"]


@pytest.mark.parametrize("file", FILES)
def test_default_character_loads_and_every_line_is_safe(file, monkeypatch):
    monkeypatch.setattr(character, "character_path", lambda: config.DEFAULTS_DIR / file)
    ch = character.Character()
    base = cm.base()
    assert ch.images.startswith("characters/") and ch.cfg["persona"]["intro"]
    assert set(ch.lines) >= set(base["lines"])
    assert cm.clean_lines(ch.lines, base["lines"]) == {c: ch.lines[c][:cm.MAX_PER_CAT] for c in base["lines"]}  # 자리표시가 어긋난 대사 없음


def test_character_folder_wins_over_shared_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULTS_DIR", tmp_path)
    own = tmp_path / "characters" / "tori"
    own.mkdir(parents=True)
    Image.new("RGBA", (10, 10)).save(config.ASSETS_DIR / "pet.png")
    Image.new("RGBA", (20, 20)).save(own / "pet.png")
    assert pet.load_images("characters/tori")["idle"].size == (20, 20)
    assert pet.load_images()["idle"].size == (10, 10)  # images가 없는 캐릭터는 내 이미지 폴더


def test_setup_cards_show_intro_and_image(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULTS_DIR", tmp_path)
    (tmp_path / "characters" / "tori").mkdir(parents=True)
    Image.new("RGBA", (10, 10)).save(tmp_path / "characters" / "tori" / "pet.png")
    (tmp_path / "a.toml").write_text('[persona]\nname = "토리"\nintro = "소개"\nimages = "characters/tori"\n', encoding="utf-8")
    (tmp_path / "b.toml").write_text('[persona]\nname = "나"\n', encoding="utf-8")  # 이미지 없음 → 카드에 그림 없이
    cards = sw.characters()
    assert [(c["file"], c["name"], c["intro"]) for c in cards] == [("a.toml", "토리", "소개"), ("b.toml", "나", "")]
    assert cards[0]["image"].startswith("data:image/png;base64,") and cards[1]["image"] == ""


def test_public_check_allows_only_default_art():
    ok = [("popme/defaults/characters/tori/pet_busy.png", b"x"), ("popme/defaults/character.tori.example.toml", b"")]
    assert cp.check(ok, []) == []
    bad = [("assets/pet.png", b"x"), ("popme/defaults/characters/tori/photo.jpg", b"x"), ("character-2.toml", b""), ("character.toml", b"")]
    assert len(cp.check(bad, [])) == len(bad)
