"""새 버전 알림: GitHub Releases의 최신 버전과 지금 버전을 비교한다. 받아서 설치하는 건 사용자가 한다."""
import json
import logging
import re
import urllib.request
from datetime import date

from popme import __version__, config

log = logging.getLogger(__name__)
API = "https://api.github.com/repos/Bluewak/popme/releases/latest"
PAGE = "https://github.com/Bluewak/popme/releases/latest"
STATE = config.DATA_DIR / "update-check.json"


def parse(tag):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag, current=__version__):
    new, now = parse(tag), parse(current)
    return bool(new and now and new > now)


def latest():
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"POPME/{__version__}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["tag_name"]


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check(today=None):
    """새 버전이 있으면 태그 (예: "v1.7.0"), 없거나 못 물어보면 None. GitHub에는 하루 한 번만 묻는다."""
    today = (today or date.today()).isoformat()
    state = _state()
    if state.get("day") != today:
        try:
            state.update(day=today, tag=latest())
        except (OSError, ValueError, KeyError):
            log.info("새 버전 확인 실패", exc_info=True)
            return None
        STATE.write_text(json.dumps(state), encoding="utf-8")
    return state["tag"] if is_newer(state.get("tag")) else None


def first_notice(tag):
    """이 버전을 처음 알리는 거면 True (같은 버전은 한 번만 말한다)."""
    state = _state()
    if state.get("told") == tag:
        return False
    STATE.write_text(json.dumps({**state, "told": tag}), encoding="utf-8")
    return True
