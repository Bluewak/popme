import logging
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "POPME"
DB_PATH = DATA_DIR / "popme.db"
CHROME_PROFILE = DATA_DIR / "chrome-profile"
BRIEFING_DIR = DATA_DIR / "briefings"
RAW_DIR = DATA_DIR / "raw"          # 파서 디버깅용 원본 응답 (최근 것만 유지)
LOG_PATH = DATA_DIR / "popme.log"

for d in (DATA_DIR, BRIEFING_DIR, RAW_DIR):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    encoding="utf-8",
)


def load() -> dict:
    """개인 설정 config.toml (저장소 제외) → 없으면 config.example.toml."""
    path = CONFIG_PATH if CONFIG_PATH.exists() else ROOT / "config.example.toml"
    with open(path, "rb") as f:
        return tomllib.load(f)
