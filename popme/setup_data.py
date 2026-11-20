"""설정 창(popme/setup_window.py)이 쓰는 고정 표: 고를 수 있는 AI·일정 출처, 메일 서버 추측, AI 설치·로그인 명령,
캘린더 앱 안내, 단계별 도움말, 로그인 사이트, 클립보드에서 꺼낼 모양. 코드 없이 값만 둔다."""
import re

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
# open = 「… 열기」 버튼이 여는 주소. 화면과 「AI한테 해결 요청하기」가 같은 내용을 쓴다
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
                  "<b>iCloud</b>에 캘린더가 켜져 있으면 「iCloud (아이폰 계정)」, <b>Gmail</b>이면 「구글 계정」을 골라요. "
                  "둘 다 켜져 있으면 일정을 주로 넣는 쪽을 골라요"],
        "faq": [["둘 다 써요", "지금은 한 곳만 연결할 수 있어요. 일정을 주로 넣는 쪽을 고르고, 나중에 설정에서 바꿀 수 있어요."]]},
    "gmail": {
        "open": ("앱 비밀번호 만들기", "https://myaccount.google.com/apppasswords"),
        "steps": ["아래 <b>「앱 비밀번호 만들기」</b> → 평소 브라우저에서 ① 메일 주소의 구글 계정으로 로그인해요",
                  "앱 이름에 <b>POPME</b> → 만들기",
                  "나온 16자리를 마우스로 끌어 <b>모두</b> 선택하고 <b>Ctrl+C</b> → 아래 ③ 칸에 저절로 들어와요 (한 번만 보여 줘요)"],
        "faq": [
            ["「사용할 수 없는 설정」이라고 나와요", "구글 계정에 2단계 인증이 켜져 있어야 앱 비밀번호를 만들 수 있어요. "
                                              "<a data-url='https://myaccount.google.com/signinoptions/twosv'>2단계 인증 켜기</a> 뒤 다시 해요. "
                                              "회사·학교 계정은 관리자가 막아 두었을 수 있어요."],
            ["「로그인하지 못했어요」가 나와요", "구글 비밀번호가 아니라 앱 비밀번호 16자리를 넣어야 해요. 구글 비밀번호를 바꾸면 앱 비밀번호도 새로 만들어야 해요."],
            ["복사했는데 안 들어와요", "「앱 비밀번호 만들기」를 누른 뒤 15분 안에 복사한 것만 저절로 들어와요. "
                                   "16자리를 모두 선택했는지 보고, 안 되면 ③ 칸을 누르고 Ctrl+V로 붙여 넣어도 돼요."],
            ["다른 구글 계정으로 열려요", "브라우저 오른쪽 위 프로필 사진을 눌러 ① 메일 주소와 같은 계정으로 바꾼 뒤 다시 만들어요."],
            ["16자리 창을 닫아 버렸어요", "다시 볼 수는 없어요. 같은 페이지에서 POPME를 지우고 새로 만들면 돼요."],
        ]},
    "mail_other": {
        "steps": ["메일 서비스 설정에서 <b>IMAP 사용</b>을 켜요", "2단계 인증을 쓰면 <b>앱 비밀번호</b>를 만들어 넣어요"],
        "faq": [["「로그인하지 못했어요」가 나와요", "메일 설정에서 IMAP 사용을 켰는지, 2단계 인증을 쓴다면 앱 비밀번호를 넣었는지 봐요. "
                                              "회사·학교 메일은 관리자가 막아 두었을 수 있어요."]]},
    "ai": {  # 브리핑 AI 단계: 설치·로그인·시험 호출
        "steps": ["<b>「설치·로그인 시작」</b>을 누르면 검은 PowerShell 창이 열리고 설치 글자가 지나가요 (정상이에요)",
                  "설치가 끝나면 브라우저가 열려요. 구독 중인 계정으로 로그인만 하면 여기서 저절로 알아채요",
                  "마지막으로 10~30초 동안 AI를 시험 삼아 불러 봐요. 답이 오면 「다음」을 눌러요"],
        "faq": [
            ["검은 창이 바로 닫혀요", "창이 닫혀도 설치는 끝났을 수 있어요. 여기 「프로그램 설치」 줄이 ✓로 바뀌었는지 보고, 아니면 「설치·로그인 시작」을 다시 눌러요."],
            ["로그인했는데 계속 기다려요", "브라우저에서 로그인을 끝까지 마쳤는지(첫 화면이 보이는지) 봐요. 구독 중인 계정과 같은 계정으로 로그인해야 해요. 안 되면 「창 다시 열기」를 눌러요."],
            ["「사용 한도」라고 나와요", "구독의 사용 한도에 걸린 거예요. 한도가 풀리는 시각 뒤에 「다시 불러 보기」를 눌러요."],
            ["「다시 불러 보기」도 실패해요", "로그인 줄이 ✓인지 먼저 봐요. 그래도 안 되면 「창 다시 열기」로 로그인을 한 번 더 하고, 몇 분 뒤 다시 불러 봐요. "
                                      "급하면 다른 AI를 골라도 돼요."],
            ["구독이 없어요", "Gemini는 무료 요금제로도 쓸 수 있어요. 위에서 Gemini를 골라요."],
        ]},
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
# 「AI한테 해결 요청하기」에 적은 상황: 이 단계 안내 안에서만 답한다
HELP_SYSTEM = """너는 POPME 설정 창의 도우미다. 사용자는 컴퓨터를 잘 모를 수 있다.
반드시 아래 [안내]와 [자주 막히는 곳]에 적힌 내용만 근거로 답한다.
- 한국어 존댓말, 3문장 이내. 화면에서 무엇을 누르면 되는지만 말한다.
- [안내]로 답할 수 없는 질문이면 정확히 「이 단계와 관련된 것만 도와드릴 수 있어요. 다른 방법을 고르거나 나중에 설정해도 돼요.」라고만 답한다.
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
