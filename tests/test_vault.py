"""자격 증명 관리자 (popme/vault.py) — conftest가 가짜로 바꿔 둔 함수 대신 실제 Windows API로 왕복한다."""
import importlib.util
import uuid
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("real_vault", Path(__file__).resolve().parent.parent / "popme/vault.py")
assert spec and spec.loader
real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(real)


@pytest.fixture
def name():
    """내 PC의 POPME 비밀번호와 겹치지 않는 시험용 이름."""
    real.PREFIX = f"POPME-test-{uuid.uuid4().hex}/"
    yield "mail"
    real.delete("mail")


def test_round_trip(name):
    assert real.get(name) == ""
    if not real.put(name, "앱-비밀번호 1234"):
        pytest.skip("이 세션은 자격 증명을 저장할 수 없음")
    assert real.get(name) == "앱-비밀번호 1234"
    assert real.put(name, "new")
    assert real.get(name) == "new"
    assert real.delete(name) is True
    assert real.get(name) == "" and real.delete(name) is False


def test_too_long_is_refused(name):
    assert real.put(name, "x" * (real.MAX_BLOB + 1)) is False
