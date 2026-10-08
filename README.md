# POPME

Windows 화면 구석에 사는 **데스크톱 브리핑 비서**. 내 PC의 로그인된 브라우저로 X·Threads·캘린더를 읽고, 매일 아침 AI 업계 소식을 "나한테 영향 있는 것만" 브리핑해 주는 캐릭터 위젯입니다.

클라우드 에이전트는 로그인이 필요한 SNS를 못 본다는 문제에서 시작했습니다. POPME는 **내 PC에서, 내 세션으로** 읽습니다.

## 할 수 있는 것
- **아침 브리핑**: 헤드라인 5~8개(✓확인됨 / ?미확인 / !긴급) + 자세히. 혜택·크레딧은 "무료? / 내 요금제 대상? / 기한"으로 나눠 보여 줌. 사람 말은 공식 변경내역·저장소와 맞춰 봄
- **수집**: X 계정·검색, Threads 토픽 태그, RSS(공식 릴리스·블로그·GeekNews·HN). 수집은 브라우저가 받는 JSON을 가로채는 방식이라 **LLM 토큰 0**
- **발굴**: 핵심 계정이 자주 리포스트하는 사람, 여러 태그에 반복 등장하고 반응이 큰 한국어 작성자를 후보로 추천
- **일정**: 오늘/내일/모레, 반복 일정, 바뀐 일정 표시, 30분 전 알림, **일정 장소의 그날 날씨**. "금요일 3시 치과" 부탁 → 승인 카드 → 캘린더에 추가 (v1은 TimeTree, [다른 캘린더 쓰기](docs/CALENDAR.md))
- **캐릭터**: 표정이 바뀌고, 말을 걸고, 졸고, 쓰다듬으면 반응. 말투·대사·이미지는 [바꿀 수 있음](docs/CHARACTER.md)
- **질문**: 수집한 자료만 근거로 답변

## 필요한 것
- Windows 11 (둥근 창 테두리·위치 서비스. Windows 10에서도 대부분 동작)
- Python 3.11+
- Google Chrome
- **Claude Code** (요약·답변에 `claude -p`를 씀. Claude 구독으로 동작, 별도 API 키 불필요). PATH에 없으면 VS Code 확장의 claude.exe를 자동으로 찾음

## 설치
```powershell
git clone <이 저장소>
cd POPME
powershell -ExecutionPolicy Bypass -File .\install.ps1
```
패키지 설치, `config.toml` 생성, 로그인 시 자동 실행, 바탕화면 바로가기까지 만듭니다.

## 처음 한 번
1. 바탕화면 **POPME** 실행 → 트레이 아이콘 우클릭 → **전용 Chrome 열기 (로그인)**
2. 열린 Chrome에서 X, Threads, TimeTree에 로그인 (평소 Chrome과 별개 프로필)
3. `config.toml`에서 볼 X 계정·Threads 태그·RSS·요금제를 내 관심사로 수정
4. 캐릭터 우클릭 → **지금 수집**

## 사용
| 동작 | 결과 |
|---|---|
| 캐릭터 클릭 | 말풍선 → 한 번 더: 패널(브리핑·일정·발굴·질문) → 한 번 더: 닫기 |
| 끌기 / 좌우로 문지르기 | 이동 / 쓰다듬기 |
| 우클릭 | 브리핑 열기 · 지금 수집 · 숨기기 · 종료 |
| 일정 탭 입력 | "금요일 3시 치과" → 승인 카드 → [넣어줘] |

09:00 이후 앱이 켜져 있고 오늘 브리핑이 없으면 자동으로 수집·브리핑합니다 (`config.toml` `briefing_after`).

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
- **보내는 데이터**: 요약·해석은 Claude(내 구독)로. 현재 위치·일정 장소는 약 1km로 반올림한 좌표를 OpenStreetMap Nominatim(지명)과 Open-Meteo(날씨)로 보냅니다. 끄려면 `config.toml` `[weather] use_windows_location = false`.
- **프롬프트 인젝션 대비**: 수집한 글을 요약하는 단계에는 도구 권한이 없고(`claude -p --tools ""`), 일정 추가처럼 쓰기 동작은 사용자가 승인 카드를 눌러야만 실행됩니다.

## 디버깅
- `python -m popme run-once`: 위젯 없이 수집+브리핑 1회
- 로그: `%LOCALAPPDATA%\POPME\popme.log`

## 라이선스·출처
- 폰트: [주아체](https://fonts.google.com/specimen/Jua) (SIL Open Font License, `assets/fonts/Jua-OFL.txt`)
- 날씨: [Open-Meteo](https://open-meteo.com/) · 지명: [OpenStreetMap Nominatim](https://nominatim.org/) (© OpenStreetMap contributors)
- 코드: [MIT License](LICENSE)
