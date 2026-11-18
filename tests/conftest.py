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
    """테스트는 내 설정·캐릭터·이미지·저장한 비밀번호를 읽지도 고치지도 않는다 — 빈 임시 폴더와 메모리로 돌려 기본값을 쓰게."""
    from popme import config, vault
    data = tmp_path / "data"
    (data / "assets").mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "ASSETS_DIR", data / "assets")
    monkeypatch.setattr(config, "CONFIG_PATH", data / "config.toml")
    for name in ("RAW_DIR", "BRIEFING_DIR"):  # 원본 응답 덤프·브리핑 md도 내 폴더 대신
        (data / name.lower()).mkdir()
        monkeypatch.setattr(config, name, data / name.lower())
    monkeypatch.setattr(config, "FIRST_BRIEFING", data / "first-briefing")
    saved = {}
    monkeypatch.setattr(vault, "get", lambda name: saved.get(name, ""))
    monkeypatch.setattr(vault, "put", lambda name, value: saved.update({name: value}) or True)
    monkeypatch.setattr(vault, "delete", lambda name: saved.pop(name, None) is not None)
    monkeypatch.setattr(vault, "saved", saved, raising=False)


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
    from popme import accounts
    monkeypatch.setattr(accounts, "X_VISITS", tmp_path / "x-visits.json")
    monkeypatch.setattr(accounts, "X_LIST", tmp_path / "x-list.json")


@pytest.fixture(autouse=True)
def assume_online(monkeypatch):
    """테스트는 인터넷 확인을 하지 않는다 (있다고 놓음). 끊긴 경우는 test_offline.py에서 바꿔 끼운다."""
    from popme import jobs
    monkeypatch.setattr(jobs, "online", lambda timeout=4: True)


@pytest.fixture(autouse=True)
def no_local_domains(monkeypatch, tmp_path):
    """분야 페르소나도 내 PC 것(DATA_DIR/domains) 대신 임시 폴더로."""
    from popme import domains
    monkeypatch.setattr(domains, "LOCAL_DIR", tmp_path / "domains")


@pytest.fixture(autouse=True)
def no_background_fieldcard(monkeypatch):
    """질문 단계가 뒤에서 띄우는 사실 확인(fieldcard)이 다른 테스트의 가짜 AI 답을 가로채지 않게 — 그 테스트에서만 켠다."""
    from popme import fieldcard
    monkeypatch.setattr(fieldcard, "BACKGROUND", False)
    fieldcard._jobs.clear()


@pytest.fixture(autouse=True, scope="session")
def never_touch_my_folder():
    """테스트가 끝난 뒤 실제 사용자 폴더(%LOCALAPPDATA%/POPME)에 새 파일이 생기지 않았는지.
    2026-11-18 상수 경로 하나를 conftest가 바꿔 끼우지 않아 테스트가 예시 설정을 내 폴더에 저장한 적이 있다.
    (있던 파일이 바뀐 것은 보지 않는다 — 같은 PC에서 앱이 돌며 상태 파일을 고칠 수 있어 흔들린다)"""
    from popme import config
    root = config.DATA_DIR
    skip = ("popme.db", "popme.log", "chrome-profile", "raw", "venv", "llm-empty")

    def snap():
        out = {}
        for p in root.glob("*") if root.exists() else ():
            if p.is_file() and not p.name.startswith(skip):
                out[p.name] = p.stat().st_mtime_ns
        return out
    before = snap()
    yield
    after = snap()
    new = sorted(set(after) - set(before))
    assert not new, f"테스트가 실제 사용자 폴더에 파일을 만들었어요: {new}"
