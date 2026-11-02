"""requirements.txt(허용 범위)와 requirements.lock(실제로 깔리는 버전)이 어긋나지 않게."""
import re
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent


def test_lock_satisfies_requirements():
    locked = dict(re.findall(r"^([a-z0-9._-]+)==(\S+)", (ROOT / "requirements.lock").read_text(encoding="utf-8"), re.M))
    locked = {re.sub(r"[-_.]+", "-", k.lower()): v for k, v in locked.items()}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            req = Requirement(line)
            name = re.sub(r"[-_.]+", "-", req.name.lower())
            assert name in locked, f"{name}이 잠금 파일에 없음 — uv pip compile을 다시 돌리세요"
            assert req.specifier.contains(locked[name]), f"{name}: 잠금 {locked[name]}이 {req.specifier} 밖"


def test_lock_is_hashed_and_ascii():
    """install.ps1은 --require-hashes로 설치하고, pip은 이 파일을 시스템 코드 페이지로 읽는다."""
    raw = (ROOT / "requirements.lock").read_bytes()
    assert raw.isascii()
    assert b"--hash=sha256:" in raw
