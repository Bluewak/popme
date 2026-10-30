"""관심사 설정 ④ 취향 문답 (설계 4-10 G2): 최근 헤드라인 → [편집자] 서로 다른 종류 10개 → 사용자가 읽고 싶은 것만 탭 → [취향 분석가] 브리핑 기준.

편집자 답은 LangGraph로 검사한다: 없는 번호·같은 번호·한 종류에 몰림이면 무엇이 문제인지 알려 주고 한 번 더 고르게 한다.
분야 페르소나(popme/domains.py)가 있으면 그 사람의 눈으로 (소식 종류·싫어하는 것·조심할 것).
"""
import logging
from typing import TypedDict

from langgraph.graph import END, StateGraph

from popme import domains, interests, persona

log = logging.getLogger(__name__)
COUNT = 10        # 보여 줄 헤드라인
POOL = 40         # 편집자에게 줄 헤드라인
PER_SUB = 8       # 세부마다 더 모을 헤드라인
MAX_TRIES = 2
SAME_CAT = 2      # 한 종류에서 최대 몇 개까지


def pool(field, subs=(), extra=(), exclude=()):
    """편집자에게 줄 헤드라인: 분야 + 세부마다 최근 뉴스 (+ 이미 받아 둔 것). 같은 제목·이미 보여 준 것은 뺀다."""
    heads = list(extra) + interests.research(field)
    for s in list(subs)[:4]:
        heads += interests.research(f"{field} {s}", limit=PER_SUB)
    seen, out = set(exclude), []
    for h in heads:
        h = " ".join(str(h).split())[:200]
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out[:POOL]


def problems(picks, heads, categories, count):
    """편집자 답의 문제 (없으면 []): 없는 번호·같은 번호·모자람·한 종류에 몰림."""
    ids = [p["id"] for p in picks]
    out = []
    if any(i < 0 or i >= len(heads) for i in ids):
        out.append("받은 목록에 없는 번호가 있다")
    if len(set(ids)) < len(ids):
        out.append("같은 헤드라인을 두 번 골랐다")
    if len(set(ids)) < min(count, len(heads)) - 2:
        out.append(f"{min(count, len(heads))}개를 골라야 하는데 {len(set(ids))}개뿐이다")
    cats = [p["category"] for p in picks if p["category"]]
    crowded = sorted({c for c in cats if cats.count(c) > SAME_CAT})
    if crowded and len(categories) > len(picks) // SAME_CAT:
        out.append(f"한 종류에 몰렸다: {', '.join(crowded)} — 다른 종류에서 골라라")
    return out


class Pick(TypedDict, total=False):
    heads: list
    picks: list
    feedback: str
    tries: int


def _graph(cfg, field, domain, scope, count):
    categories = [c["name"] for c in (domain or {}).get("categories", [])]
    eyes = domains.brief(domain, "editor") if domain else ""

    def choose(s):
        out = persona.ask(cfg, "editor", {"field": field, "scope": scope, "count": count, "categories": categories,
                                          "feedback": s.get("feedback", ""),
                                          "headlines": [{"id": i, "title": h} for i, h in enumerate(s["heads"])]},
                          timeout=180, eyes=eyes)
        return {"picks": out["picks"], "tries": s.get("tries", 0) + 1}

    def check(s):
        return {"feedback": "; ".join(problems(s["picks"], s["heads"], categories, count))}

    def again(s):
        return "choose" if s["feedback"] and s["tries"] < MAX_TRIES else END

    g = StateGraph(Pick)
    g.add_node("choose", choose)
    g.add_node("check", check)
    g.set_entry_point("choose")
    g.add_edge("choose", "check")
    g.add_conditional_edges("check", again, {"choose": "choose", END: END})
    return g.compile()


def headlines(cfg, field, domain=None, scope="world", subs=(), extra=(), exclude=(), count=COUNT):
    """→ [{title, category}] 서로 다른 종류의 헤드라인 count개. 편집자가 실패하면 앞에서부터 (종류 없이)."""
    heads = pool(field, subs, extra, exclude)
    if not heads:
        return []
    try:
        out = _graph(cfg, field, domain, scope, count).invoke({"heads": heads, "tries": 0})
    except Exception:
        log.exception("편집자 실패: %s (앞에서부터 보여 줌)", field)
        return [{"title": h, "category": ""} for h in heads[:count]]
    seen, picked = set(), []
    for p in out["picks"]:  # 다시 골라도 남은 문제(없는 번호·같은 번호)는 여기서 거른다
        if 0 <= p["id"] < len(heads) and p["id"] not in seen:
            seen.add(p["id"])
            picked.append({"title": heads[p["id"]], "category": p["category"]})
    return picked[:count]


def analyze(cfg, field, domain, shown, liked):
    """보여 준 것(shown) 중 누른 제목(liked) → {likes, less, summary}. 하나도 안 눌렀으면 AI 없이 「전반을 고르게」."""
    liked = set(liked)
    if not liked:
        return {"likes": [], "less": [], "summary": "전반을 고르게"}
    rows = lambda yes: [{"title": h["title"], "category": h.get("category", "")} for h in shown if (h["title"] in liked) == yes]
    try:
        return persona.ask(cfg, "taste_analyst", {"field": field, "liked": rows(True), "skipped": rows(False)},
                           timeout=120, eyes=domains.brief(domain, "taste") if domain else "")
    except Exception:
        log.exception("취향 분석 실패: %s", field)
        cats = [h.get("category") for h in shown if h["title"] in liked and h.get("category")]
        return {"likes": list(dict.fromkeys(cats)), "less": [], "summary": ""}


def line(t):
    """브리핑 지시문 한 줄 (popme/briefing.py rules_for). 없으면 ""."""
    if not t or not (t.get("summary") or t.get("likes")):
        return ""
    more = f" 먼저 챙길 종류: {'·'.join(t['likes'])}." if t.get("likes") else ""
    less = f" 줄일 종류: {'·'.join(t['less'])} (큰 소식만)." if t.get("less") else ""
    return f"\n- 취향: {t.get('summary', '')}{more}{less}"
