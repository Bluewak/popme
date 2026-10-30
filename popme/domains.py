"""분야 페르소나: 그 분야를 오래 본 한 사람의 상세 프로필 (설계 4-8).

일 페르소나(질문 설계자·편집자·취향 분석가 …, popme/personas)가 「이 사람의 눈으로」 일하게 지시문에 붙인다.
묶음이 있는 분야는 조사해서 확인한 프로필(popme/domains/<묶음 id>.json), 새 분야는 앱이 프로필 작가 페르소나로 그 자리에서
펼쳐 %LOCALAPPDATA%\\POPME\\domains\\<관심사 id>.json에 둔다 (다음부터 재사용).
"""
import json
import logging
import re

from popme import config

log = logging.getLogger(__name__)
REPO_DIR = config.ROOT / "popme" / "domains"
LOCAL_DIR = config.DATA_DIR / "domains"
KEYS = ["persona", "fan_types", "categories", "cares", "dislikes", "sensitive", "vocabulary", "calendar", "must_know",
        "personal", "question_ideas"]
# 소식 종류별 유통기한(설계 4-9) → 며칠. 루머·장중 소식은 「시간」, 정책·주간 순위는 「일주일」.
HALF_LIFE = {"시간": 0.25, "하루": 1, "며칠": 3, "일주일": 7, "한 달": 30}
DEFAULT_AGE = 2
NUDGE = ("strong", "medium", "weak")  # X 로그인 권유 세기


def load(pid):
    """관심사 id의 분야 페르소나 (없으면 None). 내 PC 것이 먼저."""
    for d in (LOCAL_DIR, REPO_DIR):
        f = d / f"{pid}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("분야 페르소나 읽기 실패: %s", f, exc_info=True)
    return None


def save_local(pid, data):
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    (LOCAL_DIR / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _s(v, n):
    """한 줄로 정리하고 n자까지. 넘치면 낱말 경계에서 자른다 (「…접수 시」처럼 낱말 중간에서 끊겨 검색어·지시문에 쓰이지 않게)."""
    s = " ".join(str(v or "").split())
    if len(s) <= n:
        return s
    cut = s[:n]
    if " " in cut[n // 2:]:
        cut = cut[:cut.rfind(" ")]
    log.info("분야 페르소나 글을 잘랐어요 (%d자): %s", n, s)
    return cut.rstrip(" ·,(/")


def clean(raw, pid=""):
    """프로필 작가가 준 것 → 정해진 모양 (칸이 빠지면 빈 값, 길이 제한). 판단에 쓰이니 지나치게 긴 것은 자른다."""
    p = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    lst = lambda k, n=12: [x for x in (raw.get(k) or []) if x][:n]
    out = {"id": pid or raw.get("id", ""),
           "persona": {k: _s(p.get(k), 200) for k in ("name", "born", "home", "career", "entry_story", "personality", "voice")},
           "fan_types": [{"name": _s(x.get("name"), 20), "desc": _s(x.get("desc"), 80), "wants": [_s(w, 30) for w in (x.get("wants") or [])][:4]}
                         for x in lst("fan_types", 6) if isinstance(x, dict) and x.get("name")],
           "categories": [{"name": _s(x.get("name"), 14), "desc": _s(x.get("desc"), 60), "example": _s(x.get("example"), 80),
                           "half_life": x.get("half_life") if x.get("half_life") in HALF_LIFE else ""}
                          for x in lst("categories", 12) if isinstance(x, dict) and x.get("name")],
           "cares": [_s(x, 60) for x in lst("cares")], "dislikes": [_s(x, 60) for x in lst("dislikes", 8)],
           "sensitive": [_s(x, 60) for x in lst("sensitive", 6)],
           "vocabulary": [{"word": _s(x.get("word"), 15), "meaning": _s(x.get("meaning"), 40)}
                          for x in lst("vocabulary", 15) if isinstance(x, dict) and x.get("word")],
           "calendar": [_ok(x, {"when": _s(x.get("when"), 15), "what": _s(x.get("what"), 60)})
                        for x in lst("calendar") if isinstance(x, dict) and x.get("what")],
           "must_know": [_ok(x, {"name": _s(x.get("name"), 40), "why": _s(x.get("why"), 60)})
                         for x in lst("must_know") if isinstance(x, dict) and x.get("name")],
           "personal": {k: _s((raw.get("personal") or {}).get(k), 80) for k in ("favorite", "rival", "memorable", "taste")}
           if isinstance(raw.get("personal"), dict) else {},
           "question_ideas": [_s(x, 60) for x in lst("question_ideas", 8)]}
    if isinstance(raw.get("info"), dict):
        out["info"] = clean_info(raw["info"])
    if raw.get("checked"):  # 앱이 사실 확인(fieldcard)을 마친 날
        out["checked"] = _s(raw["checked"], 10)
    return out


def _ok(raw, item):
    """앱이 사실 확인한 칸이면 ok(확인됨/미확인)를 그대로."""
    return {**item, "ok": raw["ok"]} if isinstance(raw.get("ok"), bool) else item


def _mark(x):
    return " (미확인)" if x.get("ok") is False else ""


def clean_info(i):
    """분야 카드의 정보 지형 칸 (설계 4-9): X 몫·로그인 권유·꼭 필요한 X 밖 출처·페이지 감시·놓치면 안 되는 날짜·시기별 세기."""
    try:
        share = min(max(float(i.get("x_share") or 0), 0.0), 1.0)
    except (TypeError, ValueError):
        share = 0.0
    strs = lambda k, n=10, m=60: [_s(x, m) for x in (i.get(k) or []) if x][:n]
    return {"checked": _s(i.get("checked"), 10), "x_share": share, "x_note": _s(i.get("x_note"), 80),
            "login_nudge": i.get("login_nudge") if i.get("login_nudge") in NUDGE else "weak",
            "nudge_note": _s(i.get("nudge_note"), 100), "must_have": strs("must_have"), "page_watch": strs("page_watch"),
            "deadline_kinds": strs("deadline_kinds", 6, 30),
            "seasonality": [{"when": _s(x.get("when"), 20), "intensity": x.get("intensity") if x.get("intensity") in ("높음", "보통", "낮음") else "보통",
                             "what": _s(x.get("what"), 60)} for x in (i.get("seasonality") or [])[:10] if isinstance(x, dict) and x.get("when")],
            **_scale_info(i)}


def _scale_info(i):
    """발굴 기준 칸 (popme/fieldscale.py): 팔로워 기준 {kind: [통과, 지켜보기]}·배수·표본 수·분야 낱말. 없으면 칸도 없음."""
    out = {}
    f = i.get("followers")
    if isinstance(f, dict):
        out["followers"] = {k: [int(x) for x in v][:2] for k, v in f.items()
                            if k in ("company", "global", "ko", "threads") and isinstance(v, list) and len(v) >= 2}
    if isinstance(i.get("scale"), dict):
        out["scale"] = {k: float(i["scale"][k]) for k in ("world", "kr") if k in i["scale"]}
    if i.get("sample") is not None:
        out["sample"] = int(i["sample"])
    if i.get("topic_words"):
        out["topic_words"] = [_s(w, 20) for w in i["topic_words"] if w][:80]
    return out


def max_age(d, category=""):
    """그 소식 종류가 브리핑 가치를 잃기까지 며칠 (유통기한). 모르면 DEFAULT_AGE."""
    for x in (d or {}).get("categories", []):
        if x["name"] == category and x.get("half_life"):
            return HALF_LIFE[x["half_life"]]
    return DEFAULT_AGE


def brief(d, for_task=""):
    """분야 페르소나 → 지시문에 붙일 글. for_task: "questions" | "editor" | "taste" | "people" — 일에 필요한 칸 위주로."""
    if not d:
        return ""
    p = d.get("persona", {})
    lines = [f"너는 이 분야를 오래 본 사람의 눈으로 일한다: {p.get('name', '')} ({p.get('born', '')}년생) — {p.get('career', '')}",
             f"성격·관점: {p.get('personality', '')}", f"말투: {p.get('voice', '')}"]
    if d.get("fan_types"):
        lines.append("이 분야 사람들 유형: " + " / ".join(f"{x['name']}({x['desc']})" for x in d["fan_types"]))
    if d.get("cares"):
        lines.append("실제로 궁금해하는 것: " + "; ".join(d["cares"]))
    if d.get("dislikes"):
        lines.append("싫어하는 것: " + "; ".join(d["dislikes"]))
    if d.get("sensitive"):
        lines.append("조심할 것: " + "; ".join(d["sensitive"]))
    if d.get("calendar") and for_task in ("questions", "editor", ""):
        lines.append("한 해 흐름: " + "; ".join(f"{x['when']} {x['what']}{_mark(x)}" for x in d["calendar"]))
    if d.get("categories") and for_task in ("editor", "taste", ""):
        lines.append("소식 종류: " + "; ".join(f"{x['name']}({x['desc']})" for x in d["categories"]))
    if d.get("vocabulary") and for_task in ("questions", ""):
        lines.append("이 분야 말: " + ", ".join(f"{x['word']}={x['meaning']}" for x in d["vocabulary"]))
    if d.get("must_know") and for_task in ("people", ""):
        lines.append("꼭 아는 사람·기관: " + ", ".join(f"{x['name']}({x['why']}){_mark(x)}" for x in d["must_know"]))
    if d.get("question_ideas") and for_task == "questions":
        lines.append("이 사람이 할 법한 질문: " + " / ".join(d["question_ideas"]))
    if any("(미확인)" in x for x in lines):
        lines.append("(미확인) = 최근 뉴스로 확인하지 못한 사실. 지금도 맞는지 모르니 이것만 보고 추천하거나 단정하지 않는다.")
    return "\n".join(x for x in lines if not re.fullmatch(r"[^:]+: *", x))


def write(cfg, field, category="", headlines=()):
    """새 분야: 프로필 작가 페르소나로 상세 프로필을 펼친다 (최근 헤드라인을 사실의 근거로). 실패하면 None."""
    from popme import persona
    try:
        raw = persona.ask(cfg, "profile_writer", {"field": field, "category": category,
                                                   "headlines": list(headlines)[:20]}, timeout=180)
    except Exception:
        log.exception("분야 페르소나 만들기 실패: %s", field)
        return None
    return clean(raw)
