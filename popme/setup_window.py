"""설정 창: 준비물 확인 → 브리핑 AI → 캘린더 → (메일) → 날씨·시각을 차례로 고르면 config.toml에 써 준다.

install.ps1이 설치할 때 띄우고, 나중엔 트레이(또는 캐릭터 우클릭) > "설정 바꾸기"로 다시 연다.
config.toml의 주석·다른 설정은 그대로 두고 고른 값의 줄만 바꾼다 (파일이 없으면 config.example.toml에서 시작).

캘린더 주소(ICS)는 고른 AI에게 맡길 수도 있다: 새 터미널에서 AI CLI를 대화형으로 열고, 캘린더 앱별 안내 프롬프트를 준다.
AI는 아래 명령만으로 주소를 확인·저장한다 (설정 파일을 직접 고치지 않게).
  python -m popme.setup_window check-ics '<주소>'          주소가 열리는지, 일정 몇 개인지
  python -m popme.setup_window add-ics '<이름>' '<주소>'   확인 후 config.toml에 추가 (source = "ics")
"""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from popme import config

log = logging.getLogger(__name__)
UI = config.ROOT / "popme" / "ui" / "setup.html"

AIS = {
    "claude": {"name": "Claude", "plans": ["Claude Pro", "Claude Max"], "tools": "Claude Code"},
    "codex": {"name": "ChatGPT", "plans": ["ChatGPT Plus", "ChatGPT Pro"], "tools": "ChatGPT, Codex"},
    "gemini": {"name": "Gemini", "plans": ["Google AI Pro", "Google AI Ultra", "무료"], "tools": "Gemini"},
}
SOURCES = ("timetree", "ics", "none")
MAIL_HOSTS = {"imap.gmail.com": "Gmail", "imap.naver.com": "네이버 메일", "imap.daum.net": "다음 메일"}
REFRESH_PATH = ("$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + "
                "[Environment]::GetEnvironmentVariable('Path','User')")
# 새 터미널에서 돌릴 설치·로그인 명령 (이미 설치돼 있으면 로그인만)
AI_SETUP = {
    "claude": [
        "if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Claude Code 설치 중...'; irm https://claude.ai/install.ps1 | iex",
        "  $env:Path += \";$env:USERPROFILE\\.local\\bin\"",
        "}",
        "Write-Host ''; Write-Host '브라우저가 열리면 Claude 계정으로 로그인하세요. 대화 화면이 나오면 /exit 입력 후 이 창을 닫으면 돼요.'",
        "claude",
    ],
    "codex": [
        "if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Codex CLI 설치 중...'",
        "  winget install --id OpenAI.Codex -e --accept-package-agreements --accept-source-agreements",
        "  " + REFRESH_PATH,
        "}",
        "Write-Host ''; Write-Host '브라우저가 열리면 ChatGPT 계정으로 로그인하세요. 끝나면 이 창을 닫으면 돼요.'",
        "codex login",
    ],
    "gemini": [
        "if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Node.js 설치 중 (Gemini CLI에 필요)...'",
        "  winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements",
        "  " + REFRESH_PATH,
        "}",
        "if (-not (Get-Command gemini -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Gemini CLI 설치 중...'; npm install -g @google/gemini-cli",
        "  " + REFRESH_PATH,
        "}",
        "Write-Host ''; Write-Host '「Sign in with Google」을 골라 로그인하세요. 대화 화면이 나오면 /quit 입력 후 이 창을 닫으면 돼요.'",
        "gemini",
    ],
}

# 캘린더 앱 → 읽는 방법. ics면 원본 캘린더의 비공개 주소가 필요하다 (안내는 setup.html)
CALENDAR_APPS = {
    "timetree": {"name": "TimeTree", "source": "timetree"},
    "google": {"name": "구글 캘린더", "source": "ics"},
    "icloud": {"name": "아이폰 기본 캘린더 (iCloud)", "source": "ics"},
    "minical": {"name": "미니캘 같은 아이폰 캘린더 앱", "source": "ics"},
    "naver": {"name": "네이버 캘린더", "source": "ics"},
    "outlook": {"name": "Outlook", "source": "ics"},
    "none": {"name": "안 씀", "source": "none"},
}
CAL_HOWTO = {
    "google": "PC에서 calendar.google.com → 왼쪽 캘린더 이름 옆 ⋮ → 설정 및 공유 → 맨 아래 「iCal 형식의 비공개 주소」 복사.",
    "icloud": "아이폰 캘린더 앱 → 아래 「캘린더」 → 쓰는 캘린더 옆 ⓘ → 「공개 캘린더」 켜기 → 「링크 공유」로 webcal:// 주소를 PC로 보내기.",
    "minical": "미니캘은 일정을 직접 갖지 않고 iCloud나 구글 캘린더와 동기화한다. 먼저 미니캘 설정에서 어느 계정과 연결됐는지 확인하고, "
               "그 원본 캘린더(구글 또는 iCloud)의 주소를 그 방법대로 가져온다.",
    "naver": "네이버 캘린더 → 환경설정 → 캘린더 관리 → 내보내기/공유의 ICS(iCal) 주소.",
    "outlook": "outlook.com → 설정 → 캘린더 → 공유 캘린더 → 캘린더 게시 → ICS 링크.",
}
CAL_PROMPT = """너는 POPME(윈도우 데스크톱 브리핑 위젯) 설치를 돕고 있어. 사용자의 캘린더를 POPME에 연결해 줘.
사용자는 컴퓨터를 잘 모를 수 있어. 한국어 존댓말로, 한 번에 한 단계씩, 짧게 안내하고 사용자가 끝냈다고 하면 다음 단계로 가.

- 사용자가 쓰는 캘린더 앱: {app}
- 주소 얻는 법: {howto}
- POPME는 캘린더를 ICS(iCal) 주소로 읽기만 한다. 일정을 바꾸거나 계정에 로그인할 필요는 없다.

할 일
1. 위 방법대로 주소를 얻도록 화면에서 누를 곳을 하나씩 안내한다. 사용자 화면은 직접 볼 수 없으니 무엇이 보이는지 물어 가며 진행한다.
2. 사용자가 주소를 붙여 넣으면 이 폴더에서 아래 명령으로 열리는지 확인한다.
   python -m popme.setup_window check-ics '주소'
3. 확인되면 캘린더 이름(예: 내 캘린더, 회사)을 물어보고 저장한다. 캘린더가 여러 개면 2~3을 반복한다.
   python -m popme.setup_window add-ics '이름' '주소'
4. 다 끝나면 「설정 창으로 돌아가 다음을 누르세요」라고 알려 준다.

지킬 것
- 위 두 명령 말고는 실행하지 말고, 파일을 직접 만들거나 고치지 마.
- 주소는 비밀번호와 같다. 다른 곳에 보내거나 기록하지 말고, 사용자에게도 남에게 보여 주지 말라고 알려 줘.
- 확인이 실패하면 오류 내용을 쉽게 풀어서 설명하고 주소를 다시 받아.
"""

# ---------------------------------------------------------------- config.toml 줄 단위 편집 (주석 보존)

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?")


def _section_span(lines, name):
    """[name] 표의 (머리 줄, 다음 표 머리 줄) 번호. 없으면 None."""
    start = None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        if start is not None:
            return start, i
        if m.group(1) == name and not line.lstrip().startswith("[["):
            start = i
    return (start, len(lines)) if start is not None else None


def _value_end(lines, i, col):
    """lines[i][col:]에서 시작하는 TOML 값이 끝나는 (줄, 칸). 배열·인라인 표는 여러 줄에 걸칠 수 있다."""
    depth, quote = 0, None
    for j in range(i, len(lines)):
        text, k = lines[j], (col if j == i else 0)
        while k < len(text):
            c = text[k]
            if quote:
                if c == "\\" and quote == '"':
                    k += 1
                elif c == quote:
                    quote = None
                    if depth == 0:
                        return j, k + 1
            elif c in "\"'":
                quote = c
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return j, k + 1
            elif c == "#":
                if depth == 0:
                    return j, k
                break  # 배열 안 주석: 다음 줄로
            elif depth == 0 and c.isspace():
                return j, k
            k += 1
        if depth == 0 and not quote:
            return j, len(text)
    return len(lines) - 1, len(lines[-1])


def _literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON 문자열은 TOML 기본 문자열로도 유효
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"  {_literal(x)},\n" for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_literal(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def set_value(text, section, key, value, before=()):
    """[section]의 key 값을 바꾼다 (같으면 그대로). 키가 없으면 표 끝에, 표가 없으면 before 표 앞(없으면 파일 끝)에 만든다."""
    lines = text.split("\n")
    new = _literal(value)
    span = _section_span(lines, section)
    if span is None:
        at = next((s[0] for s in (_section_span(lines, b) for b in before) if s), None)
        block = [f"[{section}]", f"{key} = {new}", ""]
        if at is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += [""] + block
        else:
            lines[at:at] = block
        return "\n".join(lines).rstrip("\n") + "\n"
    s, e = span
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)")
    for i in range(s + 1, e):
        m = key_re.match(lines[i])
        if not m:
            continue
        j, k = _value_end(lines, i, m.end())
        old = (lines[i][m.end():k] if j == i else "\n".join([lines[i][m.end():]] + lines[i + 1:j] + [lines[j][:k]]))
        if old == new:
            return text
        rest = lines[j][k:]
        if rest.lstrip().startswith("#"):  # 오른쪽 주석은 원래 칸에 맞춘다
            col = len(lines[j]) - len(rest.lstrip())
            last = (m.group(1) + new).split("\n")[-1]
            rest = " " * max(1, col - len(last)) + rest.lstrip()
        lines[i:j + 1] = (m.group(1) + new + rest).split("\n")
        return "\n".join(lines)
    last = s  # 표의 마지막 값 줄 뒤 (표 뒤의 빈 줄·다음 표 설명 주석 앞)
    for i in range(s + 1, e):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            last = _value_end(lines, i, 0)[0]
    lines[last + 1:last + 1] = f"{key} = {new}".split("\n")
    return "\n".join(lines)


# ---------------------------------------------------------------- 현재 값 읽기 · 고른 값 쓰기

def base_text():
    path = config.CONFIG_PATH if config.CONFIG_PATH.exists() else config.ROOT / "config.example.toml"
    return path.read_text(encoding="utf-8")


def characters():
    """폴더의 캐릭터 파일들 [{file, name}] ([persona]가 있는 *.toml)."""
    out = []
    for p in sorted(config.ROOT.glob("*.toml")):
        try:
            persona = tomllib.loads(p.read_text(encoding="utf-8")).get("persona")
        except (tomllib.TOMLDecodeError, OSError):
            continue
        if persona is not None:
            out.append({"file": p.name, "name": persona.get("name") or p.stem})
    return out


def mail_supported():
    """메일 알림 기능(popme/mail.py)이 들어 있는 버전에서만 메일 단계를 보인다."""
    return (config.ROOT / "popme" / "mail.py").exists()


def current():
    cfg = tomllib.loads(base_text())
    cal, w, llm_, mail = cfg.get("calendar", {}), cfg.get("weather", {}), cfg.get("llm", {}), cfg.get("mail", {})
    chars = characters()
    char = cfg.get("character", {}).get("file", "character.toml")
    if char not in {c["file"] for c in chars}:
        char = "character.example.toml"
    return {
        "exists": config.CONFIG_PATH.exists(),
        "provider": llm_.get("provider", "claude"),
        "plan": cfg.get("user", {}).get("plan", ""),
        "tools": cfg.get("user", {}).get("tools", ""),
        "briefing_after": cfg.get("schedule", {}).get("briefing_after", "09:00"),
        "source": cal.get("source", "timetree"),
        "ics": [{"name": c.get("name", ""), "url": c.get("url", "")} for c in cal.get("ics", [])],
        "holidays": cal.get("holidays", True),
        "use_windows_location": w.get("use_windows_location", True),
        "place": w.get("name", ""),
        "mail": {"supported": mail_supported(), "enabled": mail.get("enabled", False),
                 "host": mail.get("host", "imap.gmail.com"), "user": mail.get("user", ""),
                 "has_password": bool(mail.get("password")), "quiet_hours": mail.get("quiet_hours", "")},
        "mail_hosts": MAIL_HOSTS,
        "character": char,
        "characters": chars,
        "ais": AIS,
        "calendar_apps": CALENDAR_APPS,
        "python": sys.version.split()[0],
    }


def _check_url(url):
    if not re.match(r"^(https?|webcal)://\S+$", url.strip()):
        raise ValueError(f"주소 모양이 이상해요: {url[:60]} (https:// 또는 webcal://로 시작해야 해요)")
    return url.strip()


def validate(ch):
    """화면에서 받은 값 → 쓸 값. 잘못되면 ValueError(사용자에게 보일 문장)."""
    if ch.get("provider") not in AIS:
        raise ValueError("브리핑 쓸 AI를 골라 주세요.")
    t = (ch.get("briefing_after") or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        raise ValueError("브리핑 시각은 09:00처럼 넣어 주세요.")
    if ch.get("source") not in SOURCES:
        raise ValueError("캘린더를 골라 주세요.")
    rows = []
    for r in ch.get("ics") or []:
        url = (r.get("url") or "").strip()
        if url:
            rows.append({"name": (r.get("name") or "").strip() or f"캘린더 {len(rows) + 1}", "url": _check_url(url)})
    if ch["source"] == "ics" and not rows:
        raise ValueError("캘린더 주소를 하나 이상 넣어 주세요. 나중에 넣으려면 「안 씀」으로 두고 설정을 다시 열면 돼요.")
    mail = ch.get("mail")
    if mail and mail_supported():
        mail = {**mail, "enabled": bool(mail.get("enabled")), "user": (mail.get("user") or "").strip(),
                "password": (mail.get("password") or "").replace(" ", ""),  # 앱 비밀번호는 띄어 써서 보여줌
                "quiet_hours": (mail.get("quiet_hours") or "").strip()}
        if mail.get("host") not in MAIL_HOSTS:
            raise ValueError("메일 서비스를 골라 주세요.")
        if mail["enabled"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail["user"]):
            raise ValueError("메일 주소를 확인해 주세요.")
        if mail["enabled"] and not mail["password"] and not current()["mail"]["has_password"]:
            raise ValueError("메일 앱 비밀번호를 넣어 주세요.")
        hm = r"([01]\d|2[0-3]):[0-5]\d"
        if mail["quiet_hours"] and not re.fullmatch(f"{hm}-{hm}", mail["quiet_hours"]):
            raise ValueError("방해 금지 시간은 23:00-07:00처럼 넣어 주세요.")
    else:
        mail = None
    return {**ch, "briefing_after": t, "ics": rows, "mail": mail,
            "plan": (ch.get("plan") or "").strip(), "tools": (ch.get("tools") or "").strip()}


def apply(text, ch):
    """고른 값(validate 거친 것)을 config 텍스트에 반영. 결과는 TOML로 다시 읽어 검사한다."""
    if ch.get("character"):
        text = set_value(text, "character", "file", ch["character"], before=("user",))
    text = set_value(text, "user", "plan", ch["plan"], before=("schedule",))
    text = set_value(text, "user", "tools", ch["tools"], before=("schedule",))
    text = set_value(text, "schedule", "briefing_after", ch["briefing_after"], before=("chrome",))
    text = set_value(text, "llm", "provider", ch["provider"])
    text = set_value(text, "calendar", "source", ch["source"], before=("timetree", "weather"))
    if ch["source"] == "ics":
        text = set_value(text, "calendar", "ics", ch["ics"])
        text = set_value(text, "calendar", "holidays", bool(ch.get("holidays", True)))
    if "use_windows_location" in ch:
        text = set_value(text, "weather", "use_windows_location", bool(ch["use_windows_location"]))
    if ch.get("mail"):
        m = ch["mail"]
        if m["enabled"] or "[mail]" in text:  # 안 켤 거면 [mail] 표를 새로 만들지 않는다
            for k in ("enabled", "host", "user", "password", "quiet_hours"):
                if k != "password" or m[k]:  # 비밀번호 칸을 비워 두면 저장된 비밀번호를 그대로 둔다
                    text = set_value(text, "mail", k, m[k])
    if ch.get("weather"):  # 고정 지역을 바꿨을 때만 {name, lat, lon}
        for k in ("name", "lat", "lon"):
            text = set_value(text, "weather", k, ch["weather"][k])
    tomllib.loads(text)
    return text


def write(text):
    config.CONFIG_PATH.write_text(text, encoding="utf-8", newline="\n")


def geocode_place(name):
    from popme import location
    res = location._nominatim("search", {"q": name, "countrycodes": "kr", "limit": 1})
    if not res:
        raise ValueError(f"「{name}」 지역을 못 찾았어요. 「서울」「부산 해운대구」처럼 넣어 주세요.")
    return {"name": name, "lat": round(float(res[0]["lat"]), 2), "lon": round(float(res[0]["lon"]), 2)}


def check_mail(host, user, password):
    """IMAP 로그인 → 받은편지함을 읽기 전용으로 열어 보기만 한다. 실패하면 ValueError."""
    import imaplib
    password = (password or "").replace(" ", "")
    if not password:  # 비밀번호 칸을 비웠으면 저장된 것으로 확인
        password = tomllib.loads(base_text()).get("mail", {}).get("password", "")
    try:
        M = imaplib.IMAP4_SSL(host, 993, timeout=20)
        try:
            M.login(user.strip(), password)
            M.select("INBOX", readonly=True)
        finally:
            try:
                M.logout()
            except Exception:
                pass
    except imaplib.IMAP4.error as e:
        hint = (" Gmail은 일반 비밀번호가 아니라 「앱 비밀번호」 16자리를 넣어야 해요." if "gmail" in host else
                " 메일 설정에서 IMAP 사용을 켰는지도 확인해 주세요.")
        raise ValueError(f"로그인하지 못했어요.{hint} ({str(e)[:100]})")
    except OSError as e:
        raise ValueError(f"메일 서버에 연결하지 못했어요: {e}")
    return True


def check_ics(url):
    """주소를 받아 일정 수·캘린더 이름을 돌려준다. 실패하면 ValueError."""
    import icalendar
    from popme.collectors import ics
    url = _check_url(url)
    try:
        cal = icalendar.Calendar.from_ical(ics.fetch(url, timeout=20))
    except Exception as e:
        raise ValueError(f"주소를 열지 못했어요 ({type(e).__name__}: {str(e)[:120]}). 주소 전체를 다시 복사해 주세요.")
    return {"events": len(cal.walk("VEVENT")), "name": str(cal.get("X-WR-CALNAME", "") or "")}


def add_ics(name, url):
    """AI가 쓰는 명령: 확인된 주소를 config.toml에 추가하고 source를 ics로."""
    info = check_ics(url)
    cfg = tomllib.loads(base_text())
    rows = [r for r in cfg.get("calendar", {}).get("ics", []) if r.get("url") and r["url"] != url.strip()]
    rows.append({"name": name.strip() or info["name"] or "내 캘린더", "url": url.strip()})
    text = set_value(base_text(), "calendar", "source", "ics", before=("timetree", "weather"))
    text = set_value(text, "calendar", "ics", [{"name": r["name"], "url": r["url"]} for r in rows])
    tomllib.loads(text)
    write(text)
    return info


# ---------------------------------------------------------------- AI CLI 설치·로그인·캘린더 연결 (새 터미널)

def ai_status(provider):
    from popme import llm
    cfg = {"llm": {}}
    finder = {"claude": llm.find_claude, "codex": llm.find_codex, "gemini": llm.find_gemini}[provider]
    try:
        exe = finder(cfg)
    except RuntimeError:
        return {"installed": False, "logged_in": False}
    home = Path.home()
    if provider == "codex":
        try:
            r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            logged = "logged in" in (r.stdout + r.stderr).lower()
        except Exception:
            logged = None
    elif provider == "claude":
        logged = (home / ".claude" / ".credentials.json").exists() or None  # 다른 방식 로그인이면 알 수 없음
    else:
        logged = (home / ".gemini" / "oauth_creds.json").exists() or None
    return {"installed": True, "logged_in": logged, "path": exe}


def _console(lines, title):
    """새 PowerShell 창에서 명령 실행 (창은 열어 둔다)."""
    script = "\n".join([f"$Host.UI.RawUI.WindowTitle = '{title}'",
                        "[Console]::OutputEncoding = [Text.Encoding]::UTF8", *lines])
    fd, path = tempfile.mkstemp(prefix="popme-", suffix=".ps1", dir=config.DATA_DIR)
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:  # PowerShell 5.1은 BOM이 있어야 UTF-8로 읽음
        f.write(script)
    subprocess.Popen(["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", path], cwd=config.ROOT,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)


def open_ai_setup(provider):
    _console(AI_SETUP[provider], f"POPME - {AIS[provider]['name']} 설치·로그인")


def open_calendar_helper(provider, app):
    from popme import llm
    exe = {"claude": llm.find_claude, "codex": llm.find_codex, "gemini": llm.find_gemini}[provider]({"llm": {}})
    prompt = CAL_PROMPT.format(app=CALENDAR_APPS[app]["name"], howto=CAL_HOWTO.get(app, ""))
    fd, ppath = tempfile.mkstemp(prefix="popme-cal-", suffix=".txt", dir=config.DATA_DIR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(prompt)
    # PowerShell 5.1은 인자 안의 큰따옴표를 망가뜨린다 → 프롬프트엔 큰따옴표를 쓰지 않는다 (「」 사용)
    flag = "-i " if provider == "gemini" else ""
    _console([f"$p = Get-Content -Raw -Encoding UTF8 '{ppath}'", f"Remove-Item '{ppath}'",
              f"& '{exe}' {flag}$p"], f"POPME - 캘린더 연결 ({AIS[provider]['name']})")


def chrome_found():
    from popme import chrome
    try:
        chrome.chrome_exe()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------- 창

class Api:
    def __init__(self):
        self._window = None
        self._saved = False

    def get(self):
        return {**current(), "chrome": chrome_found()}

    def ai_status(self, provider):
        return ai_status(provider)

    def ai_setup(self, provider):
        open_ai_setup(provider)

    def calendar_helper(self, provider, app):
        try:
            open_calendar_helper(provider, app)
            return {"ok": True}
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def saved_ics(self):  # AI가 add-ics로 넣은 주소를 화면에 반영할 때
        return current()["ics"]

    def check_ics(self, url):
        try:
            return {"ok": True, **check_ics(url)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def check_mail(self, host, user, password):
        try:
            return {"ok": check_mail(host, user, password)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def save(self, ch):
        try:
            ch = validate(ch)
            place = (ch.get("place") or "").strip()
            if place and place != current()["place"]:
                ch["weather"] = geocode_place(place)
            write(apply(base_text(), ch))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("설정 저장 실패")
            return {"ok": False, "error": f"저장하지 못했어요: {e}"}
        self._saved = True
        return {"ok": True}

    def close(self):
        self._window.destroy()


def main():
    import webview
    api = Api()
    api._window = webview.create_window("POPME 설정", url=str(UI), js_api=api, width=520, height=760, min_size=(420, 560))
    webview.start()
    return 0 if api._saved else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    try:
        if args[:1] == ["check-ics"] and len(args) == 2:
            info = check_ics(args[1])
            print(f"확인됨: 일정 {info['events']}개" + (f", 캘린더 이름 「{info['name']}」" if info["name"] else ""))
        elif args[:1] == ["add-ics"] and len(args) == 3:
            info = add_ics(args[1], args[2])
            print(f"저장됨: 「{args[1]}」 (일정 {info['events']}개). 설정 창으로 돌아가세요.")
        elif not args:
            sys.exit(main())
        else:
            print(__doc__)
            sys.exit(2)
    except ValueError as e:
        print("실패:", e)
        sys.exit(1)
