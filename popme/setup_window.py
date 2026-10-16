"""설정 창: 준비물 → 브리핑 AI → 캘린더 → (메일) → 로그인 → 날씨·시각을 차례로 고르면 config.toml에 써 준다.

install.ps1이 설치할 때 띄우고, 나중엔 트레이(또는 캐릭터 우클릭) > "설정 바꾸기"로 다시 연다.
config.toml의 주석·다른 설정은 그대로 두고 고른 값의 줄만 바꾼다 (파일이 없으면 config.example.toml에서 시작).

연결 방법은 HELP(단계별 안내 + 자주 막히는 질문)에 데이터로 둔다. 화면 안내도, 「막혔어요?」의 AI 답도 이것만 쓴다
(AI는 이 안내 범위 밖이면 답하지 않는다 — ask_help).
  python -m popme.setup_window check-ics '<주소>'          주소가 열리는지, 일정 몇 개인지
  python -m popme.setup_window check-caldav '<Apple 계정 이메일>'   앱 암호를 물어 iCloud 캘린더·일정 수만 보여 줌 (저장 안 함)
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
from popme.collectors import caldav

log = logging.getLogger(__name__)
UI = config.ROOT / "popme" / "ui" / "setup.html"

AIS = {
    "claude": {"name": "Claude", "plans": ["Claude Pro", "Claude Max"], "tools": "Claude Code"},
    "codex": {"name": "ChatGPT", "plans": ["ChatGPT Plus", "ChatGPT Pro"], "tools": "ChatGPT, Codex"},
    "gemini": {"name": "Gemini", "plans": ["Google AI Pro", "Google AI Ultra", "무료"], "tools": "Gemini"},
}
SOURCES = ("timetree", "ics", "caldav", "none")
# 메일 주소 도메인 → IMAP 서버. 목록에 없는 도메인은 guess_mail_host()가 MX 레코드로 추측한다
MAIL_DOMAINS = {"gmail.com": "imap.gmail.com", "naver.com": "imap.naver.com", "daum.net": "imap.daum.net",
                "hanmail.net": "imap.daum.net", "kakao.com": "imap.kakao.com"}
# MX 서버 이름 끝 → IMAP 서버 (회사·학교 도메인이 구글·네이버웍스 메일을 쓰는 경우)
MX_HOSTS = {"google.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
            "worksmobile.com": "imap.worksmobile.com", "naver.com": "imap.naver.com", "daum.net": "imap.daum.net",
            "kakao.com": "imap.kakao.com"}
MS_NOTE = "Microsoft(Outlook·365) 메일은 비밀번호로 IMAP 로그인하는 것을 막아 두어서 연결이 안 될 수 있어요."
REFRESH_PATH = ("$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + "
                "[Environment]::GetEnvironmentVariable('Path','User')")
# 새 터미널에서 돌릴 설치·로그인 명령 (이미 설치돼 있으면 로그인만)
AI_SETUP = {
    "claude": [
        "if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Claude Code 설치 중...'; irm https://claude.ai/install.ps1 | iex",
        "  $env:Path += \";$env:USERPROFILE\\.local\\bin\"",
        "}",
        "Write-Host ''; Write-Host '브라우저가 열리면 Claude 계정으로 로그인하세요. 끝나면 설정 창이 알아채요. 이 창은 닫아도 돼요.'",
        "claude auth login --claudeai",
        "if ($LASTEXITCODE -ne 0) { claude }  # auth 명령이 없는 옛 버전이면 대화 화면에서 로그인",
    ],
    "codex": [
        "if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Codex CLI 설치 중...'",
        "  winget install --id OpenAI.Codex -e --source winget --accept-package-agreements --accept-source-agreements",
        "  " + REFRESH_PATH,
        "}",
        "Write-Host ''; Write-Host '브라우저가 열리면 ChatGPT 계정으로 로그인하세요. 끝나면 설정 창이 알아채요. 이 창은 닫아도 돼요.'",
        "codex login",
    ],
    "gemini": [
        "if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Node.js 설치 중 (Gemini CLI에 필요)...'",
        "  winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements",
        "  " + REFRESH_PATH,
        "}",
        "if (-not (Get-Command gemini -ErrorAction SilentlyContinue)) {",
        "  Write-Host 'Gemini CLI 설치 중...'; npm install -g @google/gemini-cli",
        "  " + REFRESH_PATH,
        "}",
        "Write-Host ''; Write-Host '「Sign in with Google」을 골라 로그인하세요. 대화 화면이 나오면 설정 창이 알아채요. /quit 입력 후 이 창을 닫으면 돼요.'",
        "gemini",
    ],
}

# 캘린더 앱 → 읽는 방법. ics면 원본 캘린더의 비공개 주소, caldav면 계정 + 앱 암호 (안내는 setup.html)
# 미니캘은 "어느 캘린더와 연결됐나"를 물어서 iCloud면 caldav, 구글이면 ics로 간다 (source "ask")
CALENDAR_APPS = {
    "timetree": {"name": "TimeTree", "source": "timetree"},
    "google": {"name": "구글 캘린더", "source": "ics"},
    "icloud": {"name": "아이폰 기본 캘린더 (iCloud)", "source": "caldav"},
    "minical": {"name": "미니캘 같은 아이폰 캘린더 앱", "source": "ask"},
    "naver": {"name": "네이버 캘린더", "source": "ics"},
    "outlook": {"name": "Outlook", "source": "ics"},
    "none": {"name": "안 씀", "source": "none"},
}
# 연결 안내: steps = 화면에 보일 순서(HTML 조각, <a data-url>은 평소 브라우저로 열림), faq = 자주 막히는 질문과 정해진 답,
# open = 「… 열기」 버튼이 여는 주소. 화면과 「막혔어요?」 AI가 같은 내용을 쓴다
HELP = {
    "google": {
        "open": ("구글 캘린더 설정 열기", "https://calendar.google.com/calendar/r/settings"),
        "steps": ["아래 <b>「구글 캘린더 설정 열기」</b> → 평소 브라우저에서 구글에 로그인돼 있는지 봐요",
                  "왼쪽 <b>「내 캘린더의 설정」</b>에서 캘린더 이름을 눌러요",
                  "맨 아래 <b>「캘린더 통합」</b>의 <b>「iCal 형식의 비공개 주소」</b> 옆 복사 버튼을 눌러요 → 여기에 저절로 들어와요",
                  "캘린더가 여러 개면 2~3을 반복해요. 하나씩 줄이 늘어나요"],
        "faq": [
            ["설정 화면이 안 나와요", "PC 브라우저에서 열어야 해요. 휴대폰 구글 캘린더 앱에는 이 설정이 없어요. "
                                  "구글에 로그인이 안 돼 있으면 먼저 로그인 화면이 나와요."],
            ["「iCal 형식의 비공개 주소」가 안 보여요", "캘린더 이름을 누른 뒤 페이지를 맨 아래까지 내려 「캘린더 통합」을 찾아요. "
                                                "회사·학교 계정은 관리자가 막아 두면 안 보여요. 그럴 땐 개인 계정 캘린더를 쓰거나 관리자에게 물어봐요."],
            ["공개 주소와 비공개 주소 중 뭘 써요?", "「비공개 주소」를 써요. 공개 주소는 캘린더를 공개로 바꿔야 해서 남도 볼 수 있어요."],
            ["복사했는데 안 들어와요", "「구글 캘린더 설정 열기」를 누른 뒤 15분 안에 복사한 것만 저절로 들어와요. "
                                   "버튼을 다시 누르고 복사하거나, 아래 칸에 직접 붙여 넣어도 돼요."],
            ["일정이 늦게 보여요", "구글 비공개 주소는 바뀐 일정이 반영되기까지 몇 분~몇 시간 걸릴 수 있어요 (구글 쪽 사정)."],
        ]},
    "icloud": {  # Apple 계정 + 앱 암호 (caldav)
        "open": ("Apple 페이지 열기", "https://account.apple.com/account/manage"),
        "steps": ["아래 <b>「Apple 페이지 열기」</b> → 평소 브라우저에서 아이폰과 같은 계정으로 로그인해요",
                  "<b>로그인 및 보안</b> → <b>앱 암호</b> → <b>+</b> → 이름 <b>POPME</b> → 만들기",
                  "나온 <b>xxxx-xxxx-xxxx-xxxx</b>를 복사만 하면 여기에 저절로 들어와요 (한 번만 보여 주니 창을 닫기 전에 복사해요)"],
        "faq": [
            ["「앱 암호」 메뉴가 없어요", "Apple 계정에 2단계 인증이 켜져 있어야 보여요. 아이폰 설정 → 맨 위 내 이름 → 로그인 및 보안 → 2단계 인증을 켜요."],
            ["Apple 계정 이메일을 모르겠어요", "아이폰 설정 맨 위 내 이름을 누르면 보여요. 평소 쓰는 메일과 다를 수 있어요."],
            ["「로그인하지 못했어요」가 나와요", "이메일이 앱 암호를 만든 그 Apple 계정인지 봐요. 그래도 안 되면 앱 암호를 새로 만들어 복사해요. "
                                            "Apple 계정 비밀번호를 바꾸면 예전 앱 암호는 모두 쓸 수 없게 돼요."],
            ["캘린더가 하나도 없대요", "아이폰 설정 → 내 이름 → iCloud에서 캘린더가 켜져 있어야 해요. 켜면 몇 분 뒤 다시 「연결 확인」을 눌러요."],
            ["복사했는데 안 들어와요", "「Apple 페이지 열기」를 누른 뒤 15분 안에 복사한 것만 저절로 들어와요. 앱 암호 칸에 직접 붙여 넣어도 돼요."],
        ]},
    "icloud_ics": {  # 앱 암호 대신 공개 캘린더 주소
        "steps": ["아이폰 <b>캘린더</b> 앱 → 아래 <b>「캘린더」</b> → 쓰는 캘린더 옆 <b>ⓘ</b>",
                  "<b>「공개 캘린더」</b>를 켜고 <b>「링크 공유」</b>로 PC에 보내요 (카톡 나와의 채팅 등)",
                  "PC에서 <code>webcal://</code>로 시작하는 주소를 복사하면 여기에 저절로 들어와요"],
        "faq": [
            ["「공개 캘린더」가 안 보여요", "iCloud 캘린더만 공개로 바꿀 수 있어요. 구글 등 다른 계정 캘린더면 그 서비스를 골라요."],
            ["공개해도 괜찮아요?", "주소를 아는 사람은 누구나 볼 수 있어요. 주소를 남에게 보내지 말아요. 걱정되면 「Apple 계정으로 연결하기」가 더 안전해요."],
        ]},
    "naver": {
        "open": ("네이버 캘린더 열기", "https://calendar.naver.com"),
        "steps": ["아래 <b>「네이버 캘린더 열기」</b> → <b>환경설정</b> → <b>캘린더 관리</b>",
                  "내보내기/공유의 <b>ICS(iCal) 주소</b>를 복사하면 여기에 저절로 들어와요"],
        "faq": [["주소가 안 보여요", "PC 브라우저의 네이버 캘린더에서 찾아요. 모바일 앱에는 이 설정이 없어요."]]},
    "outlook": {
        "open": ("Outlook 캘린더 설정 열기", "https://outlook.live.com/calendar/0/options/calendar/SharedCalendars"),
        "steps": ["아래 <b>「Outlook 캘린더 설정 열기」</b> → <b>공유 캘린더</b>",
                  "<b>캘린더 게시</b>에서 캘린더와 「모든 세부 정보 보기」를 고르고 <b>게시</b>",
                  "<b>ICS 링크</b>를 복사하면 여기에 저절로 들어와요"],
        "faq": [["「캘린더 게시」가 없어요", "회사·학교 계정은 관리자가 막아 두었을 수 있어요. 개인 outlook.com 계정에서 해 봐요."]]},
    "minical": {
        "steps": ["미니캘은 아이폰에 등록된 캘린더를 그대로 보여 줘요",
                  "아이폰 <b>설정 → 앱 → 캘린더 → 캘린더 계정</b>을 열어요 (옛 iOS: 설정 → 캘린더 → 계정)",
                  "<b>iCloud</b>에 캘린더가 켜져 있으면 「아이폰 캘린더」, <b>Gmail</b>이면 「구글 캘린더」를 골라요. "
                  "둘 다 켜져 있으면 일정을 주로 넣는 쪽을 골라요"],
        "faq": [["둘 다 써요", "지금은 한 곳만 연결할 수 있어요. 일정을 주로 넣는 쪽을 고르고, 나중에 설정에서 바꿀 수 있어요."]]},
    "gmail": {
        "open": ("앱 비밀번호 만들기", "https://myaccount.google.com/apppasswords"),
        "steps": ["아래 <b>「앱 비밀번호 만들기」</b> → 평소 브라우저에서 구글에 로그인해요",
                  "앱 이름에 <b>POPME</b> → 만들기",
                  "나온 16자리를 복사하면 위 비밀번호 칸에 저절로 들어와요 (한 번만 보여 줘요)"],
        "faq": [
            ["「사용할 수 없는 설정」이라고 나와요", "구글 계정에 2단계 인증이 켜져 있어야 앱 비밀번호를 만들 수 있어요. "
                                              "<a data-url='https://myaccount.google.com/signinoptions/twosv'>2단계 인증 켜기</a> 뒤 다시 해요. "
                                              "회사·학교 계정은 관리자가 막아 두었을 수 있어요."],
            ["「로그인하지 못했어요」가 나와요", "구글 비밀번호가 아니라 앱 비밀번호 16자리를 넣어야 해요. 구글 비밀번호를 바꾸면 앱 비밀번호도 새로 만들어야 해요."],
            ["복사했는데 안 들어와요", "「앱 비밀번호 만들기」를 누른 뒤 15분 안에 복사한 것만 저절로 들어와요. 비밀번호 칸에 직접 붙여 넣어도 돼요."],
        ]},
    "mail_other": {
        "steps": ["메일 서비스 설정에서 <b>IMAP 사용</b>을 켜요", "2단계 인증을 쓰면 <b>앱 비밀번호</b>를 만들어 넣어요"],
        "faq": [["「로그인하지 못했어요」가 나와요", "메일 설정에서 IMAP 사용을 켰는지, 2단계 인증을 쓴다면 앱 비밀번호를 넣었는지 봐요. "
                                              "회사·학교 메일은 관리자가 막아 두었을 수 있어요."]]},
    "login": {
        "steps": ["사이트 옆 <b>「로그인 열기」</b>를 누르면 POPME 전용 Chrome에 그 사이트가 열려요",
                  "평소처럼 직접 로그인해요. 끝나면 여기 ✓가 저절로 켜져요"],
        "faq": [
            ["전용 Chrome이 뭐예요?", "POPME가 소식을 모을 때 쓰는 Chrome 창이에요. 평소 Chrome과 로그인이 따로라서 여기서 한 번 더 로그인해야 해요."],
            ["비밀번호를 POPME가 봐요?", "아니요. 로그인은 Chrome 안에서 직접 하고, POPME는 로그인됐는지만 확인해요."],
            ["X·Threads 계정이 없어요", "건너뛰어도 돼요. 공식 블로그·릴리스 소식은 계정 없이도 모여요."],
            ["로그인했는데 ✓가 안 켜져요", "로그인을 끝까지 마쳤는지(첫 화면이 보이는지) 봐요. 몇 초 기다리면 저절로 다시 확인해요."],
        ]},
}
# 「막혔어요?」 자유 질문: 이 단계 안내 안에서만 답한다
HELP_SYSTEM = """너는 POPME 설정 창의 도우미다. 사용자는 컴퓨터를 잘 모를 수 있다.
반드시 아래 [안내]와 [자주 막히는 곳]에 적힌 내용만 근거로 답한다.
- 한국어 존댓말, 3문장 이내. 화면에서 무엇을 누르면 되는지만 말한다.
- [안내]로 답할 수 없는 질문이면 정확히 「여기서는 도와드릴 수 없어요. 다른 방법을 고르거나 나중에 설정해도 돼요.」라고만 답한다.
- 명령어·코드·다른 앱이나 사이트를 권하지 않는다. 비밀번호·주소를 보여 달라고 하지 않는다."""
# 로그인 단계: 전용 Chrome에서 사용자가 직접 로그인 → 쿠키(또는 로그인 페이지로 넘기는지)로 확인만 한다
LOGIN_SITES = {
    "x": {"name": "X", "url": "https://x.com/login", "why": "AI 소식 모으기 — 가장 중요해요",
          "cookie": ("https://x.com", "auth_token")},
    "threads": {"name": "Threads", "url": "https://www.threads.com/login", "why": "국내 반응 모으기 (없으면 건너뛰어요)",
                "cookie": ("https://www.threads.com", "sessionid"), "optional": True},
    "timetree": {"name": "TimeTree", "url": "https://timetreeapp.com/signin", "why": "일정 읽기",
                 "page": "https://timetreeapp.com/calendars"},  # 로그인 안 했으면 /signin으로 넘긴다
}
# 클립보드에서 꺼낼 것: 이 모양일 때만 꺼내고 다른 내용은 화면에 넘기지 않는다
CLIP = {
    "apple": (re.compile(r"[a-z]{4}-[a-z]{4}-[a-z]{4}-[a-z]{4}"), lambda t: t),  # Apple 앱 암호
    "google": (re.compile(r"[a-z]{4} ?[a-z]{4} ?[a-z]{4} ?[a-z]{4}"), lambda t: t.replace(" ", "")),  # 구글 앱 비밀번호
    # 캘린더 비공개·공개 주소 (구글 ical, iCloud published, 그 밖의 .ics)
    "ics": (re.compile(r"(?:https|webcal)://\S*(?:\.ics|/ical/\S+|caldav\.icloud\.com/published/\S+)\S*"), lambda t: t),
}


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
    dav = cfg.get("caldav", {})
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
        "caldav": {"user": dav.get("user", ""), "has_password": bool(dav.get("password")),
                   "calendars": [{"name": c.get("name", ""), "url": c.get("url", "")} for c in dav.get("calendars", [])]},
        "use_windows_location": w.get("use_windows_location", True),
        "place": w.get("name", ""),
        "mail": {"supported": mail_supported(), "enabled": mail.get("enabled", False),
                 "host": mail.get("host", "imap.gmail.com"), "user": mail.get("user", ""),
                 "has_password": bool(mail.get("password")), "quiet_hours": mail.get("quiet_hours", "")},
        "mail_domains": MAIL_DOMAINS,
        "character": char,
        "characters": chars,
        "ais": AIS,
        "calendar_apps": CALENDAR_APPS,
        "help": HELP,
        "login_sites": {k: {"name": v["name"], "why": v["why"], "optional": v.get("optional", False)}
                        for k, v in LOGIN_SITES.items()},
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
    dav = None
    if ch["source"] == "caldav":
        d = ch.get("caldav") or {}
        dav = {"user": (d.get("user") or "").strip(), "password": (d.get("password") or "").strip(),
               "calendars": [{"name": (c.get("name") or "").strip() or "캘린더", "url": c["url"].strip()}
                             for c in d.get("calendars") or [] if re.match(r"^https?://\S+$", (c.get("url") or "").strip())]}
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", dav["user"]):
            raise ValueError("Apple 계정 이메일을 확인해 주세요.")
        if not dav["password"] and not current()["caldav"]["has_password"]:
            raise ValueError("앱 암호를 넣어 주세요.")
        if not dav["calendars"]:
            raise ValueError("「연결 확인」을 눌러 읽을 캘린더를 하나 이상 골라 주세요.")
    mail = ch.get("mail")
    if mail and mail_supported():
        mail = {**mail, "enabled": bool(mail.get("enabled")), "user": (mail.get("user") or "").strip(),
                "password": (mail.get("password") or "").replace(" ", ""),  # 앱 비밀번호는 띄어 써서 보여줌
                "quiet_hours": (mail.get("quiet_hours") or "").strip()}
        mail["host"] = (mail.get("host") or "").strip().lower()
        if mail["enabled"] and not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", mail["host"]):
            raise ValueError("메일 서버 주소를 확인해 주세요 (예: imap.gmail.com).")
        if mail["enabled"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail["user"]):
            raise ValueError("메일 주소를 확인해 주세요.")
        if mail["enabled"] and not mail["password"] and not current()["mail"]["has_password"]:
            raise ValueError("메일 앱 비밀번호를 넣어 주세요.")
        hm = r"([01]\d|2[0-3]):[0-5]\d"
        if mail["quiet_hours"] and not re.fullmatch(f"{hm}-{hm}", mail["quiet_hours"]):
            raise ValueError("방해 금지 시간은 23:00-07:00처럼 넣어 주세요.")
    else:
        mail = None
    char = ch.get("character")
    if char == "character.example.toml" and not (config.ROOT / "character.toml").exists():
        # 기본 캐릭터는 이름을 박지 않는다: character.toml을 나중에 만들면 그대로 쓰이고, 없으면 예시로 넘어간다
        char = "character.toml"
    return {**ch, "briefing_after": t, "ics": rows, "caldav": dav, "mail": mail, "character": char,
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
    if ch["source"] in ("ics", "caldav"):
        text = set_value(text, "calendar", "holidays", bool(ch.get("holidays", True)))
    if ch["source"] == "caldav":
        d = ch["caldav"]
        text = set_value(text, "caldav", "server", caldav.ICLOUD, before=("timetree", "weather"))
        text = set_value(text, "caldav", "user", d["user"])
        if d["password"]:  # 비워 두면 저장된 앱 암호를 그대로 둔다
            text = set_value(text, "caldav", "password", d["password"])
        text = set_value(text, "caldav", "calendars", d["calendars"])
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


def host_from_mx(domain, mx_names):
    """MX 서버 이름들 → {host, note}. 아는 곳이 없으면 imap.<도메인>."""
    for mx in mx_names:
        mx = mx.lower().rstrip(".")
        for end, host in MX_HOSTS.items():
            if mx == end or mx.endswith("." + end):
                return {"host": host, "note": ""}
        if mx.endswith(".outlook.com") or mx.endswith(".office365.com"):
            return {"host": "outlook.office365.com", "note": MS_NOTE}
    return {"host": f"imap.{domain}", "note": ""}


def guess_mail_host(domain):
    """메일 주소 도메인 → {host, note}. 목록에 없으면 nslookup으로 MX를 보고 추측한다."""
    domain = (domain or "").strip().lower()
    if domain in MAIL_DOMAINS:
        return {"host": MAIL_DOMAINS[domain], "note": ""}
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain):
        return {"host": "", "note": ""}
    try:
        r = subprocess.run(["nslookup", "-type=mx", domain], capture_output=True, text=True, errors="replace",
                           timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        mxs = re.findall(r"mail exchanger = (\S+)", r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        mxs = []
    return host_from_mx(domain, mxs)


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


def _clipboard(clear=False):
    """Windows 클립보드 글자 (clear=True면 비우기만). 못 열면 ""."""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = wintypes.HANDLE
    k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    if not u32.OpenClipboard(None):
        return ""
    try:
        if clear:
            u32.EmptyClipboard()
            return ""
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        p = k32.GlobalLock(h) if h else None
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def take_clipboard(kind, read=_clipboard):
    """클립보드가 CLIP[kind] 모양 하나뿐이면 꺼내고(정리해서) 클립보드를 비운다. 아니면 "" — 다른 내용은 화면에 넘기지 않는다."""
    pattern, tidy = CLIP[kind]
    text = read().strip()
    if not pattern.fullmatch(text):
        return ""
    if read().strip() == text:  # 그 사이 다른 걸 복사했으면 건드리지 않는다
        read(clear=True)
    return tidy(text)


# ---------------------------------------------------------------- 로그인 단계 (전용 Chrome)

def _chrome_port():
    return int(tomllib.loads(base_text()).get("chrome", {}).get("port", 9333))


def login_status(sites):
    """{"chrome": 전용 Chrome이 켜져 있나, site: True/False}. 꺼져 있으면 site는 None (알 수 없음)."""
    from popme import chrome
    port = _chrome_port()
    if not chrome.is_running(port):
        return {"chrome": False, **{s: None for s in sites}}
    from playwright.sync_api import sync_playwright
    out = {"chrome": True}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            ctx = browser.contexts[0]
            for s in sites:
                site = LOGIN_SITES[s]
                try:
                    if "cookie" in site:
                        url, name = site["cookie"]
                        out[s] = any(c["name"] == name and c["value"] for c in ctx.cookies([url]))
                    else:  # 쿠키 이름으로는 알 수 없는 곳: 로그인 페이지로 넘기는지 본다 (화면은 안 연다)
                        out[s] = ctx.request.get(site["page"], max_redirects=0, timeout=15000).status == 200
                except Exception:
                    log.warning("%s 로그인 확인 실패", s, exc_info=True)
                    out[s] = None
        finally:
            browser.close()  # CDP 연결만 끊는다 (Chrome은 그대로)
    return out


def open_login(site):
    """전용 Chrome에 그 사이트 로그인 화면을 연다 (꺼져 있으면 켠다)."""
    from popme import chrome
    port, url = _chrome_port(), LOGIN_SITES[site]["url"]
    if not chrome.is_running(port):
        chrome.ensure_chrome(port, [url])
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            page = browser.contexts[0].new_page()
            page.goto(url)
            page.bring_to_front()
        finally:
            browser.close()


def request_first_briefing():
    """저장 뒤 POPME가 켜지면 바로 수집·브리핑을 시작하라는 표시 (app.py가 보고 지운다)."""
    config.FIRST_BRIEFING.write_text("1", encoding="utf-8")


def _plain(html):
    return re.sub(r"<[^>]+>", "", html)


def ask_help(provider, key, question):
    """「막혔어요?」 자유 질문 — HELP[key] 안내 안에서만 짧게 답한다 (도구 끈 AI). 실패하면 ValueError."""
    from popme import llm
    h = HELP[key]
    guide = "\n".join(f"{i}. {_plain(t)}" for i, t in enumerate(h["steps"], 1))
    faq = "\n".join(f"- {q}: {_plain(a)}" for q, a in h.get("faq", []))
    prompt = f"[안내]\n{guide}\n\n[자주 막히는 곳]\n{faq}\n\n[사용자 질문]\n{question.strip()[:300]}"
    try:
        return llm.ask(_llm_cfg(provider), HELP_SYSTEM, prompt, timeout=90).strip()[:400]
    except Exception as e:
        raise ValueError(f"AI를 부르지 못했어요. 「브리핑 AI」 단계에서 준비됐는지 봐 주세요. ({str(e)[:80]})")


def check_caldav(user, password):
    """Apple 계정 + 앱 암호로 로그인해 일정 캘린더 목록. 앱 암호를 비우면 저장된 것으로. 실패하면 ValueError."""
    password = (password or "").strip()
    if not password:
        password = tomllib.loads(base_text()).get("caldav", {}).get("password", "")
    if not (user or "").strip() or not password:
        raise ValueError("Apple 계정 이메일과 앱 암호를 넣어 주세요.")
    cals = caldav.discover(user, password)
    if not cals:
        raise ValueError("로그인은 됐는데 일정 캘린더가 없어요. 아이폰 설정에서 iCloud 캘린더가 켜져 있는지 봐 주세요.")
    return cals


# ---------------------------------------------------------------- AI CLI 설치·로그인·캘린더 연결 (새 터미널)

def _llm_cfg(provider):
    """저장된 [llm] 설정(claude_path 등)에 고른 AI만 바꿔 끼운 cfg."""
    return {"llm": {**tomllib.loads(base_text()).get("llm", {}), "provider": provider}}


def ai_status(provider):
    """{installed, logged_in(True/False/None=알 수 없음), path, plan(알아낸 요금제, 없으면 "")}"""
    from popme import llm
    finder = {"claude": llm.find_claude, "codex": llm.find_codex, "gemini": llm.find_gemini}[provider]
    try:
        exe = finder(_llm_cfg(provider))
    except RuntimeError:
        return {"installed": False, "logged_in": False, "plan": ""}
    home, plan = Path.home(), ""

    def run(*args):
        return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if provider == "codex":
        try:
            r = run("login", "status")
            logged = "logged in" in (r.stdout + r.stderr).lower()
        except Exception:
            logged = None
    elif provider == "claude":
        logged = None
        try:  # claude auth status: {"loggedIn": true, "subscriptionType": "pro", ...} (옛 버전엔 없음)
            st = json.loads(run("auth", "status", "--json").stdout)
            logged = bool(st.get("loggedIn"))
            plan = {"pro": "Claude Pro", "max": "Claude Max"}.get(str(st.get("subscriptionType", "")).lower(), "")
        except Exception:
            logged = (home / ".claude" / ".credentials.json").exists() or None  # 다른 방식 로그인이면 알 수 없음
    else:
        logged = (home / ".gemini" / "oauth_creds.json").exists() or None
    return {"installed": True, "logged_in": logged, "path": exe, "plan": plan}


def ai_test(provider):
    """고른 AI를 실제로 한 번 불러 본다 (도구 끈 상태, 짧은 답). 실패하면 ValueError(사용자에게 보일 문장)."""
    from popme import llm
    name = AIS[provider]["name"]
    try:
        answer = llm.ask(_llm_cfg(provider), "짧게 한 문장으로만 답한다.", "「준비됐어요」라고만 답해 주세요.", timeout=120)
    except subprocess.TimeoutExpired:
        raise ValueError(f"{name}이(가) 2분 안에 답하지 않았어요. 잠시 뒤 「다시 불러 보기」를 눌러 주세요.")
    except (RuntimeError, OSError) as e:
        err = str(e).lower()
        if any(k in err for k in ("login", "auth", "api key", "credential", "401")):
            raise ValueError(f"{name} 로그인이 필요해요. 「로그인 하기」를 눌러 주세요.")
        if any(k in err for k in ("limit", "quota", "429", "rate")):
            raise ValueError(f"{name} 사용 한도에 걸렸어요. 한도가 풀리면 브리핑도 써져요. 그대로 다음으로 가도 돼요.")
        raise ValueError(f"{name}을(를) 불러 보지 못했어요: {str(e)[:200]}")
    if not answer.strip():
        raise ValueError(f"{name}이(가) 빈 답을 줬어요. 「다시 불러 보기」를 눌러 주세요.")
    return answer.strip()[:60]


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

    def ai_test(self, provider):
        try:
            return {"ok": True, "answer": ai_test(provider)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def guess_mail_host(self, domain):
        return guess_mail_host(domain)

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def check_ics(self, url):
        try:
            return {"ok": True, **check_ics(url)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def check_caldav(self, user, password):
        try:
            return {"ok": True, "calendars": check_caldav(user, password)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def take_clipboard(self, kind):
        try:
            return take_clipboard(kind)
        except Exception:
            log.exception("클립보드 읽기 실패")
            return ""

    def login_status(self, sites):
        try:
            return login_status(sites)
        except Exception as e:
            log.exception("로그인 확인 실패")
            return {"chrome": False, "error": str(e)[:200]}

    def open_login(self, site):
        try:
            open_login(site)
            return {"ok": True}
        except Exception as e:
            log.exception("로그인 창 열기 실패")
            return {"ok": False, "error": f"전용 Chrome을 열지 못했어요: {str(e)[:150]}"}

    def ask_help(self, provider, key, question):
        try:
            return {"ok": True, "answer": ask_help(provider, key, question)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def request_first_briefing(self):
        request_first_briefing()

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
    if sys.stdout:  # 트레이에서 pythonw로 띄우면 콘솔이 없어 None — 여기서 죽으면 설정 창이 아예 안 뜬다
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    try:
        if args[:1] == ["check-ics"] and len(args) == 2:
            info = check_ics(args[1])
            print(f"확인됨: 일정 {info['events']}개" + (f", 캘린더 이름 「{info['name']}」" if info["name"] else ""))
        elif args[:1] == ["check-caldav"] and len(args) == 2:  # 설정은 그대로 두고 iCloud 연결만 시험
            import getpass
            pw = getpass.getpass("앱 암호 (입력해도 화면에 안 보여요): ")
            for c in check_caldav(args[1], pw):
                n = len(caldav.fetch_events(c, args[1], pw))
                print(f"  {c['name']}: 일정 {n}개")
        elif not args:
            sys.exit(main())
        else:
            print(__doc__)
            sys.exit(2)
    except ValueError as e:
        print("실패:", e)
        sys.exit(1)
