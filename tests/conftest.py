import sys
from datetime import timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 저장소 루트의 popme 패키지

KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def korea_pc(monkeypatch):
    """테스트는 'PC가 한국에 있다'고 놓고 돈다 (CI 서버 시계는 UTC). 다른 시간대 동작은 test_clock.py에서."""
    from popme import clock
    monkeypatch.setattr(clock, "tz", lambda: KST)


@pytest.fixture(autouse=True)
def no_personal_config(monkeypatch, tmp_path):
    """테스트는 내 config.toml을 읽지도 고치지도 않는다 (config.load가 예전 형식을 옮기므로) — 없는 경로로 돌려 example을 쓰게."""
    from popme import config
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
