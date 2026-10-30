"""G1 분야 카드 확인 (LangGraph, 설계 4-10): 새 분야의 분야 페르소나 초안 → 사실마다 최근 뉴스 찾기 → 사실 확인 페르소나 → 고친 카드.

묶음 분야는 개발자가 웹 검색으로 확인해 배포한다. 새 분야는 앱이 같은 순서(자료 → 초안 → 확인 → 정리)를 밟되,
웹을 AI가 직접 다니지 않고 앱이 구글 뉴스 RSS로 모은 헤드라인만 근거로 준다 (웹 글이 AI에 지시로 닿지 않게).
- 헤드라인과 어긋나는 사실(은퇴·폐지·일정 바뀜)은 뺀다. 근거를 못 찾은 사실은 남기되 ok=false (「미확인」으로 보인다).
- 설정 중에는 기다리지 않게: 질문을 만들 때 초안으로 뒤에서 시작하고(start), ③ 대상 추천에서 받아 쓴다(verified).
"""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import TypedDict

from langgraph.graph import END, StateGraph

from popme import domains, persona

log = logging.getLogger(__name__)
MAX_CLAIMS = 10   # 꼭 아는 사람·기관, 한 해 흐름 각각
EVIDENCE = 5      # 사실 하나에 헤드라인 몇 개
DAYS = 30         # 사람·기관은 1주엔 기사가 없을 수 있어 한 달
YEAR = 365        # 한 해 흐름은 1년에 한 번 있는 일이라 1년
WAIT = 120        # ③ 대상 추천이 초안·확인을 기다리는 최대 초
BACKGROUND = True  # 테스트는 끈다 (conftest)

_jobs = {}
_lock = threading.Lock()


class Card(TypedDict, total=False):
    field: str
    category: str
    heads: list
    domain: dict
    claims: list
    evidence: dict
    verdicts: dict


def claims_of(field, d):
    """확인할 사실: 꼭 아는 사람·기관(이름으로 검색), 한 해 흐름(분야 + 일로 검색)."""
    # 사람·기관은 「이름 + 분야」로 먼저 (「소방청」만 찾으면 예산·국감 기사, 「소방청 캠핑」이면 캠핑 안전수칙), 없으면 이름만
    out = [{"id": f"m{i}", "text": f"{x['name']} — {x['why']}", "query": f"{key_words(x['name']) or x['name']} {field}",
            "alt": key_words(x["name"]) or x["name"], "days": DAYS}
           for i, x in enumerate(d.get("must_know", [])[:MAX_CLAIMS])]
    out += [{"id": f"c{i}", "text": f"{x['when']} {x['what']}", "query": f"{field} {key_words(x['what'])}", "days": YEAR}
            for i, x in enumerate(d.get("calendar", [])[:MAX_CLAIMS])]
    return out


def key_words(what):
    """「PGA 챔피언십·US오픈·디오픈 등 남자 메이저」 → 「PGA 챔피언십」: 괄호를 빼고 첫 덩어리의 앞 세 낱말 (긴 문장을 통째로 검색하면 0건)."""
    first = re.split(r"[·,/]| 등 |와 |과 ", re.sub(r"\([^)]*\)", "", what))[0]
    return " ".join(first.split()[:3])


def apply(d, verdicts):
    """판정을 카드에: 어긋나면 빼고, 확인되면 ok=true, 모르면 ok=false. 확인한 날을 남긴다."""
    d = {**d}
    for key, pre in (("must_know", "m"), ("calendar", "c")):
        kept = []
        for i, x in enumerate(d.get(key, [])):
            v = verdicts.get(f"{pre}{i}", {}).get("status", "unknown") if i < MAX_CLAIMS else "unknown"
            if v != "contradicted":
                kept.append({**x, "ok": v == "supported"})
        d[key] = kept
    d["checked"] = date.today().isoformat()
    return d


def _graph(cfg):
    from popme import interests

    def draft(s):  # 초안이 없으면 프로필 작가부터
        return {"domain": s.get("domain") or domains.write(cfg, s["field"], s.get("category", ""), s.get("heads") or [])}

    def gather(s):
        claims = claims_of(s["field"], s["domain"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = list(pool.map(lambda c: interests.research(c["query"], limit=EVIDENCE, days=c["days"])
                                  or (interests.research(c["alt"], limit=EVIDENCE, days=c["days"]) if c.get("alt") else []), claims))
        return {"claims": claims, "evidence": {c["id"]: f for c, f in zip(claims, found)}}

    def check(s):
        if not s["claims"]:
            return {"verdicts": {}}
        out = persona.ask(cfg, "fact_checker", {"field": s["field"], "claims": [
            {"id": c["id"], "text": c["text"], "evidence": s["evidence"].get(c["id"], [])} for c in s["claims"]]}, timeout=180)
        return {"verdicts": {v["id"]: v for v in out["verdicts"]}}

    def fix(s):
        return {"domain": domains.clean(apply(s["domain"], s["verdicts"]), s["domain"].get("id", ""))}

    g = StateGraph(Card)
    for name, fn in (("draft", draft), ("gather", gather), ("check", check), ("fix", fix)):
        g.add_node(name, fn)
    g.set_entry_point("draft")
    g.add_conditional_edges("draft", lambda s: "gather" if s.get("domain") else END, {"gather": "gather", END: END})
    g.add_edge("gather", "check")
    g.add_edge("check", "fix")
    g.add_edge("fix", END)
    return g.compile()


def build(cfg, field, category="", heads=(), domain=None):
    """분야 카드를 만들고(없으면) 확인한다 → 확인한 카드, 실패하면 None (초안을 그대로 쓰게)."""
    try:
        return _graph(cfg).invoke({"field": field, "category": category, "heads": list(heads), "domain": domain}).get("domain")
    except Exception:
        log.exception("분야 카드 확인 실패: %s", field)
        return None


def start(cfg, field, domain=None, category="", heads=()):
    """분야 카드를 뒤에서 만들고(초안이 없으면 프로필 작가부터) 확인한다 — 같은 분야는 한 번만.
    새 분야는 질문을 먼저 만들어 보여 주고, 그동안 여기서 초안(약 70초)·확인(약 20초)을 돌려 ③ 사람 추천에서 합친다."""
    if not BACKGROUND:
        return
    with _lock:
        if field in _jobs:
            return
        job: dict = {"result": None}
        job["thread"] = threading.Thread(target=lambda: job.update(result=build(cfg, field, category, heads, domain)), daemon=True)
        _jobs[field] = job
    job["thread"].start()


def verified(field, wait=WAIT):
    """뒤에서 돌던 확인 결과 (wait초까지 기다림). 시작 안 했거나 실패·시간 초과면 None."""
    job = _jobs.get(field)
    if not job:
        return None
    job["thread"].join(wait)
    return job["result"]
