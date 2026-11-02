import logging
import os
import re
import shutil
import tomllib
from pathlib import Path

from popme import vault

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
        text = path.read_text(encoding="utf-8")
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
        path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


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


def load() -> dict:
    """개인 설정 config.toml (DATA_DIR) → 없으면 기본 예시.
    예전 형식(관심사 없이 [[rss]]만)이면 먼저 새 형식으로 옮긴다 (백업: config.toml.bak-날짜).
    파일에 평문 비밀번호가 있으면 자격 증명 관리자로 옮기고, 돌려주는 값에는 비밀번호를 채워 준다."""
    from popme import catalog
    hide_passwords()
    try:
        note = catalog.migrate_file()
        if note:
            logging.getLogger(__name__).info(note)
    except Exception:
        logging.getLogger(__name__).exception("소식 출처 옮기기 실패 — 예전 형식 그대로 씀")
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for s in SECRET_SECTIONS:
        if s in cfg and not cfg[s].get("password"):
            cfg[s]["password"] = vault.get(s)
    return cfg
