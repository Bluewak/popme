"""설정 창에서 캐릭터 만들기 (popme/charmaker.py) — AI가 무엇을 주든 앱이 켜지는 캐릭터 파일만 나온다."""
import tomllib
from pathlib import Path

import pytest
from PIL import Image

from popme import character, charmaker as cm, config, persona
from popme import setup_window as sw

REPO = Path(__file__).resolve().parent.parent
INFO = {"name": "모모", "call_user": "주인님", "tone": "bright", "detail": "복숭아를 좋아하는 강아지"}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """임시 POPME 폴더 (예시 캐릭터·prepare.py만 있음)."""
    (tmp_path / "assets").mkdir()
    for name in ("character.example.toml", "assets/prepare.py"):
        (tmp_path / name).write_bytes((REPO / name).read_bytes())
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def loaded(monkeypatch, file):
    """앱이 하듯 캐릭터 파일을 읽는다."""
    monkeypatch.setattr(character, "character_path", lambda: config.ROOT / file)
    return character.Character()


def test_ai_lines_kept_and_broken_ones_fall_back(folder):
    default = cm.base()["lines"]
    out = cm.clean_lines({
        "greet_morning": ["{user}, 좋은 아침!", "<b>안녕</b>"],     # {user}는 어디서나, 태그는 지운다
        "warn": ["큰일이에요!"],                                   # 필수 {msg}가 빠짐 → 기본
        "schedule_today": ["{nope} 있어요", "{title:>9} 있어요", "오늘 {title} 있어요!"],  # 없는 자리표시·형식 지정은 버린다
        "cheer": "힘내요",                                         # 목록이 아님 → 기본
        "petted": ["헤헤 {"],                                      # 깨진 중괄호 → 기본
        "없는칸": ["무시"],
    })
    assert out["greet_morning"] == ["{user}, 좋은 아침!", "안녕"]
    assert out["warn"] == default["warn"]
    assert out["schedule_today"] == ["오늘 {title} 있어요!"]
    assert out["cheer"] == default["cheer"] and out["petted"] == default["petted"]
    assert set(out) == set(default)


def test_ui_text_keeps_line_break_and_rejects_fields_that_would_crash(folder):
    ui = cm.base()["ui"]
    out = cm.clean_ui({"briefing_lead": "소식 왔다!", "today_events": "일정 없음", "warn_lead": "{boom} 큰일",
                       "plan_fail": "모르겠어: {msg}", "panel_title": "안 바뀜"})
    assert out["briefing_lead"] == "소식 왔다!<br>"  # 뒤에 내용이 붙는 문구
    assert out["today_events"] == ui["today_events"]  # 필수 {n}이 빠짐
    assert out["warn_lead"] == ui["warn_lead"]  # 시작할 때 format이 죽을 자리표시
    assert out["plan_fail"] == "모르겠어: {msg}"
    assert out["panel_title"] == ui["panel_title"]


def test_saved_file_loads_in_app(folder, monkeypatch):
    nc = cm.clean({**INFO, "lines": {"greet_morning": ["{user}, 좋은 아침이에요!"]}, "ui": {"no_events": "일정 없어요!"}})
    file = cm.save(nc)
    assert file == "character.toml"
    ch = loaded(monkeypatch, file)
    assert (ch.name, ch.user) == ("모모", "주인님")
    assert ch.line("greet_morning") == "주인님, 좋은 아침이에요!"
    assert ch.ui["no_events"] == "일정 없어요!"
    prompt = ch.cfg["persona"]["prompt"]
    assert '"주인님"' in prompt and "복숭아" in prompt and "\n" in prompt  # 오늘의 멘트도 호칭·말투를 따른다
    assert tomllib.loads((folder / file).read_text(encoding="utf-8"))["behavior"] == cm.base()["behavior"]


def test_without_ai_still_makes_character(folder, monkeypatch):
    ch = loaded(monkeypatch, cm.save(cm.clean(INFO)))
    assert ch.name == "모모" and ch.lines == cm.base()["lines"]


def test_never_overwrites_existing_character(folder):
    (folder / "character.toml").write_text("mine", encoding="utf-8")
    (folder / "character-2.toml").write_text("mine", encoding="utf-8")
    assert cm.save(cm.clean(INFO)) == "character-3.toml"
    assert (folder / "character.toml").read_text(encoding="utf-8") == "mine"
    assert [c["name"] for c in sw.characters() if c["file"] == "character-3.toml"] == ["모모"]


def test_name_required_and_text_cleaned(folder):
    with pytest.raises(ValueError):
        cm.clean_info({"name": "  "})
    info = cm.clean_info({"name": 'a"b{c}<d>\\', "tone": "없는말투"})
    assert info["name"] == "abcd" and info["tone"] == "calm"


def test_draft_uses_writer_and_explains_failure(folder, monkeypatch):
    seen = {}

    def ask(cfg, name, inputs, timeout=0):
        seen.update(name=name, inputs=inputs)
        return {"lines": {"greet_morning": ["{user}, 좋은 아침!"]}, "ui": {}}
    monkeypatch.setattr(persona, "ask", ask)
    out = cm.draft({}, INFO)
    assert seen["name"] == "character_writer" and "복숭아" in seen["inputs"]["detail"]
    assert "<" not in seen["inputs"]["ui"]["briefing_lead"]
    assert out["samples"][0] == "주인님, 좋은 아침!"

    def down(*a, **k):
        raise RuntimeError("claude 호출 실패")
    monkeypatch.setattr(persona, "ask", down)
    with pytest.raises(ValueError, match="기본 대사"):
        cm.draft({}, INFO)


def test_writer_persona_matches_schema():
    p = persona.load("character_writer")
    for ex in p["examples"]:
        assert persona.check(p, ex["output"])["lines"]


def test_image_replaces_old_set_and_keeps_backup(folder):
    src = folder / "white.png"
    img = Image.new("RGB", (64, 64), "white")
    img.paste((200, 30, 30), (20, 20, 44, 44))
    img.save(src)
    (folder / "assets" / "pet_busy.png").write_bytes(b"old")
    prepared = cm.prepare_image(src)
    with Image.open(prepared) as made:
        assert max(made.size) < 40  # 흰 여백을 잘랐다 (64 → 몸통 24 + 부드러운 가장자리)
    cm.save(cm.clean(INFO), prepared)
    assets = folder / "assets"
    assert (assets / "pet.png").exists() and not (assets / "pet_busy.png").exists()
    assert list(assets.glob("backup/*/pet_busy.png"))


def test_transparent_image_keeps_its_alpha(folder):
    src = folder / "clear.png"
    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    img.paste((255, 255, 255, 255), (10, 10, 30, 30))  # 흰 몸통이 투명 배경 위에 있어도 지워지면 안 된다
    img.save(src)
    with Image.open(cm.prepare_image(src)) as made:
        assert made.convert("RGBA").getpixel((10, 10)) == (255, 255, 255, 255)


def test_bad_image_says_so(folder):
    bad = folder / "not.png"
    bad.write_text("이미지 아님", encoding="utf-8")
    with pytest.raises(ValueError, match="이미지"):
        cm.prepare_image(bad)


def test_wizard_save_creates_character_and_points_config(folder, monkeypatch, tmp_path):
    (folder / "config.example.toml").write_bytes((REPO / "config.example.toml").read_bytes())
    monkeypatch.setattr(sw, "base_text", lambda: (folder / "config.example.toml").read_text(encoding="utf-8"))
    written = {}
    monkeypatch.setattr(sw, "write", lambda t: written.update(text=t))
    r = sw.Api().save({"provider": "claude", "plan": "Claude Pro", "tools": "Claude Code", "briefing_after": "09:00",
                       "source": "none", "ics": [], "use_windows_location": True, "mail": None,
                       "character": "character.example.toml", "new_character": INFO})
    assert r == {"ok": True}
    assert tomllib.loads(written["text"])["character"]["file"] == "character.toml"
    assert (folder / "character.toml").exists()
