"""설정 저장·읽기 안전장치: 한글 표 이름, 통째로 바꿔 쓰기, 깨진 설정·BOM, 깨진 캐릭터 파일 (popme/tomledit.py, fileio.py, config.py)."""
import os
import tomllib

import pytest

from popme import character, config, fileio, tomledit
from popme import setup_window as sw


def test_korean_table_name_is_quoted_and_found_again():
    """예전엔 [news.picks.custom-축구]로 써서 다음 실행부터 설정을 못 읽었다 (TOML 맨 키는 ASCII만)."""
    text = '[news]\ninterests = ["custom-축구"]\n'
    once = tomledit.set_value(text, "news.picks.custom-축구", "topics", ["이적"])
    assert '[news.picks."custom-축구"]' in once
    twice = tomledit.set_value(once, "news.picks.custom-축구", "topics", ["부상"])
    cfg = tomllib.loads(twice)
    assert cfg["news"]["picks"]["custom-축구"]["topics"] == ["부상"] and twice.count("[news.picks.") == 1
    assert tomledit.set_value(once, "news.picks.ai-coding", "topics", ["x"]).count("[news.picks.ai-coding]") == 1


def test_apply_interests_with_custom_korean_id_parses():
    text = sw.apply_interests(sw.base_text(), ["custom-축구"], {"custom-축구": {"topics": ["이적"]}})
    assert tomllib.loads(text)["news"]["picks"]["custom-축구"]["topics"] == ["이적"]


def test_split_key():
    assert tomledit.split_key('news.picks."custom-축구"') == ["news", "picks", "custom-축구"]
    assert tomledit.split_key("a . b") == ["a", "b"]
    assert tomledit.split_key("a..b") is None


def test_atomic_write_keeps_old_file_when_replace_fails(tmp_path, monkeypatch):
    (tmp_path / "w").mkdir()
    p = tmp_path / "w" / "f.txt"
    p.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fileio.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        fileio.atomic_write_text(p, "new")
    assert p.read_text(encoding="utf-8") == "old" and [x.name for x in p.parent.iterdir()] == ["f.txt"]


def test_save_config_refuses_broken_text_and_keeps_last_good():
    sw.write(sw.base_text())
    good = config.CONFIG_PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        tomledit.save_config("[news\n")
    assert config.CONFIG_PATH.read_text(encoding="utf-8") == good
    tomledit.save_config(tomledit.set_value(good, "user", "plan", "Max"))
    assert config.last_good().read_text(encoding="utf-8") == good


def test_broken_config_falls_back_to_last_good_and_says_so():
    sw.write(sw.base_text())
    tomledit.save_config(tomledit.set_value(config.CONFIG_PATH.read_text(encoding="utf-8"), "user", "plan", "Max"))
    config.CONFIG_PATH.write_text('[user]\nplan = "Pro\n', encoding="utf-8")  # 따옴표 빠짐
    cfg = config.load()
    assert config.LOAD_ERROR and "읽지 못해" in config.LOAD_ERROR
    assert "news" in cfg  # last-good(예시 설정)으로
    assert config.CONFIG_PATH.read_text(encoding="utf-8") == '[user]\nplan = "Pro\n'  # 사용자 파일은 그대로
    config.CONFIG_PATH.write_text('[user]\nplan = "Pro"\n', encoding="utf-8")
    assert config.load()["user"]["plan"] == "Pro" and config.LOAD_ERROR is None


def test_bom_config_reads():
    config.CONFIG_PATH.write_bytes(b"\xef\xbb\xbf" + '[user]\nplan = "Pro"\n'.encode())
    assert config.load()["user"]["plan"] == "Pro"


def test_load_has_no_side_effects(monkeypatch):
    """읽기만 한다 — 옮기기·비밀번호 숨기기는 catalog.prepare_config (켤 때 한 번)."""
    config.CONFIG_PATH.write_text('[user]\nplan = "Pro"\n', encoding="utf-8")
    before = os.stat(config.CONFIG_PATH).st_mtime_ns
    monkeypatch.setattr(config, "hide_passwords", lambda: pytest.fail("load가 파일을 고침"))
    config.load()
    assert os.stat(config.CONFIG_PATH).st_mtime_ns == before


def test_broken_character_file_uses_default(monkeypatch, tmp_path):
    bad = tmp_path / "me.toml"
    bad.write_text("[persona\n", encoding="utf-8")
    monkeypatch.setattr(character, "character_path", lambda: bad)
    assert character.Character().name


def test_fallback_config_gets_no_saved_password():
    """대신 켠 설정(예전 메일 계정일 수 있다)에 지금 저장된 비밀번호를 붙이지 않는다."""
    from popme import vault
    vault.put("mail", "new-secret")
    config.CONFIG_PATH.write_text('[mail]\nuser = "a@example.com"\npassword = ""\n', encoding="utf-8")
    assert config.load()["mail"]["password"] == "new-secret"
    config.last_good().write_text('[mail]\nuser = "old@example.com"\npassword = ""\n', encoding="utf-8")
    config.CONFIG_PATH.write_text('[mail\n', encoding="utf-8")
    cfg = config.load()
    assert cfg["mail"]["user"] == "old@example.com" and not cfg["mail"].get("password")


def test_last_good_has_no_plaintext_password():
    config.CONFIG_PATH.write_text('[mail]\nuser = "a@example.com"\npassword = "typed-by-hand"\n', encoding="utf-8")
    tomledit.save_config('[mail]\nuser = "a@example.com"\npassword = ""\n')
    assert "typed-by-hand" not in config.last_good().read_text(encoding="utf-8")
