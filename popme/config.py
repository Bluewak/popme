import logging
import os
import re
import shutil
import tomllib
from pathlib import Path

from popme import vault
from popme.fileio import atomic_write_text

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_DIR = ROOT / "popme" / "defaults"  # 앱과 함께 배포되는 기본 설정·캐릭터
EXAMPLE_CONFIG = DEFAULTS_DIR / "config.example.toml"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "POPME"
# 사용자 파일은 앱 폴더 밖에: 새 버전을 어디에 풀어도 그대로고, 저장소에 섞이지 않는다
CONFIG_PATH = DATA_DIR / "config.toml"
ASSETS_DIR = DATA_DIR / "assets"    # 직접 만든 캐릭터의 이미지 (pet.png 외)
DB_PATH = DATA_DIR / "popme.db"
CHROME_PROFILE = DATA_DIR / "chrome-profile"
BRIEFING_DIR = DATA_DIR / "briefings"
RAW_DIR = DATA_DIR / "raw"          # 파서 디버깅용 원본 응답 (최근 것만 유지)
LOG_PATH = DATA_DIR / "popme.log"
FIRST_BRIEFING = DATA_DIR / "first-briefing"  # 설정 창의 「첫 브리핑 받아 보기」 → 켜지자마자 수집 (app.py가 지움)

# 비밀번호는 config.toml이 아니라 자격 증명 관리자에 둔다 (popme/vault.py)
SECRET_SECTIONS = ("mail", "caldav")
PASSWORD_LINE = re.compile(r"""^(\s*password\s*=\s*)("(?:[^"\\]|\\.)*"|'[^']*')""")
# 예전 앱 폴더의 assets에 같이 배포되던 것 — 개인 파일이 아니라 옮기지 않는다 (pet.ico는 켤 때 다시 만든다)
OLD_APP_ASSETS = {"README.md", "prepare.py", "fonts", "characters", "pet.ico", "__pycache__"}

for d in (DATA_DIR, BRIEFING_DIR, RAW_DIR, ASSETS_DIR):
    d.mkdir(parents=True, exist_ok=True)

LOG_MAX = 5 * 1024 * 1024  # 이보다 크면 켤 때 popme.log.1로 넘긴다 (예전엔 끝없이 커졌다)
try:  # 설정 창 프로세스도 같은 파일에 써서 RotatingFileHandler(쓰는 중 이름 바꾸기)는 Windows에서 실패한다 — 켤 때 한 번만
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX:
        os.replace(LOG_PATH, LOG_PATH.with_suffix(".log.1"))
except OSError:
    pass  # 다른 프로세스가 잡고 있으면 다음에

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    encoding="utf-8",
)


def image_dir(folder=""):
    """캐릭터 파일 [persona] images → 이미지 폴더. 비우면 내 이미지, 기본 캐릭터는 DEFAULTS_DIR 안."""
    if not folder:
        return ASSETS_DIR
    return (DATA_DIR if Path(folder).parts[0] == "assets" else DEFAULTS_DIR) / folder


def _hide(path, overwrite):
    """path의 평문 비밀번호를 자격 증명 관리자로 옮기고 파일의 값은 비운다. 못 옮기면 평문을 그대로 둔다 (계정이 끊기면 안 되므로).
    overwrite=False: 이미 저장된 값이 있으면 그 값을 둔다 (백업·옛 폴더의 값이 지금 값을 덮지 않게)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
        cfg = tomllib.loads(text)
    except (OSError, ValueError):
        return
    lines, section, changed = text.split("\n"), "", False
    for i, line in enumerate(lines):
        head = re.match(r"\s*\[+([^\[\]]+)\]+", line)
        if head:
            section = head.group(1).strip()
        elif section in SECRET_SECTIONS:
            value = cfg[section].get("password")
            new, hit = PASSWORD_LINE.subn(r'\1""', line)
            if hit and value and ((not overwrite and vault.get(section)) or vault.put(section, value)):
                lines[i], changed = new, True
    if changed:
        atomic_write_text(path, "\n".join(lines))


def hide_passwords():
    _hide(CONFIG_PATH, overwrite=True)


def _is_character(path):
    try:
        return "persona" in tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


def adopt_old_files(folder=None):
    """예전 버전이 앱 폴더에 두던 개인 설정·캐릭터·이미지를 DATA_DIR로 옮긴다 → 옮긴 파일 수.
    복사가 끝난 뒤 원본을 지우고, 이미 있는 파일은 덮어쓰지 않는다. 옛 폴더에 평문 비밀번호가 남지 않게 하는 게 목적이다."""
    folder = Path(folder) if folder else ROOT
    files = [(p, DATA_DIR / p.name) for p in folder.glob("config.toml.bak-*")]
    files += [(p, DATA_DIR / p.name) for p in folder.glob("*.toml")
              if p.name == "config.toml" or (not p.name.endswith(".example.toml") and _is_character(p))]
    assets = folder / "assets"
    for top in assets.iterdir() if assets.is_dir() else ():
        if top.name not in OLD_APP_ASSETS:
            for p in ([top] if top.is_file() else (f for f in top.rglob("*") if f.is_file())):
                files.append((p, ASSETS_DIR / p.relative_to(assets)))
    moved = 0
    for src, dst in files:
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        src.unlink()
        moved += 1
    if moved:
        logging.getLogger(__name__).info("앱 폴더의 개인 파일 %d개를 옮겼어요: %s → %s", moved, folder, DATA_DIR)
    hide_passwords()
    for p in (folder / "config.toml", *folder.glob("config.toml.bak-*"), *DATA_DIR.glob("config.toml.bak-*")):
        _hide(p, overwrite=False)  # 이미 새 설정이 있어 남은 옛 파일·백업에도 비밀번호를 두지 않는다
    return moved


LOAD_ERROR = None  # 사용자 설정을 못 읽어 대신 쓴 경우 그 이유 (app이 켜질 때 말풍선으로 알린다)


def last_good():
    """마지막으로 앱이 저장한 읽히는 설정 (tomledit.save_config). 상수로 두지 않는다 — CONFIG_PATH를 바꿔 끼운 테스트가
    실제 사용자 폴더에 쓰지 않게 (2026-11-18 상수로 뒀다가 테스트가 예시 설정을 내 폴더에 저장했다)."""
    return CONFIG_PATH.with_name("config.toml.last-good")


def load() -> dict:
    """개인 설정 config.toml (DATA_DIR) → 없으면 기본 예시. 읽기만 한다 (옮기기·비밀번호 숨기기는 catalog.prepare_config).
    파일이 깨졌으면(메모장으로 고치다 따옴표가 빠지는 등) 마지막으로 앱이 저장한 설정(last-good), 그것도 없으면 기본 예시로 켜고
    LOAD_ERROR에 이유를 남긴다. 예전엔 예외가 나서 pythonw 앱이 아무 표시 없이 켜지지 않았다. 사용자 파일은 덮어쓰지 않는다.
    돌려주는 값에는 자격 증명 관리자의 비밀번호를 채워 준다."""
    global LOAD_ERROR
    LOAD_ERROR = None
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG
    try:
        cfg = tomllib.loads(path.read_text(encoding="utf-8-sig"))  # 메모장이 붙이는 BOM도 읽는다
    except ValueError as e:
        logging.getLogger(__name__).exception("설정 파일을 못 읽음: %s", path)
        LOAD_ERROR = f"설정 파일(config.toml)을 읽지 못해 마지막으로 저장한 설정으로 켰어요 ({e})"
        alt = last_good() if last_good().exists() else EXAMPLE_CONFIG
        cfg = tomllib.loads(alt.read_text(encoding="utf-8-sig"))
    if LOAD_ERROR:  # 대신 켠 설정의 계정(예전 메일 주소 등)에 지금 저장된 비밀번호를 붙여 다른 서버로 보내지 않는다
        return cfg
    for s in SECRET_SECTIONS:
        if s in cfg and not cfg[s].get("password"):
            cfg[s]["password"] = vault.get(s)
    return cfg
