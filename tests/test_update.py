"""새 버전 알림 (popme/update.py): 버전 비교·하루 한 번만 묻기·같은 버전은 한 번만 알리기."""
from datetime import date

import pytest

from popme import __version__, update


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "STATE", tmp_path / "update-check.json")


@pytest.mark.parametrize("tag, current, newer", [
    ("v1.10.0", "1.9.9", True),   # 글자가 아니라 숫자로 비교
    ("v2.0.0", "1.9.9", True),
    ("v1.6.0", "1.6.0", False),
    ("v1.5.9", "1.6.0", False),
    ("nightly", "1.6.0", False),
    ("", "1.6.0", False),
    (None, "1.6.0", False),
])
def test_is_newer(tag, current, newer):
    assert update.is_newer(tag, current) is newer


def test_app_version_is_parseable():
    assert update.parse(__version__)


def test_asks_github_once_a_day(monkeypatch):
    calls = []
    monkeypatch.setattr(update, "latest", lambda: calls.append(1) or "v99.0.0")
    day = date(2026, 11, 2)
    assert update.check(day) == "v99.0.0"
    assert update.check(day) == "v99.0.0"
    assert len(calls) == 1
    assert update.check(date(2026, 11, 3)) == "v99.0.0"
    assert len(calls) == 2


def test_no_news_when_up_to_date_or_offline(monkeypatch):
    monkeypatch.setattr(update, "latest", lambda: f"v{__version__}")
    assert update.check(date(2026, 11, 2)) is None

    def fail():
        raise OSError("인터넷 없음")
    monkeypatch.setattr(update, "latest", fail)
    assert update.check(date(2026, 11, 3)) is None


def test_same_version_told_once():
    assert update.first_notice("v99.0.0") is True
    assert update.first_notice("v99.0.0") is False
    assert update.first_notice("v99.0.1") is True
