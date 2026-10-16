# POPME

Windows 화면 구석에 사는 **데스크톱 브리핑 비서**. 내 PC의 로그인된 브라우저로 X·Threads·캘린더를 읽고, 매일 아침 AI 업계 소식을 "나한테 영향 있는 것만" 브리핑해 주는 캐릭터 위젯입니다.

클라우드 에이전트는 로그인이 필요한 SNS를 못 본다는 문제에서 시작했습니다. POPME는 **내 PC에서, 내 세션으로** 읽습니다.

## 할 수 있는 것
- **아침 브리핑**: 소식을 **카드**로(뉴스 제목처럼 한 줄 + 누르면 설명·링크). ✓확인됨 / ?미확인 / !긴급, "꼭 볼 것 / 알아두면 좋은 것 / 나머지", 분류별 보기. 카드마다 "나한테 해당? · 할 일 · 무료/기한" 표시. 사람 말은 공식 변경내역·저장소와 맞춰 보고, 자료에 없는 링크는 코드가 버림
- **수집**: X 계정·검색, Threads 토픽 태그, RSS(공식 릴리스·블로그·GeekNews·HN). 수집은 브라우저가 받는 JSON을 가로채는 방식이라 **LLM 토큰 0**
- **발굴**: 핵심 계정이 자주 리포스트하는 사람, 여러 태그에 반복 등장하고 반응이 큰 한국어 작성자를 후보로 추천
- **일정**: 오늘/내일/모레, 반복 일정, 바뀐 일정 표시, 30분 전 알림, **일정 장소의 그날 날씨**. "금요일 3시 치과" 부탁 → 승인 카드 → 캘린더에 추가. 캘린더: **TimeTree**(읽기·쓰기) 또는 **구글·iCloud·네이버·아웃룩 등 ICS 주소**(읽기 전용) — [설정법](docs/CALENDAR.md)
- **중요 메일 알림**: Gmail(IMAP)을 읽기 전용으로 지켜보다가 중요한 메일만 말풍선으로 알림 (보통 1~2분 안), 아침 브리핑에 "밤사이 중요 메일". 규칙(중요 발신자·키워드·무시) → 못 정한 것만 AI — [설정법](docs/MAIL.md)
- **캐릭터**: 표정이 바뀌고, 말을 걸고, 졸고, 쓰다듬으면 반응. 말투·대사·이미지는 [바꿀 수 있음](docs/CHARACTER.md)
- **질문**: 수집한 자료만 근거로 답변

## 필요한 것
- Windows 11 (둥근 창 테두리·위치 서비스. Windows 10에서도 대부분 동작)
- Python 3.11~3.13 (없으면 설치 스크립트가 winget으로 3.12를 설치)
- Google Chrome
- 요약·답변용 AI 구독 하나 (별도 API 키 불필요, 설치할 때 설정 창에서 고름 → `config.toml` `[llm] provider`)
  - **Claude** (`provider = "claude"`, 기본): Claude Code의 `claude -p`. PATH에 없으면 VS Code 확장의 claude.exe를 자동으로 찾음
  - **ChatGPT** (`provider = "codex"`): Codex CLI의 `codex exec`. 설정 창에서 설치(`winget install OpenAI.Codex`)와 `codex login`까지
  - **Gemini** (`provider = "gemini"`): Gemini CLI의 `gemini -p`. Google AI Pro 구독 또는 무료 한도. 설정 창에서 설치(Node.js + `npm i -g @google/gemini-cli`)와 Google 로그인까지

## 설치
[Releases](https://github.com/Bluewak/popme/releases)에서 `POPME-v*.zip`을 받아 풀고 **`install.bat`을 더블클릭**하세요.
(`install.ps1`을 우클릭 → "PowerShell에서 실행"하면 PC의 스크립트 실행 정책에 막혀 창이 바로 닫힐 수 있습니다. `install.bat`은 이번 실행에만 정책을 풀어 줍니다.)

```powershell
# 또는 저장소에서
git clone https://github.com/Bluewak/popme.git
cd popme
.\install.bat
```
설치 스크립트가 하는 일:
1. Python 3.11~3.13을 찾고(3.12 우선), 없으면 winget으로 3.12 설치. WebView2가 없으면 설치
2. 켜져 있는 POPME를 끄고, 패키지를 `%LOCALAPPDATA%\POPME\venv`에 따로 설치 (PC의 다른 Python과 안 섞이고, 깊은 경로 제한에도 안 걸림)
3. 새 버전을 다른 폴더에 풀어 설치하면 예전 폴더의 `config.toml`·`character.toml`·캐릭터 이미지를 가져옴
4. **설정 창** → 시작프로그램·바탕화면 바로가기 → POPME 켜기
5. 과정은 `%LOCALAPPDATA%\POPME\install.log`에 남음 (설치가 멈추면 이 파일 확인)

**지우기**: `uninstall.bat` 더블클릭 → POPME를 끄고 바로가기·패키지 환경을 지움. 데이터(브리핑 기록·전용 Chrome 로그인)는 물어보고 지움. 그다음 폴더를 지우면 끝.

**설정 창** 순서: 준비물 확인 → 브리핑 AI(설치·로그인) → 캘린더 → 메일 알림 → 로그인(전용 Chrome) → 날씨·브리핑 시각 → 첫 브리핑 받아 보기. 고른 값은 `config.toml`에 저장됩니다(주석·다른 설정은 그대로). 나중에 바꾸려면 트레이 아이콘(또는 캐릭터) 우클릭 → **설정 바꾸기**. 저장하면 POPME가 다시 켜집니다.

계정·주소는 **평소 브라우저에서 만들고 복사만** 하면 됩니다: 설정 창의 「Apple 페이지 열기」·「구글 캘린더 설정 열기」·「앱 비밀번호 만들기」 뒤 15분 동안, 클립보드가 앱 암호·캘린더 주소 모양일 때만 가져와 채우고 클립보드에서 지웁니다. 막히면 **막혔어요?** → 자주 막히는 곳은 정해진 답, 그 밖의 질문은 고른 AI가 그 단계 안내 안에서만 답합니다 (`popme/setup_window.py` `HELP`). **로그인** 단계는 사이트별 「로그인 열기」로 전용 Chrome에 직접 로그인하면 저절로 ✓가 켜집니다.

## 처음 한 번
설정 창의 **로그인** 단계에서 X, Threads(, TimeTree)에 로그인하고 마지막에 **첫 브리핑 받아 보기**를 누르면 끝입니다. 건너뛰었다면:
1. 트레이 아이콘 우클릭 → **전용 Chrome 열기 (로그인)** → X, Threads(, TimeTree)에 로그인 (평소 Chrome과 별개 프로필)
2. (선택) `config.toml`에서 볼 X 계정·Threads 태그·RSS를 내 관심사로 수정
3. 캐릭터 우클릭 → **지금 수집**

## 사용
| 동작 | 결과 |
|---|---|
| 캐릭터 클릭 | 말풍선 → 한 번 더: 패널(브리핑·일정·발굴·질문) → 한 번 더: 닫기 |
| 끌기 / 좌우로 문지르기 | 이동 / 쓰다듬기 |
| 우클릭 | 브리핑 열기 · 지금 수집 · 숨기기 · 종료 |
| 일정 탭 입력 | "금요일 3시 치과" → 승인 카드 → [넣어줘] |

09:00 이후 앱이 켜져 있고 오늘 브리핑이 없으면 자동으로 수집·브리핑합니다 (`config.toml` `briefing_after`).

## 캐릭터 바꾸기
1. **이미지**: `assets` 폴더에 `pet.png`를 넣습니다 (상황별 `pet_busy` `pet_happy` `pet_sleepy` `pet_alert`는 선택). 배경이 투명한 정사각형이 가장 예쁩니다
2. **이름·말투·대사**: `character.example.toml`을 복사해 `character.toml`로 이름을 바꾸고 메모장으로 고칩니다. 예시 파일은 직접 고치지 마세요 (업데이트하면 원래대로 돌아감)
3. 캐릭터 우클릭 → 종료 → 다시 실행. 말투가 그대로면 트레이 우클릭 → **설정 바꾸기**에서 새 캐릭터를 고르고 저장

바탕화면·트레이 아이콘은 켜질 때 새 이미지로 바뀌고, AI가 만드는 '오늘의 멘트'는 다음 브리핑부터 새 말투를 따릅니다. 흰 배경 따기, 대사 칸 설명 등 자세한 방법은 [docs/CHARACTER.md](docs/CHARACTER.md).

## 설정 파일
| 파일 | 내용 | 저장소 |
|---|---|---|
| `config.toml` | 수집 계정·태그·RSS·캘린더·날씨 지역 | 제외 (예시: `config.example.toml`) |
| `character.toml` | 캐릭터 이름·말투·대사·행동 | 제외 (예시: `character.example.toml`) |
| `assets/pet*.png` | 캐릭터 이미지 | 제외 |
| `%LOCALAPPDATA%\POPME` | DB, 브리핑 Markdown, 로그, 전용 Chrome 프로필, 파서 디버깅용 원본 응답 | — |

## 꼭 알아둘 점
- **계정 위험**: X·Threads·TimeTree를 브라우저 자동화로 읽습니다. 사람 속도로 읽기만 하도록 만들었지만, 각 서비스 약관상 자동화는 제한될 수 있고 계정 제재 가능성이 0은 아닙니다. 본인 책임으로 쓰세요.
- **잘 깨지는 부분**: 서비스들이 내부 API를 자주 바꿉니다. 수집이 0개가 되면 `%LOCALAPPDATA%\POPME\raw\`의 원본 응답을 보고 `popme/collectors/*.py` 파서를 고치세요 (X는 이름이 아니라 내용으로 트윗을 찾게 해 둠).
- **보내는 데이터**: 요약·해석은 고른 AI(Claude·ChatGPT·Gemini, 내 구독)로. 현재 위치·일정 장소는 약 1km로 반올림한 좌표를 OpenStreetMap Nominatim(지명)과 Open-Meteo(날씨)로 보냅니다. 끄려면 `config.toml` `[weather] use_windows_location = false`.
- **메일** (켰을 때만): 메일은 내 PC가 메일 서버에서 직접 받고, 규칙으로 못 정한 메일의 보낸사람·제목·본문 앞 300자만 고른 AI로 보냅니다 (`[mail] ai = false`면 안 보냄). 앱 비밀번호는 `config.toml`에 평문으로 저장됩니다.
- **프롬프트 인젝션 대비**: 수집한 글을 요약하는 단계에는 도구 권한이 없고(`claude -p --tools ""` / Codex는 셸·코드 실행·브라우저 등 도구를 전부 끄고 빈 폴더·읽기 전용으로 실행 / Gemini는 빈 폴더 설정으로 내장 도구·MCP·확장을 전부 끄고 실행), 일정 추가처럼 쓰기 동작은 사용자가 승인 카드를 눌러야만 실행됩니다.

## 개발
- 테스트: `python -m pip install pytest` → `python -m pytest -q tests`
- **개인 정보 지킴이**: 이 저장소는 개인용 POPME 폴더에서 바로 올립니다. 개인 파일은 `.gitignore`로 빼고, 실수로 섞이지 않게 두 겹으로 검사합니다 (`tools/check_private.py`)
  - 커밋 직전(pre-commit 훅): `python tools/check_private.py --install`로 한 번 설치
  - 푸시마다(GitHub Actions `CI`): 같은 검사 + 테스트 + 문법 검사
  - 막는 것: 개인 설정·캐릭터 파일, 이미지·DB·로그·zip, 개인 이메일, 개인 단어(목록은 공개하지 않음 — 로컬 `.private-words.txt` / Secret `PRIVATE_WORDS`)
- 배포: `git tag v1.1.0` → `git push origin v1.1.0` 하면 Actions `Release`가 검사·테스트 후 zip을 Releases에 올림

## 디버깅
- `python -m popme run-once`: 위젯 없이 수집+브리핑 1회
- 로그: `%LOCALAPPDATA%\POPME\popme.log`

## 라이선스·출처
- 폰트: [주아체](https://fonts.google.com/specimen/Jua) (SIL Open Font License, `assets/fonts/Jua-OFL.txt`)
- 날씨: [Open-Meteo](https://open-meteo.com/) · 지명: [OpenStreetMap Nominatim](https://nominatim.org/) (© OpenStreetMap contributors)
- 코드: [MIT License](LICENSE)
