# 일정: TimeTree 대신 다른 캘린더 쓰기

POPME는 기본으로 **TimeTree**를 씁니다. 구글·iCloud 등은 **A(ICS 주소)를 설정만 하면** 바로 쓸 수 있습니다. TimeTree 공식 API는 2023-12-22에 종료됐기 때문에, 로그인해 둔 전용 Chrome에서 TimeTree 웹 앱과 같은 요청을 보내 일정을 읽고 씁니다.

다른 캘린더(Google, 네이버, Outlook, Apple 등)를 쓰려면 **일정을 다루는 4곳만** 바꾸면 됩니다. 브리핑·수집·캐릭터 쪽은 손대지 않아도 됩니다.

## 1. 일정 데이터 형식 (이것만 맞추면 나머지는 그대로 동작)

모든 일정은 DB `events` 테이블에 아래 dict로 저장됩니다 (`popme/collectors/timetree.py`의 `_event()`가 만드는 형식).

| 키 | 예 | 설명 |
|---|---|---|
| `id` | `"abc123"` | 일정 고유 ID (문자열). 공휴일은 `"holiday:..."` |
| `calendar_id` | `"12345678:가족 캘린더"` | `"<캘린더ID>:<캘린더 이름>"` |
| `title` | `"11시 회의"` | 제목 |
| `start_at`, `end_at` | `"2026-10-08T00:00:00+00:00"` | ISO 시각 (UTC). **종일 일정은 그 날짜의 UTC 자정** |
| `all_day` | `1` | 종일이면 1 |
| `updated_at` | `"1791417600000"` | 바뀌었는지 판단용 (문자열이면 무엇이든 OK) |
| `deleted` | `0` | 지워졌으면 1 |
| `recurrences` | `'["RRULE:FREQ=WEEKLY"]'` | 반복 규칙 JSON 문자열 (RFC 5545 RRULE/EXDATE). 없으면 `"[]"` |
| `location` | `"한강공원"` | 장소 칸 (없으면 `None`) — 일정 장소 날씨에 씀 |

이 형식으로 넣으면 오늘/내일/모레 표시, 반복 일정 펼치기, 새로 생김·바뀜·지워짐 표시, 일정 30분 전 알림, 일정 장소 날씨가 모두 그대로 동작합니다 (`popme/agenda.py`, `popme/event_weather.py`).

## 2. 바꿀 곳

| # | 하는 일 | 지금 코드 (TimeTree) | 바꿀 내용 |
|---|---|---|---|
| ① | **일정 읽기** | `popme/collectors/timetree.py` `collect(ctx, cfg, progress)` → `(events, synced_calendar_ids)` | 같은 모양을 돌려주는 함수 |
| ② | 읽기 호출 | `popme/jobs.py` `_browser_collect()` 안의 `timetree.collect(...)` 한 줄 | ①의 새 함수로 교체 (브라우저가 필요 없으면 `_run()`의 RSS 수집 옆으로 옮겨도 됨) |
| ③ | **일정 추가** (선택) | `popme/collectors/timetree.py` `create_event(ctx, cfg, calendar_id, title, date)` | 새 서비스의 추가 API. 읽기 전용이면 생략 |
| ④ | 추가 호출 | `popme/app.py` `Api.add_event()` | ③의 새 함수 호출. 생략하면 일정 탭의 "부탁하기"를 숨기면 됨 |

`synced_calendar_ids`는 "이번에 전체를 다시 읽은 캘린더 목록"입니다. 여기 들어간 캘린더에서 사라진 일정은 '지워짐'으로 표시됩니다. 변경분만 읽는 방식이면 빈 리스트를 주세요.

캘린더 목록은 `popme/planner.py` `calendar_names()`가 DB의 `calendar_id`에서 뽑으므로 따로 바꿀 필요 없습니다.

## 3. 서비스별 방법

### A. ICS(iCal) 주소로 읽기 — 설정만 하면 됨 (내장), 읽기 전용
코드가 이미 들어 있습니다 (`popme/collectors/ics.py`). `config.toml`에 주소만 넣으세요.

```toml
[calendar]
source = "ics"
holidays = true      # 대한민국 공휴일도 표시 (구글 공휴일 캘린더)
ics = [
  { name = "내 캘린더", url = "https://calendar.google.com/calendar/ical/.../private-.../basic.ics" },
]
```

캘린더별 주소 얻는 법 (비공개 주소는 **비밀번호처럼** 다루세요 — 주소만 알면 누구나 일정을 볼 수 있음)
- **구글 캘린더**: PC에서 calendar.google.com → 왼쪽 캘린더 이름 옆 ⋮ → 설정 및 공유 → 맨 아래 **"iCal 형식의 비공개 주소"** 복사
- **iCloud(아이폰 기본 캘린더)**: 아이폰 캘린더 앱 → 아래 '캘린더' → 캘린더 옆 ⓘ → **"공개 캘린더" 켜기** → 링크 공유로 `webcal://...` 주소 복사 (그대로 붙여 넣어도 됨)
- **네이버 캘린더**: 환경설정 → 캘린더 관리 → 내보내기/공유의 ICS 주소
- **Outlook**: 설정 → 캘린더 → 공유 캘린더 → 캘린더 게시 → ICS 링크
- **미니캘·타임블럭스 같은 아이폰 캘린더 앱**: 일정을 직접 갖지 않고 iCloud·구글 캘린더와 동기화하는 앱이 많습니다. 앱 설정에서 어느 계정과 연결돼 있는지 보고, **그 원본 캘린더**의 주소를 위 방법으로 넣으세요.

알아둘 점
- 수집할 때마다 전체를 다시 받아서, 새로 생김·바뀜·지워짐 표시와 반복 일정(뺀 날·옮긴 회차 포함), 여러 날 일정, 일정 장소 날씨가 모두 그대로 동작합니다.
- ICS는 읽기만 되므로 일정 탭의 "부탁하기"(일정 추가)는 숨겨집니다.
- 구글 비공개 주소는 반영이 몇 분~몇 시간 늦을 수 있습니다 (구글 쪽 캐시).
### B. Google Calendar API — 읽기·쓰기
1. Google Cloud에서 프로젝트 생성 → Calendar API 사용 설정 → OAuth 클라이언트(데스크톱 앱) 만들기 → `credentials.json` 받기
2. `pip install google-api-python-client google-auth-oauthlib`
3. 처음 한 번 브라우저 로그인으로 `token.json` 저장 (둘 다 .gitignore에 추가)
4. 읽기: `service.events().list(calendarId=..., timeMin=..., singleEvents=False)` → 위 형식으로 변환 (`recurrence` 필드가 RRULE 목록)
5. 쓰기(③): 종일 일정은 `{"summary": title, "start": {"date": date}, "end": {"date": date + 1일}}`로 `events().insert()`

### C. 네이버·Outlook 등 다른 서비스
- 공식 API가 있으면 B처럼, 없으면 A(ICS)로 읽기만 하거나
- TimeTree처럼 **전용 Chrome에 로그인해 두고 웹 앱의 요청을 따라 하는** 방법도 있습니다. `timetree.py`의 `_session()`(헤더 얻기)과 `FETCH_JS`(페이지 안에서 fetch) 패턴을 그대로 참고하세요. 웹 앱이 바뀌면 깨질 수 있습니다.

### D. 일정 기능 끄기
`config.toml`에서 `[calendar] source = "none"`으로 두면 됩니다. 브리핑·캐릭터·날씨는 그대로 동작하고, 일정 관련 대사와 표시만 빠집니다.

## 4. 사용자 습관에 맞는 부분 (필요하면 같이 바꾸기)
- 일정 추가는 **종일 일정 + 제목에 시간**("3시 치과")으로 넣습니다. 시간 지정 일정으로 넣고 싶으면 `planner.py`의 프롬프트와 ③에서 시간을 쓰도록 바꾸세요.
- 종일 일정 제목의 시각("11시 회의")을 읽어 30분 전 알림을 줍니다 (`agenda.parse_time`). 오전/오후 표시가 없는 1~7시는 오후로 봅니다.
