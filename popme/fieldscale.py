"""분야마다 「팔로워 몇 명이면 큰가」와 「그 분야 글인가」 (설계 4-9): 발굴(popme/discovery.py)이 관심사마다 이 기준으로 판정한다.

다른 분야(AI)의 숫자를 빌리지 않는다. 그 분야의 유명 계정에서 출발해 거꾸로 타고 내려가며 그 분야 자체의 분포를 잰다 (사용자 결정):
  1) 출발: 그 분야 유명 계정 (새 분야는 채널 찾기 페르소나 + oEmbed 확인, 이미 쓰는 관심사는 그 관심사의 X 계정)
  2) 타고 내려가기: 새 분야는 만들 때 유명 계정의 팔로잉 목록(그가 직접 고른 계정들 — 목록 응답에 팔로워·자기소개가 실려 온다,
     전용 Chrome으로 10곳까지), 쓰는 중에는 매일 수집하며 쌓인 관계(유명 계정이 리포스트·인용·답글한 계정)
  3) 그 분야 계정만: 유명 계정 둘 이상이 팔로우·반응했거나, 자기소개·반응한 글에 분야 낱말이 있을 때
  4) 그 분야 분포의 상위 25% 선 = 지켜보기, 상위 10% 선 = 통과 (AI 기준을 정한 방식과 같다). 국내·해외 개인·기업은 따로,
     국내 계정이 모자라면 국내 기준은 비워 둔다 (해외 분포를 빌리지 않음)
  5) 매주 다시 — 발굴로 승인한 계정도 출발점이 되니 쓸수록 더 아래까지
표본이 모자라면 AI 기준으로 대신하지 않고 「조사 중」으로 둔다 (discovery가 판정을 미룬다).
「그 분야 글인가」는 분야 카드의 소식 종류·팬의 말·꼭 아는 이름·분야 이름과 다른 말로 만든 낱말.
"""
import json
import logging
import re
from datetime import date, timedelta

from popme import config

log = logging.getLogger(__name__)
WATCH_Q, PASS_Q = 0.75, 0.90   # 상위 25% 선 = 지켜보기, 상위 10% 선 = 통과
MIN_KIND = 5                   # 한 갈래(국내·해외 개인·기업)에 이만큼은 있어야 그 갈래만의 선을 긋는다 (모자라면 분야 전체 분포로)
MIN_POOL = 8                   # 분야 전체가 이보다 적으면 「조사 중」
WINDOW_DAYS = 56               # 쓰는 중 보정: 최근 8주의 반응 관계
CALIBRATION = config.DATA_DIR / "field_rules.json"  # 매주 보정한 값 (관심사 id → 기준)
AI_INTERESTS = ("ai-coding", "data-science", "ml-engineering", "enterprise", "llm-apps")  # 지금 기준(DEFAULTS)이 이 방식으로 나온 분야


def _round(n):
    """두 자리 유효 숫자로 (37,412 → 37,000)."""
    n = max(int(n), 1)
    k = 10 ** max(len(str(n)) - 2, 0)
    return max(round(n / k) * k, 1)


def _quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = q * (len(xs) - 1)
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def kind_of(p):
    """계정 갈래 (discovery.kind_of와 같은 뜻): 공식 기업 인증 → company, 한국어 → ko, 나머지 → global."""
    if (p.get("verified") or "") == "Business":
        return "company"
    return "ko" if p.get("lang") == "ko" else "global"


def field_pool(core, edges, users, words=()):
    """출발 계정(core)과 그들의 반응(edges: (반응한 계정, 반응받은 계정, 글))에서 그 분야 계정 → [{handle, followers, kind, core}].
    반응받은 계정은 유명 계정 둘 이상이 반응했거나 그 글에 분야 낱말이 있을 때만 (분야 밖 리트윗은 뺀다)."""
    core = {c.lower() for c in core}
    topic = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I) if words else None
    by, on_topic = {}, set()
    for src, ref, text in edges:
        src, ref = (src or "").lower(), (ref or "").lower()
        if src not in core or not ref or ref == src:
            continue
        by.setdefault(ref, set()).add(src)
        if topic and topic.search(text or ""):
            on_topic.add(ref)
    keep = core | {r for r, s in by.items() if len(s) >= 2 or r in on_topic}
    out = []
    for h in sorted(keep):
        u = users.get(h)
        if u and isinstance(u.get("followers"), int):
            out.append({"handle": h, "followers": u["followers"], "kind": kind_of(u), "core": h in core})
    return out


def thresholds(pool):
    """그 분야 분포 → {kind: [통과, 지켜보기]}. 전체가 MIN_POOL 미만이면 None (조사 중).
    국내(ko)는 국내 계정만으로 — 모자라면 그 갈래는 비워 둔다 (해외 분포를 국내에 빌리지 않음, 발굴이 국내 후보 판정을 미룬다).
    해외 개인(global)이 모자라면 국내가 아닌 계정 전체로, 기업(company)이 모자라면 같은 분야 해외 분포로."""
    if len(pool) < MIN_POOL:
        return None
    by = {k: [p["followers"] for p in pool if p["kind"] == k] for k in ("company", "global", "ko")}
    abroad = by["company"] + by["global"]
    picks = {"ko": by["ko"], "global": by["global"] if len(by["global"]) >= MIN_KIND else abroad,
             "company": by["company"] if len(by["company"]) >= MIN_KIND else abroad}
    return {k: [_round(_quantile(xs, PASS_Q)), _round(_quantile(xs, WATCH_Q))] for k, xs in picks.items() if len(xs) >= MIN_KIND} or None


def topic_words(domain, names=None):
    """그 분야 글인지 볼 낱말: 소식 종류 이름 조각·팬의 말·꼭 아는 이름·분야 이름과 다른 말."""
    d, names = domain or {}, names or {}
    words = set()
    for c in d.get("categories", []):
        words |= {w for w in re.split(r"[·/,\s]+", c["name"]) if len(w) >= 2}
    words |= {v["word"] for v in d.get("vocabulary", []) if len(v.get("word", "")) >= 2}
    words |= {re.split(r"[(\s]", m["name"])[0] for m in d.get("must_know", []) if len(m.get("name", "")) >= 2}
    words |= {w for w in [names.get("ko"), names.get("en"), *names.get("aliases", [])] if w and len(w) >= 2}
    return sorted(w for w in words if not re.fullmatch(r"[\d\W]+", w))[:80]


def rules_from(pool, domain=None, names=None, how=""):
    """그 분야 계정 표본 → 카드 info에 넣을 기준 {followers(없으면 조사 중), sample, topic_words, checked, how}."""
    f = thresholds(pool)
    return {**({"followers": f} if f else {}), "sample": len(pool), "topic_words": topic_words(domain, names),
            "checked": date.today().isoformat(), "how": how}


def _calibrated():
    try:
        return json.loads(CALIBRATION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rules_for(pid):
    """관심사 하나의 발굴 기준 {followers(없으면 None — 조사 중), topic(정규식 또는 None)}. 매주 보정한 값 > 분야 카드."""
    from popme import domains
    info = ((domains.load(pid) or {}).get("info") or {})
    cal = _calibrated().get(pid) or {}
    followers = cal.get("followers") or info.get("followers")
    words = info.get("topic_words") or []
    topic = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I) if words else None
    return {"followers": followers, "topic": topic}


def calibrate(db, cfg):
    """매주: 관심사마다 그 관심사 X 계정(출발점)과 최근 8주 그들이 반응한 계정(수집하며 쌓인 관계·팔로워)으로 그 분야 분포를 다시. 반환: 고친 id."""
    from popme import catalog, domains
    old, out = _calibrated(), {}
    core_of = {}
    for a in catalog.x_accounts(cfg):
        core_of.setdefault(a["interest"], set()).add(a["handle"].lower())
    for c in db.q("SELECT handle, interest FROM candidates WHERE status='added' AND interest IS NOT NULL "
                  "AND COALESCE(platform,'x')='x'"):
        core_of.setdefault(c["interest"], set()).add(c["handle"].lower())  # 승인한 발굴 계정도 출발점 → 더 아래까지
    since = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    for pid, core in core_of.items():
        if pid in AI_INTERESTS:
            continue
        marks = ",".join("?" * len(core))
        edges = [(r["author"], r["ref_author"], r["text"]) for r in db.q(
            f"SELECT author, ref_author, text FROM tweets WHERE lower(author) IN ({marks}) AND ref_author IS NOT NULL "
            f"AND kind IN ('retweet','quote','reply') AND created_at >= ?", *core, since)]
        handles = core | {(e[1] or "").lower() for e in edges}
        users = {}
        for h in handles:
            p = db.profile("x", h)
            if p and p["followers"]:
                users[h] = dict(p)
        pool = field_pool(core, edges, users, (domains.load(pid) or {}).get("info", {}).get("topic_words", []))
        f = thresholds(pool)
        if f:
            out[pid] = {"followers": f, "sample": len(pool), "checked": date.today().isoformat(), "how": "수집한 반응 관계"}
    if out:
        CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION.write_text(json.dumps({**old, **out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return sorted(out)


def missing(cfg):
    """고른 관심사 중 발굴 기준이 아직 없는 것 (AI 관심사는 지금 기준이 이 방식으로 나온 값이라 빼고)."""
    from popme import catalog
    return [p["id"] for p in catalog.chosen(cfg) if p["id"] not in AI_INTERESTS and not rules_for(p["id"])["followers"]]


def due(days=7):
    """매주 보정할 때가 됐나 (field_rules.json을 마지막으로 쓴 지 days일)."""
    try:
        return (date.today() - date.fromtimestamp(CALIBRATION.stat().st_mtime)).days >= days
    except OSError:
        return True
