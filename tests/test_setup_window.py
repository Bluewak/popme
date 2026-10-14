"""설정 창이 config.toml을 고치는 부분 (popme/setup_window.py) — 주석·다른 설정은 그대로, 고른 값만 바뀐다."""
import tomllib
from pathlib import Path

import pytest

from popme import setup_window as sw

EXAMPLE = (Path(__file__).resolve().parent.parent / "config.example.toml").read_text(encoding="utf-8")


def choices(**kw):
    base = {"provider": "claude", "plan": "Claude Pro", "tools": "Claude Code", "briefing_after": "09:00",
            "source": "timetree", "ics": [], "holidays": True, "use_windows_location": True,
            "character": "character.toml", "mail": None}
    return sw.validate({**base, **kw})


def test_same_choices_change_nothing():
    assert sw.apply(EXAMPLE, choices()) == EXAMPLE


def test_gemini_and_ics():
    rows = [{"name": "내 캘린더", "url": "https://calendar.google.com/calendar/ical/a%40b/private-x/basic.ics"},
            {"name": "", "url": "webcal://p01-caldav.icloud.com/published/2/abc#frag"}]
    out = sw.apply(EXAMPLE, choices(provider="gemini", plan="Google AI Pro", tools="Gemini", source="ics", ics=rows,
                                    holidays=False, briefing_after="08:30"))
    cfg = tomllib.loads(out)
    assert cfg["llm"]["provider"] == "gemini"
    assert cfg["user"] == {"plan": "Google AI Pro", "tools": "Gemini"}
    assert cfg["schedule"]["briefing_after"] == "08:30"
    assert cfg["calendar"]["source"] == "ics" and cfg["calendar"]["holidays"] is False
    assert cfg["calendar"]["ics"][1] == {"name": "캘린더 2", "url": rows[1]["url"]}  # 이름 없으면 번호, URL 속 #은 주석이 아님
    # 다른 설정·주석은 그대로
    assert cfg["x"]["accounts"] == tomllib.loads(EXAMPLE)["x"]["accounts"]
    assert "# 교차 확인·새 오픈소스 발굴용" in out
    assert 'provider = "gemini"   # 브리핑 쓸 AI' in out  # 오른쪽 주석 칸 유지
    # 다시 열어 같은 값으로 저장하면 그대로
    assert sw.apply(out, choices(provider="gemini", plan="Google AI Pro", tools="Gemini", source="ics", ics=rows,
                                 holidays=False, briefing_after="08:30")) == out


def test_replaces_existing_ics_array():
    text = '[calendar]\nsource = "ics"\nics = [\n  { name = "옛것", url = "https://a/x.ics" },  # 주석 ]\n]\nholidays = true\n'
    out = sw.apply(text, choices(source="ics", ics=[{"name": "새것", "url": "https://b/y.ics"}]))
    cfg = tomllib.loads(out)
    assert cfg["calendar"]["ics"] == [{"name": "새것", "url": "https://b/y.ics"}]
    assert "옛것" not in out


def test_missing_sections_are_created():
    text = '[user]\nplan = "x"\n\n[timetree]\nurl = "https://timetreeapp.com/calendars"\n\n[weather]\nname = "서울"\nlat = 37.57\nlon = 126.98\n'
    out = sw.apply(text, choices(provider="codex", weather={"name": "부산", "lat": 35.18, "lon": 129.08}))
    cfg = tomllib.loads(out)
    assert cfg["calendar"]["source"] == "timetree"
    assert out.index("[calendar]") < out.index("[timetree]")  # 새 표는 관련 표 앞에
    assert cfg["llm"]["provider"] == "codex"
    assert cfg["weather"] == {"name": "부산", "lat": 35.18, "lon": 129.08, "use_windows_location": True}


def test_mail_password_kept_when_blank(monkeypatch):
    monkeypatch.setattr(sw, "mail_supported", lambda: True)
    monkeypatch.setattr(sw, "current", lambda: {"mail": {"has_password": True}})
    text = sw.set_value(EXAMPLE, "mail", "password", "oldsecret")
    mail = {"enabled": True, "host": "imap.gmail.com", "user": "me@example.com", "password": "", "quiet_hours": "23:00-07:00"}
    cfg = tomllib.loads(sw.apply(text, choices(mail=mail)))
    assert {k: cfg["mail"][k] for k in mail} == {**mail, "password": "oldsecret"}
    assert cfg["mail"]["vip"] == []  # 설정 창에 없는 메일 설정은 그대로
    cfg = tomllib.loads(sw.apply(text, choices(mail={**mail, "password": "abcd efgh ijkl mnop"})))
    assert cfg["mail"]["password"] == "abcdefghijklmnop"  # 앱 비밀번호 띄어쓰기는 뺀다


def test_mail_off_does_not_add_section(monkeypatch):
    monkeypatch.setattr(sw, "mail_supported", lambda: True)
    mail = {"enabled": False, "host": "imap.gmail.com", "user": "", "password": "", "quiet_hours": ""}
    text = EXAMPLE[:EXAMPLE.index("[mail]")]
    assert "[mail]" not in sw.apply(text, choices(mail=mail))


def test_default_character_is_not_pinned(tmp_path, monkeypatch):
    """기본 캐릭터(예시)를 골라도 config에는 character.toml — 나중에 만들면 바로 쓰이게."""
    monkeypatch.setattr(sw.config, "ROOT", tmp_path)
    assert choices(character="character.example.toml")["character"] == "character.toml"
    (tmp_path / "character.toml").write_text('[persona]\nname = "나만의"\n', encoding="utf-8")
    assert choices(character="character.example.toml")["character"] == "character.example.toml"  # 있으면 고른 대로
    assert choices(character="other.toml")["character"] == "other.toml"


@pytest.mark.parametrize("kw, msg", [
    ({"provider": "grok"}, "AI"),
    ({"briefing_after": "9시"}, "시각"),
    ({"source": "ics", "ics": []}, "주소"),
    ({"source": "ics", "ics": [{"name": "a", "url": "calendar.google.com/x"}]}, "주소 모양"),
])
def test_validate_rejects(kw, msg):
    with pytest.raises(ValueError, match=msg):
        choices(**kw)
