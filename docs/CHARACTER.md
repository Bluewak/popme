# 나만의 캐릭터 만들기

POPME의 캐릭터는 **파일 두 종류**로 정해집니다. 코드는 고칠 필요 없습니다.

| 무엇 | 파일 | 없으면 |
|---|---|---|
| 이름·호칭·말투·대사·행동 수치 | `character.toml` (또는 config.toml `[character] file`로 지정) | `character.example.toml` (기본 캐릭터 "포피") |
| 이미지 | `assets/pet.png` 외 | 주황 방울 모양 |

둘 다 `.gitignore`에 들어 있어서 저장소에 올라가지 않습니다. **다른 작품의 캐릭터를 쓴다면 개인 PC에서만 쓰고 공개하지 마세요.**

## 1. 말투·대사
```powershell
copy character.example.toml character.toml
```
- `[persona]`: `name`(이름표·메뉴), `call_user`(호칭, 대사에서 `{user}`), `prompt`(Claude가 '오늘의 멘트'를 만들 때 따르는 말투 설명)
- `[ui]`: 말풍선·패널의 고정 문구
- `[lines]`: 상황별 대사 묶음. 묶음 안에서 무작위로 고르고, 같은 날 같은 대사는 되도록 반복하지 않음
- `[behavior]`: 표정 바뀌는 간격·비율, 졸린 시간대, 잡담 간격, 쓰다듬기 쿨타임
- `[weather_advice]`: 일정 장소 날씨 대사 끝에 붙는 한마디

자리표시: `{title}` `{n}` `{headline}` `{msg}` `{date}` `{place}` `{place_eun}`(장소+은/는) `{min}` `{max}` `{rain}` `{day}` `{desc}` `{advice}` `{month}` `{name}`(공휴일 이름)

## 2. 이미지
| 파일 | 언제 |
|---|---|
| `pet.png` (필수) | 평소 |
| `pet_busy.png` | 수집 중 |
| `pet_happy.png` | 새 소식·쓰다듬기 |
| `pet_sleepy.png` | 자리 비움·졸린 시간대 |
| `pet_alert.png` | 경고 (없으면 평소 이미지가 떨며 땀방울) |

흰 배경 이미지는 배경을 따서 맞춰 줍니다:
```powershell
python assets\prepare.py "C:\경로\내캐릭터.png" pet
python assets\prepare.py "C:\경로\열심.png" pet_busy
```
바탕화면·트레이 아이콘은 `assets/pet.ico` (install.ps1이 pet.png로 만들어 줌).

## 3. 적용
캐릭터 우클릭 → 종료 → 다시 실행.
