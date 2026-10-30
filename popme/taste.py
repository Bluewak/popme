"""관심사 설정 ④ 취향 문답 (설계 4-10 G2): 최근 헤드라인 → [편집자] 서로 다른 종류 10개 → 사용자가 읽고 싶은 것만 탭 → [취향 분석가] 브리핑 기준.

근본은 「그 사람이 고른 것의 최근 동향」을 끌어오는 것 (2026-10-30 시험: 분야 이름 검색만으로는 세부·대상 소식이 0~2개).
- 모으기: ③에서 고른 대상(팀·아티스트·종목) → ①의 세부(묶음에 정해 둔 검색어, 해외면 영어도) → 분야 순으로 따로 찾아
  최근순으로 번갈아 넣는다. 매체 꼬리를 뗀 제목이 비슷하면 같은 사건으로 하나만, 한 매체 몫·광고성 제목은 거른다.
- 편집자: 헤드라인마다 어디서 왔는지(group)를 알고, 고른 세부·대상마다 하나 이상, 이미 보여 준 사건은 다시 고르지 않는다.
  답은 LangGraph로 검사해 문제(없는 번호·같은 번호·한 종류 몰림·고른 세부가 빠짐)를 알려 주고 한 번 더 고르게 한다.
- 취향: 「줄일 종류」는 코드가 근거를 확인한다 (여러 번 안 눌렀고 하나도 안 눌렀고 ③에서 고른 게 아닐 때만).
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

from langgraph.graph import END, StateGraph

from popme import domains, interests, persona

log = logging.getLogger(__name__)
COUNT = 10        # 보여 줄 헤드라인
POOL = 45         # 편집자에게 줄 헤드라인
DAYS = 7          # 최근 며칠
TARGETS, SUBS = 3, 4                                    # 검색할 대상·세부 수
PER_TARGET, PER_SUB, PER_EN, PER_FIELD = 6, 8, 5, 12    # 검색 하나에서 받을 수
PER_OUTLET = 3    # 한 매체 몫
MAX_TRIES = 2
OTHER = "기타"    # 맞는 소식 종류가 없을 때 — 되도록 고르지 않는다
SPAM = re.compile(r"\[(광고|AD|PR|협찬|이벤트)\]|협찬|매수\s*신호|프리미엄\s*콘텐츠|리딩방|급등\s*임박|무료\s*(추천|리딩)|할인\s*쿠폰|최저가", re.I)


def term(x):
    """세부·대상 하나 → {label, news, en, region} (화면이 이름만 넘겨도)."""
    x = x if isinstance(x, dict) else {"label": str(x)}
    label = " ".join(str(x.get("label") or "").split())[:30]
    return {"label": label, "news": str(x.get("news") or "").strip()[:40], "en": str(x.get("en") or "").strip()[:40],
            "region": x.get("region") or ""}


def queries(field, subs=(), targets=(), scope="world"):
    """→ [(group, 검색어, loc)] 대상 → 세부 → 분야. 해외도(world)면 영어 이름이 있는 것은 영어 기사도."""
    out = []
    for kind, xs, n in (("대상", targets, TARGETS), ("세부", subs, SUBS)):
        for x in [term(x) for x in xs if x][:n]:
            if not x["label"]:
                continue
            q = x["news"] or (x["label"] if kind == "대상" or len(x["label"]) > 3 else f"{field} {x['label']}")
            out.append((f"{kind}:{x['label']}", q.replace("·", " ").replace("/", " "), None))
            if scope == "world" and x["en"]:
                out.append((f"{kind}:{x['label']}", x["en"], "world"))
    out.append(("분야", field, None))
    return out


def tidy(title):
    """보여 줄 제목: 끝의 「 - https://…」 출처를 매체 이름으로, 같은 매체가 두 번 붙은 것은 하나로."""
    t = " ".join(str(title).split())[:200]
    t = re.sub(r" - https?://(?:www\.)?([^/\s]+)/?\S*$", lambda m: f" - {m[1]}", t)
    parts = t.split(" - ")
    while len(parts) >= 3 and parts[-1] == parts[-2]:
        parts.pop()
    return " - ".join(parts)


def _tokens(title):
    """같은 사건 비교용: 매체 꼬리·괄호·따옴표를 뗀 낱말들."""
    t = title.rpartition(" - ")[0] or title
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", t)
    return {w for w in re.findall(r"[0-9A-Za-z가-힣]+", t.lower()) if len(w) >= 2}


def same_event(a, b):
    """낱말이 많이 겹치면 같은 사건 (매체만 다른 기사)."""
    if not a or not b:
        return False
    common = len(a & b)
    return common / len(a | b) >= 0.5 or (common >= 3 and common >= 0.6 * min(len(a), len(b)))


def pool(field, subs=(), targets=(), scope="world", extra=(), exclude=()):
    """편집자에게 줄 헤드라인 [{title, group, published}]: 묶음(대상·세부·분야)마다 최근순, 번갈아 넣고 같은 사건·광고·한 매체 몰림을 거른다."""
    qs = queries(field, subs, targets, scope)
    per = lambda g, loc: PER_EN if loc == "world" and g != "분야" else PER_TARGET if g.startswith("대상") else PER_SUB if g.startswith("세부") else PER_FIELD
    with ThreadPoolExecutor(max_workers=8) as ex:
        found = list(ex.map(lambda q: interests.research_items(q[1], per(q[0], q[2]), DAYS, q[2]), qs))
    groups = {}
    for (g, _, _), items in zip(qs, found):
        groups.setdefault(g, []).extend(items)
    groups.setdefault("분야", []).extend({"title": h, "published": "", "outlet": ""} for h in extra)
    for g in groups:
        groups[g].sort(key=lambda i: i.get("published") or "", reverse=True)
    seen = [_tokens(t) for t in exclude]
    outlets, out = {}, []
    queues = [list(v) for v in groups.values()]
    names = list(groups)
    while any(queues) and len(out) < POOL:
        for g, q in zip(names, queues):  # 번갈아: 대상·세부가 분야 검색에 밀리지 않게
            while q:
                it = q.pop(0)
                title = tidy(it["title"])
                toks = _tokens(title)
                if not title or SPAM.search(title) or any(same_event(toks, s) for s in seen):
                    continue
                if it.get("outlet") and outlets.get(it["outlet"], 0) >= PER_OUTLET:
                    continue
                seen.append(toks)
                outlets[it.get("outlet")] = outlets.get(it.get("outlet"), 0) + 1
                out.append({"title": title, "group": g, "published": it.get("published", "")})
                break
    return out[:POOL]


def problems(picks, heads, categories, count):
    """편집자 답의 문제 (없으면 []): 없는 번호·같은 번호·모자람·한 종류에 몰림·고른 세부/대상이 빠짐·기타가 많음."""
    ids = [p["id"] for p in picks]
    out = []
    if any(i < 0 or i >= len(heads) for i in ids):
        out.append("받은 목록에 없는 번호가 있다")
    if len(set(ids)) < len(ids):
        out.append("같은 헤드라인을 두 번 골랐다")
    want = min(count, len(heads))
    if len(set(ids)) < want - 2:
        out.append(f"{want}개를 골라야 하는데 {len(set(ids))}개뿐이다")
    cats = [p["category"] for p in picks if p["category"] and p["category"] != OTHER]
    limit = 1 if len(categories) >= count else 2  # 종류가 넉넉하면 종류마다 하나
    crowded = sorted({c for c in cats if cats.count(c) > limit})
    if crowded:
        out.append(f"한 종류에 몰렸다: {', '.join(crowded)} — 아직 안 고른 종류에서 골라라")
    if sum(1 for p in picks if p["category"] == OTHER) > 2:
        out.append(f"「{OTHER}」를 너무 많이 골랐다 — 분야의 소식 종류에 맞는 것으로")
    picked_groups = {heads[i]["group"] for i in ids if 0 <= i < len(heads)}
    missing = [g.split(":", 1)[1] for g in dict.fromkeys(h["group"] for h in heads) if g != "분야" and g not in picked_groups]
    if missing:
        out.append(f"사용자가 고른 {', '.join(missing)} 소식이 목록에 있는데 하나도 안 골랐다 — 하나씩은 넣어라")
    return out


class Pick(TypedDict, total=False):
    heads: list
    picks: list
    feedback: str
    tries: int


def _graph(cfg, field, domain, scope, count, exclude):
    categories = [c["name"] for c in (domain or {}).get("categories", [])]
    eyes = domains.brief(domain, "editor") if domain else ""

    def choose(s):
        heads = s["heads"]
        out = persona.ask(cfg, "editor", {
            "field": field, "scope": scope, "count": count, "categories": categories,
            "chosen": [g.split(":", 1)[1] for g in dict.fromkeys(h["group"] for h in heads) if g != "분야"],
            "shown_before": list(exclude)[:30], "feedback": s.get("feedback", ""),
            "headlines": [{"id": i, "title": h["title"], "group": h["group"]} for i, h in enumerate(heads)]},
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


def headlines(cfg, field, domain=None, scope="world", subs=(), targets=(), extra=(), exclude=(), count=COUNT):
    """→ [{title, category, group}] 서로 다른 종류의 헤드라인 count개.
    편집자가 실패하면 모은 순서(대상·세부·분야를 번갈아, 같은 사건·광고는 이미 거름)대로."""
    heads = pool(field, subs, targets, scope, extra, exclude)
    if not heads:
        return []
    try:
        out = _graph(cfg, field, domain, scope, count, exclude).invoke({"heads": heads, "tries": 0})
    except Exception:
        log.exception("편집자 실패: %s (모은 순서대로 보여 줌)", field)
        return [{"title": h["title"], "category": "", "group": h["group"]} for h in heads[:count]]
    seen, picked = set(), []
    for p in out["picks"]:  # 다시 골라도 남은 문제(없는 번호·같은 번호)는 여기서 거른다
        if 0 <= p["id"] < len(heads) and p["id"] not in seen:
            seen.add(p["id"])
            picked.append({"title": heads[p["id"]]["title"], "category": "" if p["category"] == OTHER else p["category"],
                           "group": heads[p["id"]]["group"]})
    return picked[:count]


ALL_LIKED = 0.8   # 이만큼 넘게 누르면 「전반을 고르게」
LESS_MIN_LIKED = 3  # 이만큼은 눌러야 「줄일 종류」를 판단한다
LESS_MIN_SKIPS = 2  # 같은 종류를 이만큼 안 눌러야
REDUCE = re.compile(r"큰 (것|소식)만|줄이|덜 |빼고|제외")


def analyze(cfg, field, domain, shown, liked, answers=()):
    """보여 준 것(shown) 중 누른 제목(liked) → {likes, less, summary}. answers: ③에서 고른 것 (줄이지 않는다).
    하나도 안 눌렀거나 거의 다 눌렀으면 AI 없이 「전반을 고르게」."""
    liked = set(liked)
    uniq = list({h["title"]: h for h in shown}.values())
    cats_of = lambda yes: [h.get("category") for h in uniq if (h["title"] in liked) == yes and h.get("category")]
    if not liked:
        return {"likes": [], "less": [], "summary": "전반을 고르게"}
    if len(uniq) >= 5 and len(liked) >= ALL_LIKED * len(uniq):
        return {"likes": list(dict.fromkeys(cats_of(True)))[:6], "less": [], "summary": "전반을 고르게"}
    rows = lambda yes: [{"title": h["title"], "category": h.get("category", "")} for h in uniq if (h["title"] in liked) == yes]
    try:
        out = persona.ask(cfg, "taste_analyst", {"field": field, "chosen": list(answers), "liked": rows(True), "skipped": rows(False)},
                          timeout=120, eyes=domains.brief(domain, "taste") if domain else "")
    except Exception:
        log.exception("취향 분석 실패: %s", field)
        return {"likes": list(dict.fromkeys(cats_of(True)))[:6], "less": [], "summary": ""}
    yes, no = cats_of(True), cats_of(False)
    keep = lambda c: (len(liked) >= LESS_MIN_LIKED and no.count(c) >= LESS_MIN_SKIPS and c not in yes
                      and not any(a and (a in c or c in a) for a in answers))
    out["less"] = [c for c in out["less"] if keep(c)]  # 근거가 모자란 「줄이기」는 코드가 지운다
    if not out["less"]:  # 줄일 게 없는데 요약이 다른 걸 줄이라고 하면 그 말은 뺀다
        parts = [p for p in re.split(r",\s*|\s*—\s*", out["summary"]) if not REDUCE.search(p)]
        out["summary"] = ", ".join(parts).strip(" ,") or "전반을 고르게"
    return out


def line(t):
    """브리핑 지시문 한 줄 (popme/briefing.py rules_for). 없으면 ""."""
    if not t or not (t.get("summary") or t.get("likes")):
        return ""
    parts = [f"취향: {t['summary'].strip().rstrip('.')}." if t.get("summary") else "취향:"]
    if t.get("likes"):
        parts.append(f"먼저 챙길 종류: {', '.join(t['likes'])}.")
    if t.get("less"):
        parts.append(f"줄일 종류(큰 소식만): {', '.join(t['less'])}.")
    return "\n- " + " ".join(parts)
