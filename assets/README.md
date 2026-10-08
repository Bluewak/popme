# 캐릭터 이미지

여기에 넣으면 앱을 다시 켤 때 반영됩니다. 없으면 기본 방울 모양이 나옵니다.
이 폴더의 `pet*` 이미지는 `.gitignore`로 저장소에 올라가지 않습니다.

| 파일 | 언제 |
|---|---|
| `pet.png` (필수) | 평소 |
| `pet_busy.png` | 수집 중 |
| `pet_happy.png` | 새 소식·쓰다듬기 |
| `pet_sleepy.png` | 자리 비움·졸린 시간대 |
| `pet_alert.png` | 경고 (없으면 평소 이미지가 떨며 땀방울) |

- `.png` `.webp` `.gif` 중 아무거나 됩니다. 배경이 투명한 정사각형(256px 이상)이 가장 예쁩니다.
- 흰 배경 이미지는 `python assets\prepare.py <원본> pet` 처럼 배경을 따서 넣을 수 있습니다.
- 이름·말투·대사는 `docs/CHARACTER.md` 참고.
