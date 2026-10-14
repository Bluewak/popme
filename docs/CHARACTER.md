# 나만의 캐릭터 만들기

POPME의 캐릭터는 **파일 두 종류**로 정해집니다. 코드는 고칠 필요 없습니다.

| 무엇 | 파일 | 없으면 |
|---|---|---|
| 이름·호칭·말투·대사·행동 수치 | `character.toml` (또는 config.toml `[character] file`로 지정) | `character.example.toml` (기본 캐릭터 "포피") |
| 이미지 | `assets/pet.png` 외 | 주황 방울 모양 |

둘 다 `.gitignore`에 들어 있어서 저장소에 올라가지 않고, 새 버전을 설치할 때 예전 폴더에서 가져옵니다. **`character.example.toml`은 직접 고치지 마세요** — 업데이트하면 원래대로 돌아갑니다. **다른 작품의 캐릭터를 쓴다면 개인 PC에서만 쓰고 공개하지 마세요.**

## 1. 말투·대사
POPME 폴더에서:
```powershell
copy character.example.toml character.toml
```
`character.toml`을 메모장으로 열어 고칩니다. `[persona] name`도 바꿔 두면 설정 창에서 기본 캐릭터와 구분됩니다.

- `[persona]`: `name`(이름표·메뉴), `call_user`(호칭, 대사에서 `{user}`), `prompt`(AI가 아침 브리핑 때 '오늘의 멘트'를 만들 때 따르는 말투 설명)
- `[ui]`: 말풍선·패널의 고정 문구
- `[lines]`: 상황별 대사 묶음. 묶음 안에서 무작위로 고르고, 같은 날 같은 대사는 되도록 반복하지 않음
- `[behavior]`: 표정 바뀌는 간격·비율, 졸린 시간대, 잡담 간격, 쓰다듬기 쿨타임
- `[weather_advice]`: 일정 장소 날씨 대사 끝에 붙는 한마디

자리표시: `{title}` `{n}` `{headline}` `{msg}` `{date}` `{place}` `{place_eun}`(장소+은/는) `{min}` `{max}` `{rain}` `{day}` `{desc}` `{advice}` `{month}` `{name}`(공휴일 이름). 대사에 없는 자리표시를 쓰면 그 대사는 건너뜁니다.

- `[lines]`·`[ui]`는 다시 켜면 바로, `prompt`는 **다음 브리핑부터** 바뀝니다 (오늘 멘트는 이미 만들어 둔 것).
- 브리핑 카드 본문과 질문 탭 답변은 캐릭터 말투를 따르지 않습니다.

## 2. 이미지
`assets` 폴더에 넣습니다. `.png` `.webp` `.gif` 모두 되고, 배경이 투명한 정사각형(256px 이상)이 가장 예쁩니다.

| 파일 | 언제 |
|---|---|
| `pet.png` (필수) | 평소 |
| `pet_busy.png` | 수집 중 |
| `pet_happy.png` | 새 소식·쓰다듬기 |
| `pet_sleepy.png` | 자리 비움·졸린 시간대 |
| `pet_alert.png` | 경고 (없으면 평소 이미지가 떨며 땀방울) |

흰 배경 이미지는 배경을 따서 맞춰 줍니다. POPME 폴더의 PowerShell에서, 설치 때 만든 Python으로 실행하세요 (그냥 `python`에는 이미지 라이브러리가 없습니다):
```powershell
& "$env:LOCALAPPDATA\POPME\venv\Scripts\python.exe" assets\prepare.py "C:\경로\내캐릭터.png" pet
& "$env:LOCALAPPDATA\POPME\venv\Scripts\python.exe" assets\prepare.py "C:\경로\열심.png" pet_busy
```
바탕화면·트레이 아이콘(`assets/pet.ico`)은 POPME가 켜질 때 `pet.png`로 다시 만듭니다. 바탕화면 아이콘은 윈도우가 늦게 바꿀 수 있습니다.

## 3. 적용
캐릭터 우클릭 → 종료 → 바탕화면 **POPME**로 다시 실행.

말투가 그대로면 트레이 아이콘 우클릭 → **설정 바꾸기**에서 새 캐릭터를 고르고 저장하세요. 예전 버전의 설정 창은 기본 캐릭터 파일 이름을 `config.toml`에 적어 두어서, `character.toml`을 만들어도 바로 쓰이지 않습니다. 캐릭터 파일을 여러 개 두고 설정 창에서 바꿔 가며 쓸 수도 있습니다.
