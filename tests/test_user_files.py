"""사용자 파일 (popme/config.py): 앱 폴더 밖 보관, 예전 폴더에서 옮기기, 비밀번호는 자격 증명 관리자로."""
import tomllib

import pytest
from PIL import Image

from popme import character, config, setup_window as sw, vault

OLD_CONFIG = '''[character]
file = "mine.toml"

[mail]
enabled = true
user = "me@example.com"
password = "old-secret"   # 앱 비밀번호

[[mail.vip]]
name = "password"

[caldav]
user = "me@example.com"
password = 'dav-secret'
'''


@pytest.fixture
def old_app(tmp_path):
    """예전 버전이 깔린 폴더: 개인 파일과 배포 파일이 섞여 있다."""
    old = tmp_path / "old"
    (old / "assets" / "backup" / "20261001").mkdir(parents=True)
    (old / "assets" / "fonts").mkdir()
    (old / "assets" / "characters" / "tori").mkdir(parents=True)
    (old / "config.toml").write_text(OLD_CONFIG, encoding="utf-8")
    (old / "config.toml.bak-20261001").write_text(OLD_CONFIG, encoding="utf-8")
    (old / "mine.toml").write_text('[persona]\nname = "나"\n', encoding="utf-8")
    (old / "character.toml").write_text('[persona]\nname = "둘째"\n', encoding="utf-8")
    (old / "character.example.toml").write_text('[persona]\nname = "기본"\n', encoding="utf-8")
    (old / "other.toml").write_text('[tool]\nx = 1\n', encoding="utf-8")
    for rel in ("assets/pet.png", "assets/pet_busy.png", "assets/backup/20261001/pet.png",
                "assets/pet.ico", "assets/prepare.py", "assets/README.md",
                "assets/fonts/Jua-Regular.ttf", "assets/characters/tori/pet.png"):
        (old / rel).write_bytes(b"x")
    return old


def test_adopt_moves_personal_files_only(old_app):
    assert config.adopt_old_files(old_app) == 7
    data = config.DATA_DIR
    for name in ("config.toml", "config.toml.bak-20261001", "mine.toml", "character.toml",
                 "assets/pet.png", "assets/pet_busy.png", "assets/backup/20261001/pet.png"):
        assert (data / name).exists() and not (old_app / name).exists(), name
    for name in ("character.example.toml", "other.toml", "assets/pet.ico", "assets/prepare.py", "assets/README.md",
                 "assets/fonts/Jua-Regular.ttf", "assets/characters/tori/pet.png"):
        assert (old_app / name).exists() and not (data / name).exists(), name


def test_adopt_hides_every_password_and_keeps_the_rest(old_app):
    config.adopt_old_files(old_app)
    for name in ("config.toml", "config.toml.bak-20261001"):
        text = (config.DATA_DIR / name).read_text(encoding="utf-8")
        assert "secret" not in text
        assert 'password = ""   # 앱 비밀번호' in text or name != "config.toml"
        cfg = tomllib.loads(text)
        assert cfg["mail"]["user"] == "me@example.com" and cfg["mail"]["vip"][0]["name"] == "password"
    assert vault.saved == {"mail": "old-secret", "caldav": "dav-secret"}
    cfg = config.load()
    assert (cfg["mail"]["password"], cfg["caldav"]["password"]) == ("old-secret", "dav-secret")


def test_adopt_never_overwrites_but_clears_password_left_behind(old_app):
    config.CONFIG_PATH.write_text('[mail]\nuser = "new@example.com"\npassword = "new-secret"\n', encoding="utf-8")
    config.adopt_old_files(old_app)
    assert tomllib.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))["mail"]["user"] == "new@example.com"
    assert vault.saved["mail"] == "new-secret"  # 옛 폴더의 값이 지금 값을 덮지 않는다
    assert "secret" not in (old_app / "config.toml").read_text(encoding="utf-8")


def test_adopt_is_safe_to_repeat(old_app):
    config.adopt_old_files(old_app)
    assert config.adopt_old_files(old_app) == 0
    assert config.adopt_old_files(old_app.parent / "없는 폴더") == 0


def test_password_stays_when_vault_refuses(monkeypatch):
    """자격 증명 관리자를 못 쓰는 PC에서 메일이 끊기면 안 된다."""
    monkeypatch.setattr(vault, "put", lambda name, value: False)
    config.CONFIG_PATH.write_text('[mail]\npassword = "keep"\n', encoding="utf-8")
    assert config.load()["mail"]["password"] == "keep"
    assert 'password = "keep"' in config.CONFIG_PATH.read_text(encoding="utf-8")


def test_saving_setup_stores_password_outside_the_file():
    text = sw.apply(sw.base_text(), {**sw.validate({"provider": "claude", "plan": "", "tools": "", "briefing_after": "09:00",
                                                    "source": "none", "ics": [], "use_windows_location": True, "mail": None,
                                                    "character": "character.example.toml"}),
                                     "mail": {"enabled": True, "host": "imap.gmail.com", "user": "me@example.com",
                                              "password": "fromwizard", "quiet_hours": ""}})
    sw.write(text)
    assert "fromwizard" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    assert vault.saved["mail"] == "fromwizard"
    assert sw.current()["mail"]["has_password"]
    assert sw.saved_mail_password("imap.gmail.com", "me@example.com") == "fromwizard"
    assert sw.saved_mail_password("imap.gmail.com", "other@example.com") == ""  # 다른 계정에 쓰지 않는다


def test_my_character_wins_over_default_and_custom_file_found(monkeypatch):
    (config.DATA_DIR / "character.toml").write_text('[persona]\nname = "내것"\n', encoding="utf-8")
    assert character.character_path() == config.DATA_DIR / "character.toml"
    (config.DATA_DIR / "character.toml").unlink()
    assert character.character_path() == config.DEFAULTS_DIR / "character.example.toml"
    monkeypatch.setattr(config, "load", lambda: {"character": {"file": "character.tori.example.toml"}})
    assert character.character_path() == config.DEFAULTS_DIR / "character.tori.example.toml"


def test_image_folders():
    assert config.image_dir() == config.ASSETS_DIR
    assert config.image_dir("characters/tori") == config.DEFAULTS_DIR / "characters" / "tori"
    assert config.image_dir("assets/mine") == config.DATA_DIR / "assets" / "mine"  # 예전 캐릭터 파일의 경로


def test_setup_shows_my_image_as_data_uri():
    Image.new("RGBA", (4, 4)).save(config.ASSETS_DIR / "pet.png")
    (config.DATA_DIR / "mine.toml").write_text('[persona]\nname = "나"\n', encoding="utf-8")
    cards = {c["file"]: c for c in sw.characters()}
    assert cards["mine.toml"]["image"].startswith("data:image/png;base64,")
    assert sw.current()["pet_image"].startswith("data:image/png;base64,")
    assert set(cards) >= {"character.example.toml", "character.muk.example.toml", "character.tori.example.toml"}
