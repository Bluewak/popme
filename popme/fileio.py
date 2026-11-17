"""파일을 통째로 바꿔 쓰기 — 쓰는 도중 꺼져도 예전 파일이나 새 파일 중 하나가 남는다.

예전엔 write_text로 바로 덮어써서, 쓰는 중에 전원이 꺼지거나 백신이 파일을 잡으면 config.toml이 잘린 채 남았고
그다음부터 앱이 아무 표시 없이 켜지지 않았다. 같은 폴더의 임시 파일에 다 쓴 뒤 os.replace로 바꿔 끼운다.
"""
import os
import tempfile
import time
from pathlib import Path


def atomic_write_text(path, text, encoding="utf-8", newline="\n"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):  # Windows: 다른 프로그램(백신·편집기)이 잠깐 잡고 있으면 바꿔 끼우기가 거부된다
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
