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


def test_mail_custom_host(monkeypatch):
    """직접 입력한 도메인은 서버 이름만 모양을 본다 (목록에 없어도 됨)."""
    monkeypatch.setattr(sw, "mail_supported", lambda: True)
    mail = {"enabled": True, "host": " IMAP.Example.co.kr ", "user": "me@example.com", "password": "pw", "quiet_hours": ""}
    assert choices(mail=mail)["mail"]["host"] == "imap.example.co.kr"
    with pytest.raises(ValueError, match="메일 서버"):
        choices(mail={**mail, "host": "imap example"})
    assert choices(mail={**mail, "enabled": False, "host": ""})["mail"]["host"] == ""  # 끈 상태면 비어도 됨


@pytest.mark.parametrize("mx, host, note", [
    (["aspmx.l.google.com."], "imap.gmail.com", False),          # 회사 도메인 + Google Workspace
    (["mx1.worksmobile.com"], "imap.worksmobile.com", False),    # 네이버웍스
    (["corp-com.mail.protection.outlook.com"], "outlook.office365.com", True),
    (["mx.example.co.kr"], "imap.example.co.kr", False),
    ([], "imap.example.co.kr", False),
])
def test_host_from_mx(mx, host, note):
    g = sw.host_from_mx("example.co.kr", mx)
    assert g["host"] == host and bool(g["note"]) is note


def test_guess_mail_host_known_domain_needs_no_lookup(monkeypatch):
    monkeypatch.setattr(sw.subprocess, "run", lambda *a, **k: pytest.fail("nslookup 안 해야 함"))
    assert sw.guess_mail_host("Hanmail.net")["host"] == "imap.daum.net"
    assert sw.guess_mail_host("not a domain")["host"] == ""


@pytest.mark.parametrize("err, msg", [
    ("claude 호출 실패: Invalid API key · Please run /login", "로그인"),
    ("claude 호출 실패: 5-hour limit reached", "한도"),
    ("claude 호출 실패: 무언가 이상함", "불러 보지 못했어요"),
])
def test_ai_test_errors_are_friendly(monkeypatch, err, msg):
    from popme import llm

    def boom(*a, **k):
        raise RuntimeError(err)
    monkeypatch.setattr(llm, "ask", boom)
    with pytest.raises(ValueError, match=msg):
        sw.ai_test("claude")


def test_caldav_saved(monkeypatch):
    monkeypatch.setattr(sw, "current", lambda: {"caldav": {"has_password": False}})
    cals = [{"name": "집", "url": "https://p1-caldav.icloud.com/1/calendars/home/"}]
    dav = {"user": " me@example.com ", "password": "abcd-efgh-ijkl-mnop", "calendars": cals}
    out = sw.apply(EXAMPLE, choices(source="caldav", caldav=dav, holidays=False))
    cfg = tomllib.loads(out)
    assert cfg["calendar"]["source"] == "caldav" and cfg["calendar"]["holidays"] is False
    assert cfg["caldav"] == {"server": "https://caldav.icloud.com", "user": "me@example.com",
                             "password": "abcd-efgh-ijkl-mnop", "calendars": cals}
    # 앱 암호를 비워 두면 저장된 것을 그대로 둔다
    monkeypatch.setattr(sw, "current", lambda: {"caldav": {"has_password": True}})
    again = tomllib.loads(sw.apply(out, choices(source="caldav", caldav={**dav, "password": ""})))
    assert again["caldav"]["password"] == "abcd-efgh-ijkl-mnop"


@pytest.mark.parametrize("dav, msg", [
    ({"user": "나", "password": "x", "calendars": [{"name": "a", "url": "https://a/"}]}, "이메일"),
    ({"user": "me@example.com", "password": "", "calendars": [{"name": "a", "url": "https://a/"}]}, "앱 암호"),
    ({"user": "me@example.com", "password": "x", "calendars": []}, "연결 확인"),
])
def test_caldav_rejects(monkeypatch, dav, msg):
    monkeypatch.setattr(sw, "current", lambda: {"caldav": {"has_password": False}})
    with pytest.raises(ValueError, match=msg):
        choices(source="caldav", caldav=dav)


def test_take_app_password_only_returns_app_password():
    """클립보드에서 앱 암호 모양만 꺼내고, 꺼낸 뒤엔 클립보드를 비운다. 다른 내용은 돌려주지 않는다."""
    board = {"text": " abcd-efgh-ijkl-mnop\n"}

    def read(clear=False):
        if clear:
            board["text"] = ""
        return board["text"]
    assert sw.take_app_password(read) == "abcd-efgh-ijkl-mnop"
    assert board["text"] == ""
    for other in ["내 비밀 메모", "abcd-efgh-ijkl-mnop 그리고 더", "ABCD-EFGH-IJKL-MNOP1", "abcdefghijklmnop"]:
        board["text"] = other
        assert sw.take_app_password(read) == ""
        assert board["text"] == other  # 앱 암호가 아니면 건드리지 않는다
