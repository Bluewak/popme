import json
import time

from popme.config import RAW_DIR


# 수집 경로 (tweets.via 접두어). 읽는 쪽(briefing.build_data의 재료 구획, discovery)은 이 이름으로 고른다.
# 경로를 더하면 SECTION에 어느 재료 구획에 드는지 적는다 (None = 재료에 직접 넣지 않음) — tests/test_via.py가 확인한다.
VIA_X_USER, VIA_X_SEARCH, VIA_X_CHECK = "user:", "search:", "check:"
VIA_TH_USER, VIA_TH_SEARCH, VIA_TH_TAG, VIA_TH_CHECK = "th-user:", "th-search:", "th-tag:", "th-check:"
SECTION = {VIA_X_USER: "X 핵심 계정", VIA_X_SEARCH: "한국어 X 검색", VIA_X_CHECK: None,
           VIA_TH_USER: "Threads 계정", VIA_TH_SEARCH: "Threads 검색·태그", VIA_TH_TAG: "Threads 검색·태그", VIA_TH_CHECK: None}


class NeedLogin(Exception):
    """전용 Chrome에서 로그인이 풀렸거나 차단 신호가 보임 → 즉시 중단하고 사용자에게 알림."""


def reached_since(posts, owner, since):
    """프로필 주인 글 중 since보다 오래된 게 3개 이상 보이면 그 아래는 볼 필요가 없다.
    고정글은 오래돼도 맨 위에 뜨므로 1~2개는 여유로 둔다. created_at·since는 UTC ISO 문자열."""
    owner = owner.lower()
    old = sum(1 for p in posts if (p.get("author") or "").lower() == owner
              and p.get("created_at") and p["created_at"] < since)
    return old >= 3


def dump_raw(kind: str, name: str, data, keep: int = 30):
    """파서가 깨졌을 때 고칠 수 있도록 최근 원본 응답만 남긴다."""
    d = RAW_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{int(time.time() * 1000)}_{name}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    files = sorted(d.glob("*.json"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)
