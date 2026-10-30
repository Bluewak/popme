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
    return " ".join(str(v or "").split())[:n]


def clean(raw, pid=""):
    """프로필 작가가 준 것 → 정해진 모양 (칸이 빠지면 빈 값, 길이 제한). 판단에 쓰이니 지나치게 긴 것은 자른다."""
    p = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    lst = lambda k, n=12: [x for x in (raw.get(k) or []) if x][:n]
    out = {"id": pid or raw.get("id", ""),
           "persona": {k: _s(p.get(k), 200) for k in ("name", "born", "home", "career", "entry_story", "personality", "voice")},
           "fan_types": [{"name": _s(x.get("name"), 20), "desc": _s(x.get("desc"), 80), "wants": [_s(w, 30) for w in (x.get("wants") or [])][:4]}
                         for x in lst("fan_types", 6) if isinstance(x, dict) and x.get("name")],
           "categories": [{"name": _s(x.get("name"), 12), "desc": _s(x.get("desc"), 60), "example": _s(x.get("example"), 80)}
                          for x in lst("categories", 12) if isinstance(x, dict) and x.get("name")],
           "cares": [_s(x, 60) for x in lst("cares")], "dislikes": [_s(x, 60) for x in lst("dislikes", 8)],
           "sensitive": [_s(x, 60) for x in lst("sensitive", 6)],
           "vocabulary": [{"word": _s(x.get("word"), 15), "meaning": _s(x.get("meaning"), 40)}
                          for x in lst("vocabulary", 15) if isinstance(x, dict) and x.get("word")],
           "calendar": [{"when": _s(x.get("when"), 15), "what": _s(x.get("what"), 40)}
                        for x in lst("calendar") if isinstance(x, dict) and x.get("what")],
           "must_know": [{"name": _s(x.get("name"), 30), "why": _s(x.get("why"), 40)}
                         for x in lst("must_know") if isinstance(x, dict) and x.get("name")],
           "personal": {k: _s((raw.get("personal") or {}).get(k), 80) for k in ("favorite", "rival", "memorable", "taste")}
           if isinstance(raw.get("personal"), dict) else {},
           "question_ideas": [_s(x, 60) for x in lst("question_ideas", 8)]}
    return out


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
        lines.append("한 해 흐름: " + "; ".join(f"{x['when']} {x['what']}" for x in d["calendar"]))
    if d.get("categories") and for_task in ("editor", "taste", ""):
        lines.append("소식 종류: " + "; ".join(f"{x['name']}({x['desc']})" for x in d["categories"]))
    if d.get("vocabulary") and for_task in ("questions", ""):
        lines.append("이 분야 말: " + ", ".join(f"{x['word']}={x['meaning']}" for x in d["vocabulary"]))
    if d.get("must_know") and for_task in ("people", ""):
        lines.append("꼭 아는 사람·기관: " + ", ".join(f"{x['name']}({x['why']})" for x in d["must_know"]))
    if d.get("question_ideas") and for_task == "questions":
        lines.append("이 사람이 할 법한 질문: " + " / ".join(d["question_ideas"]))
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
