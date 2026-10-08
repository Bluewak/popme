import json
import time

from popme.config import RAW_DIR


class NeedLogin(Exception):
    """전용 Chrome에서 로그인이 풀렸거나 차단 신호가 보임 → 즉시 중단하고 사용자에게 알림."""


def dump_raw(kind: str, name: str, data, keep: int = 30):
    """파서가 깨졌을 때 고칠 수 있도록 최근 원본 응답만 남긴다."""
    d = RAW_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{int(time.time() * 1000)}_{name}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    files = sorted(d.glob("*.json"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)
