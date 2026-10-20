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


@pytest.fixture(autouse=True)
def no_feed_cache(monkeypatch, tmp_path):
    """RSS 조건부 요청 캐시(ETag 등)도 내 PC 것 대신 임시 파일로."""
    from popme.sources import rss
    monkeypatch.setattr(rss, "CACHE_PATH", tmp_path / "feed-cache.json")
    monkeypatch.setattr(rss, "_cache", None)


@pytest.fixture(autouse=True)
def no_custom_profiles(monkeypatch, tmp_path):
    """새로 만든 관심사(DATA_DIR/profiles)도 내 PC 것 대신 빈 임시 폴더로."""
    from popme import catalog
    monkeypatch.setattr(catalog, "CUSTOM_DIR", tmp_path / "profiles")
    catalog.reload()
    yield
    catalog.reload()


@pytest.fixture(autouse=True)
def no_x_visits(monkeypatch, tmp_path):
    from popme import briefing
    monkeypatch.setattr(briefing, "X_VISITS", tmp_path / "x-visits.json")


@pytest.fixture(autouse=True)
def assume_online(monkeypatch):
    """테스트는 인터넷 확인을 하지 않는다 (있다고 놓음). 끊긴 경우는 test_offline.py에서 바꿔 끼운다."""
    from popme import jobs
    monkeypatch.setattr(jobs, "online", lambda timeout=4: True)
