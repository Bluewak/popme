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
