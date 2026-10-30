"""관심사 설정 ④ 취향 문답 (설계 4-10 G2): 최근 헤드라인 → [편집자] 서로 다른 종류 10개 → 사용자가 읽고 싶은 것만 탭 → [취향 분석가] 브리핑 기준.

근본은 「그 사람이 고른 것의 최근 동향」을 끌어오는 것 (2026-10-30 시험: 분야 이름 검색만으로는 세부·대상 소식이 0~2개).
- 모으기: ③에서 고른 대상(팀·아티스트·종목) → ①의 세부 → 분야 순으로 따로 찾아 최근순으로 번갈아 넣는다.
  검색어엔 늘 분야를 붙인다(「할인·특가 행사」만 찾으면 쿠팡, 「페라리」만 찾으면 양산차). 묶음은 세부마다 정해 둔 검색어·영어 이름.
  매체 꼬리·조사를 떼고 비교해 같은 사건은 하나만, 광고·시세 자동기사·주가 전망·투자 권유 매체는 거른다.
- 편집자: 헤드라인마다 어디서 왔는지(group)·며칠 전인지를 알고, 고른 것 먼저, 이미 보여 준 사건은 다시 고르지 않는다.
  답은 LangGraph로 검사해 문제(없는 번호·같은 번호·분야 기사가 한 종류에 몰림)를 알려 주고 한 번 더 — 둘 중 나은 답을 쓰고,
  모자라면 남은 풀에서 고른 것부터 채운다 (2차 시험: 검사끼리 부딪혀 5~9개만 나오고 늘 두 번 돌았다).
- 취향: 「줄일 종류」는 코드가 근거를 확인한다 (같은 사건은 한 번으로 세어, 여러 번 안 눌렀고 누른 적 없고 ③에서 고른 게 아닐 때만).
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, StateGraph

from popme import domains, interests, persona

log = logging.getLogger(__name__)
COUNT = 10        # 보여 줄 헤드라인
POOL = 50         # 편집자에게 줄 헤드라인
DAYS = 7          # 최근 며칠
TARGETS, SUBS = 3, 4                                     # 검색할 대상·세부 수
PER_TARGET, PER_SUB, PER_EN, PER_FIELD = 8, 10, 6, 15    # 검색 하나에서 받을 수
PER_OUTLET = 3    # 한 매체 몫
MAX_TRIES = 2
OTHER = "기타"    # 맞는 소식 종류가 없을 때 — 되도록 고르지 않는다
# 광고·홍보·시세 자동기사·주가 전망·투자 권유 (2차 시험에서 대상 검색 「○○ 주가」가 끌어온 것들)
SPAM = re.compile(
    r"\[(광고|AD|PR|협찬|이벤트|설명|해명)\]|협찬|매수\s*신호|프리미엄\s*콘텐츠|리딩방|급등\s*임박|무료\s*(추천|리딩)|할인\s*쿠폰|최저가"
    r"|주가\s*전망|목표\s*주가|저가\s*매수|초고수|늘려(라|야)|\d[\d,]*원으로\s*\d*%?\S*\s*(상승|하락)|(오름|내림)세\b|애프터마켓\s*\d"
    r"|생중계.*(무료|채널)|기념\s.*이벤트|\bto buy\b|\boutperform|stock quote|press release|price prediction", re.I)
SKIP_OUTLETS = re.compile(r"mitrade|tradingkey|motley fool|fool\.com|investing\.com|vantage data|stocktwits|naver blog|브런치|티스토리", re.I)
STEM = re.compile(r"(으로|에서|에게|까지|부터|이며|이다|했다|한다|하고|하는|하며|된다|됐다|에는|에도|은|는|이|가|을|를|에|의|로|와|과|도|만)$")


def term(x):
    """세부·대상 하나 → {label, news, en, region} (화면이 이름만 넘겨도)."""
    x = x if isinstance(x, dict) else {"label": str(x)}
    label = " ".join(str(x.get("label") or "").split())[:30]
    return {"label": label, "news": str(x.get("news") or "").strip()[:40], "en": str(x.get("en") or "").strip()[:60],
            "region": x.get("region") or ""}


def _core(label):
    """「SF 자이언츠 (이정후)」 → 「SF 자이언츠 이정후」, 「건강·질병」 → 「건강 질병」."""
    return " ".join(re.sub(r"[()·/]", " ", label).split())


def queries(field, subs=(), targets=(), scope="world"):
    """→ [(group, 검색어, loc)] 대상 → 세부 → 분야.
    - 묶음이 정해 둔 검색어(news)가 있으면 그대로 (「○○ 주가」·「○○ 집값」의 꼬리는 뗀다 — 시세 자동기사만 온다).
    - 없으면(새 분야 보기·직접 적은 것) 분야를 붙인다. 영어 기사는 대상(사람·팀) 또는 묶음이 정해 둔 세부만 (일반 문구 영어는 엉뚱)."""
    out = []
    for kind, xs, n in (("대상", targets, TARGETS), ("세부", subs, SUBS)):
        for x in [term(x) for x in xs if x][:n]:
            if not x["label"]:
                continue
            curated = bool(x["news"])
            q = re.sub(r"\s*(주가|집값)$", "", x["news"]) if curated else f"{_core(x['label'])} {field}"
            out.append((f"{kind}:{x['label']}", _core(q), None))
            if scope == "world" and x["en"] and (kind == "대상" or curated):
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
    """같은 사건 비교용: (낱말들, 붙여 쓴 글자 두 개씩). 매체 꼬리·괄호를 떼고, 낱말 끝 조사·어미를 뗀다 (「수훈에」 → 「수훈」)."""
    t = title.rpartition(" - ")[0] or title
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|[‘’“”'\"…·]", " ", t).lower()
    words = set()
    for w in re.findall(r"[0-9a-z가-힣]+", t):
        w = STEM.sub("", w) if len(w) >= 3 and re.search(r"[가-힣]$", w) else w
        if len(w) >= 2:
            words.add(w)
    flat = re.sub(r"[^0-9a-z가-힣]", "", t)
    return words, {flat[i:i + 2] for i in range(len(flat) - 1)}


def same_event(a, b):
    """같은 사건(매체·표현만 다른 기사)인가: 낱말이 많이 겹치거나, 띄어쓰기를 무시한 글자 쌍이 많이 겹치면."""
    (wa, ba), (wb, bb) = a, b
    if not wa or not wb:
        return False
    common = len(wa & wb)
    if common / len(wa | wb) >= 0.5 or (common >= 3 and common >= 0.5 * min(len(wa), len(wb))):
        return True
    return bool(ba and bb) and len(ba & bb) / len(ba | bb) >= 0.45


def _on_target(item, group):
    """대상 검색 결과가 정말 그 대상 이야기인가 — 제목에 이름(의 한 낱말)이 있어야 (「마포구」로 찾은 「서울 아파트 매물」은 분야로)."""
    names = [w for w in _core(group.split(":", 1)[1]).split() if len(w) >= 2]
    return not names or any(n.lower() in item["title"].lower() or n.rstrip("구시군").lower() in item["title"].lower() for n in names)


def pool(field, subs=(), targets=(), scope="world", extra=(), exclude=()):
    """편집자에게 줄 헤드라인 [{title, group, published}]: 묶음(대상·세부·분야)마다 최근순, 번갈아 넣고 같은 사건·광고·한 매체 몰림을 거른다."""
    qs = queries(field, subs, targets, scope)
    per = lambda g, loc: PER_EN if loc == "world" and g != "분야" else PER_TARGET if g.startswith("대상") else PER_SUB if g.startswith("세부") else PER_FIELD
    with ThreadPoolExecutor(max_workers=8) as ex:
        found = list(ex.map(lambda q: interests.research_items(q[1], per(q[0], q[2]), DAYS, q[2]), qs))
    groups = {}
    for (g, _, _), items in zip(qs, found):
        for it in items:
            groups.setdefault(g if not g.startswith("대상") or _on_target(it, g) else "분야", []).append(it)
    groups.setdefault("분야", []).extend({"title": h, "published": "", "outlet": ""} for h in extra)
    for g in groups:
        groups[g].sort(key=lambda i: i.get("published") or "", reverse=True)
    seen = [_tokens(t) for t in exclude]
    outlets, out = {}, []
    names = [g for g in groups if g != "분야"] + ["분야"]  # 고른 것 먼저
    queues = [list(groups[g]) for g in names]
    while any(queues) and len(out) < POOL:
        for g, q in zip(names, queues):  # 번갈아: 대상·세부가 분야 검색에 밀리지 않게
            while q:
                it = q.pop(0)
                title, outlet = tidy(it["title"]), it.get("outlet") or ""
                toks = _tokens(title)
                if not title or SPAM.search(title) or SKIP_OUTLETS.search(outlet) or any(same_event(toks, s) for s in seen):
                    continue
                if outlet and outlets.get(outlet, 0) >= PER_OUTLET:
                    continue
                seen.append(toks)
                outlets[outlet] = outlets.get(outlet, 0) + 1
                out.append({"title": title, "group": g, "published": it.get("published", "")})
                break
    return out[:POOL]


def ago(published, now=None):
    """「오늘」·「1일 전」 … (편집자가 최근 것을 고르게). 모르면 ""."""
    try:
        t = datetime.fromisoformat(published)
    except (TypeError, ValueError):
        return ""
    days = ((now or datetime.now(timezone.utc)) - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).days
    return "오늘" if days <= 0 else f"{days}일 전"


def problems(picks, heads, categories, count):
    """편집자 답의 문제 (없으면 []): 없는 번호·같은 번호·분야 기사가 한 종류에 몰림·기타가 많음.
    고른 세부·대상의 기사는 몰림으로 치지 않는다 (사용자가 고른 것이 둘 나오는 건 괜찮다). 모자람은 코드가 채운다."""
    ids = [p["id"] for p in picks]
    out = []
    if any(i < 0 or i >= len(heads) for i in ids):
        out.append("받은 목록에 없는 번호가 있다")
    if len(set(ids)) < len(ids):
        out.append("같은 헤드라인을 두 번 골랐다")
    general = [p["category"] for p in picks if 0 <= p["id"] < len(heads) and heads[p["id"]]["group"] == "분야"
               and p["category"] and p["category"] != OTHER]
    limit = 1 if len(categories) >= count else 2
    crowded = sorted({c for c in general if general.count(c) > limit})
    if crowded:
        out.append(f"분야 기사가 한 종류에 몰렸다: {', '.join(crowded)} — 아직 안 고른 종류에서 골라라")
    if sum(1 for p in picks if p["category"] == OTHER) > 2:
        out.append(f"「{OTHER}」를 너무 많이 골랐다 — 분야의 소식 종류에 맞는 것으로")
    return out


class Pick(TypedDict, total=False):
    heads: list
    picks: list
    feedback: str
    tries: int
    best: list
    best_n: int
    best_usable: int


def _graph(cfg, field, domain, scope, count, exclude):
    categories = [c["name"] for c in (domain or {}).get("categories", [])]
    eyes = domains.brief(domain, "editor") if domain else ""

    def choose(s):
        heads = s["heads"]
        out = persona.ask(cfg, "editor", {
            "field": field, "scope": scope, "count": count, "categories": categories,
            "chosen": [g.split(":", 1)[1] for g in dict.fromkeys(h["group"] for h in heads) if g != "분야"],
            "shown_before": list(exclude)[:30], "feedback": s.get("feedback", ""),
            "headlines": [{"id": i, "title": h["title"], "group": h["group"], "ago": ago(h.get("published"))} for i, h in enumerate(heads)]},
            timeout=180, eyes=eyes)
        return {"picks": out["picks"], "tries": s.get("tries", 0) + 1}

    def check(s):
        found = problems(s["picks"], s["heads"], categories, count)
        # 나은 답 = 품질 문제(몰림·기타)가 적고, 같으면 쓸 수 있는 것이 많은 답 (없는 번호·같은 번호는 코드가 어차피 거른다)
        bad = sum(1 for x in found if "몰렸다" in x or OTHER in x)
        usable = len({p["id"] for p in s["picks"] if 0 <= p["id"] < len(s["heads"])})
        better = s.get("best") is None or (bad, -usable) < (s.get("best_n", 99), -s.get("best_usable", 0))
        return {"feedback": "; ".join(found), **({"best": s["picks"], "best_n": bad, "best_usable": usable} if better else {})}

    def again(s):
        return "choose" if s["feedback"] and s["tries"] < MAX_TRIES else END

    g = StateGraph(Pick)
    g.add_node("choose", choose)
    g.add_node("check", check)
    g.set_entry_point("choose")
    g.add_edge("choose", "check")
    g.add_conditional_edges("check", again, {"choose": "choose", END: END})
    return g.compile()


def fill(picked, heads, count):
    """편집자가 적게 골랐으면 남은 풀에서 채운다 — 고른 세부·대상 기사 먼저, 이미 고른 것과 같은 사건은 빼고 (꼬리표 없이)."""
    if len(picked) >= count:
        return picked
    have = [_tokens(p["title"]) for p in picked]
    rest = sorted((h for h in heads if h["title"] not in {p["title"] for p in picked}), key=lambda h: h["group"] == "분야")
    for h in rest:
        if len(picked) >= count:
            break
        toks = _tokens(h["title"])
        if not any(same_event(toks, t) for t in have):
            have.append(toks)
            picked.append({"title": h["title"], "category": "", "group": h["group"]})
    return picked


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
    for p in out.get("best") or out["picks"]:  # 둘 중 나은 답. 남은 문제(없는 번호·같은 번호)는 여기서 거른다
        if 0 <= p["id"] < len(heads) and p["id"] not in seen:
            seen.add(p["id"])
            picked.append({"title": heads[p["id"]]["title"], "category": "" if p["category"] == OTHER else p["category"],
                           "group": heads[p["id"]]["group"]})
    return fill(picked[:count], heads, count)


ALL_LIKED = 0.8   # 이만큼 넘게 누르면 「전반을 고르게」
LESS_MIN_LIKED = 3  # 이만큼은 눌러야 「줄일 종류」를 판단한다
LESS_MIN_SKIPS = 2  # 같은 종류의 다른 사건을 이만큼 안 눌러야
REDUCE = re.compile(r"큰 (것|소식)만|줄[이여일인]|덜 |빼고|제외|적게")
GENERAL = "전반을 고르게"


def _events(rows):
    """같은 사건은 하나로 (같은 사건을 두 번 안 누른 것을 「두 번 안 눌렀다」로 세지 않게)."""
    out, toks = [], []
    for r in rows:
        t = _tokens(r["title"])
        if not any(same_event(t, x) for x in toks):
            toks.append(t)
            out.append(r)
    return out


def analyze(cfg, field, domain, shown, liked, answers=()):
    """보여 준 것(shown) 중 누른 제목(liked) → {likes, less, summary}. answers: ③까지 고른 것 (줄이지 않는다).
    하나도 안 눌렀거나 거의 다 눌렀으면 AI 없이 「전반을 고르게」."""
    liked = set(liked)
    uniq = list({h["title"]: h for h in shown}.values())
    if not liked:
        return {"likes": [], "less": [], "summary": GENERAL}
    if len(uniq) >= 5 and len(liked) >= ALL_LIKED * len(uniq):
        return {"likes": [], "less": [], "summary": GENERAL}  # 다 좋다 = 고르게 (먼저 챙길 종류를 몇 개로 자르면 서로 어긋난다)
    rows = lambda yes: [{"title": h["title"], "category": h.get("category", "")} for h in uniq if (h["title"] in liked) == yes]
    try:
        out = persona.ask(cfg, "taste_analyst", {"field": field, "chosen": list(answers), "liked": rows(True), "skipped": rows(False)},
                          timeout=120, eyes=domains.brief(domain, "taste") if domain else "")
    except Exception:
        log.exception("취향 분석 실패: %s", field)
        return {"likes": list(dict.fromkeys(r["category"] for r in rows(True) if r["category"]))[:6], "less": [], "summary": ""}
    yes = [r["category"] for r in rows(True) if r["category"]]
    no = [r["category"] for r in _events(rows(False)) if r["category"]]
    keep = lambda c: (len(liked) >= LESS_MIN_LIKED and no.count(c) >= LESS_MIN_SKIPS and c not in yes
                      and not any(a and (a in c or c in a) for a in answers))
    out["less"] = [c for c in out["less"] if keep(c)]  # 근거가 모자란 「줄이기」는 코드가 지운다
    if not out["less"]:  # 줄일 게 없는데 요약이 다른 걸 줄이라고 하면 그 말은 뺀다
        parts = [p for p in re.split(r",\s*|\s*—\s*", out["summary"]) if not REDUCE.search(p)]
        out["summary"] = ", ".join(parts).strip(" ,") or GENERAL
    return out


def line(t):
    """브리핑 지시문 한 줄 (popme/briefing.py rules_for). 없으면 ""."""
    if not t or not (t.get("summary") or t.get("likes")):
        return ""
    parts = [f"취향: {t['summary'].strip().rstrip('.')}." if t.get("summary") else "취향:"]
    if t.get("likes"):
        parts.append(f"우선 종류: {', '.join(t['likes'])}.")
    if t.get("less"):
        parts.append(f"줄일 종류(큰 소식만): {', '.join(t['less'])}.")
    return "\n- " + " ".join(parts)
