"""분야마다 「팔로워 몇 명이면 큰가」와 「그 분야 글인가」 (설계 4-9): 발굴(popme/discovery.py)이 관심사마다 이 기준으로 판정한다.

지금 기준(discovery.DEFAULTS)은 AI 분야 표본에서 정했다. 분야마다 시장 크기가 달라 (K팝 기획사 수천만, 부동산 수천) 같은 수를 쓰면
K팝은 아무나 통과하고 부동산은 다 떨어진다. 그래서 그 분야 대표 계정들의 팔로워 중앙값을 AI 대표 계정 중앙값과 견줘 기준을 늘이고 줄인다.
- 묶음: 채널 명부(조사 때 잰 수)로 — tools/build_card.py
- 새 분야: 만들 때 G1(popme/fieldcard.py)이 대표 계정을 찾아 잰다 (X는 많이 열면 제한될 수 있어 10곳까지)
- 쓰면서: 매주 그 관심사 계정들의 실제 팔로워(수집할 때 DB에 쌓임)로 다시 (calibrate)
「그 분야 글인가」는 AI 단어 대신 분야 카드의 소식 종류·팬의 말·다른 이름으로 만든 낱말.
"""
import json
import logging
import re
import statistics
from datetime import date

from popme import config

log = logging.getLogger(__name__)
# AI 관심사 대표 계정의 X 팔로워 중앙값 (2026-10-31, 앱 DB: 해외 61곳). 국내는 표본이 2곳뿐이라
# 지금 기준의 국내/해외 비(1.5만/3만)로 맞춘다.
AI_REF = {"world": 137_000, "kr": 68_500}
MIN_SAMPLE = 3            # 이보다 적으면 그 지역은 AI 기준 그대로 (해외 구단 수천만을 국내 기준에 쓰지 않게)
SCALE_RANGE = (0.05, 10)  # 배수를 이 안으로 (대표 계정 몇 개가 거대해도 기준이 터무니없지 않게)
FLOOR = 300               # 기준이 이보다 낮아지지는 않게
CALIBRATION = config.DATA_DIR / "field_rules.json"  # 매주 보정한 값 (관심사 id → 기준)


def _round(n):
    """두 자리 유효 숫자로 (37,412 → 37,000)."""
    n = max(int(n), FLOOR)
    k = 10 ** max(len(str(n)) - 2, 0)
    return round(n / k) * k


def scales(samples):
    """samples [(region, followers)] → {"world": 배수, "kr": 배수, "n": 표본 수}."""
    by = {"world": [], "kr": []}
    for region, f in samples:
        if f:
            by["kr" if region == "kr" else "world"].append(f)
    raw = {r: statistics.median(v) / AI_REF[r] for r, v in by.items() if len(v) >= MIN_SAMPLE}
    out = {}
    for r in ("world", "kr"):
        s = raw.get(r) or 1.0
        out[r] = min(max(s, SCALE_RANGE[0]), SCALE_RANGE[1])
    out["n"] = sum(len(v) for v in by.values())
    return out


def thresholds(sc, base=None):
    """배수 → 발굴 기준 {kind: [통과, 지켜보기]} (discovery.DEFAULTS의 kind). 세계 기업·개인은 해외, 국내 X·Threads는 국내 배수."""
    from popme import discovery
    base = base or discovery.DEFAULTS["followers"]
    region = {"company": "world", "global": "world", "ko": "kr", "threads": "kr"}
    return {k: [_round(v * sc[region[k]]) for v in base[k]] for k in base}


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


def rules_from(samples, domain=None, names=None):
    """대표 계정 표본 → 카드 info에 넣을 기준 {followers, scale, sample, topic_words, checked}."""
    sc = scales(samples)
    return {"followers": thresholds(sc), "scale": {"world": round(sc["world"], 3), "kr": round(sc["kr"], 3)},
            "sample": sc["n"], "topic_words": topic_words(domain, names), "checked": date.today().isoformat()}


def _calibrated():
    try:
        return json.loads(CALIBRATION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rules_for(pid):
    """관심사 하나의 발굴 기준 {followers, topic(정규식 또는 None)}. 매주 보정한 값 > 분야 카드 > 없음(AI 기준)."""
    from popme import domains
    info = ((domains.load(pid) or {}).get("info") or {})
    cal = _calibrated().get(pid) or {}
    followers = cal.get("followers") or info.get("followers")
    words = info.get("topic_words") or []
    topic = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I) if words else None
    return {"followers": followers, "topic": topic}


def calibrate(db, cfg, min_sample=5):
    """매주: 관심사마다 그 관심사 X 계정들의 실제 팔로워(수집 때 DB에 쌓임)로 기준을 다시 잡아 field_rules.json에. 반환: 고친 관심사 id."""
    from popme import catalog
    old, out = _calibrated(), {}
    by = {}
    for a in catalog.x_accounts(cfg):
        p = db.profile("x", a["handle"])
        if p and p["followers"]:
            region = a.get("region") or ("kr" if p["lang"] == "ko" else "world")
            by.setdefault(a["interest"], []).append((region, p["followers"]))
    for pid, samples in by.items():
        if len(samples) < min_sample or pid in AI_INTERESTS:
            continue  # AI 관심사는 지금 기준이 곧 바탕값
        sc = scales(samples)
        out[pid] = {"followers": thresholds(sc), "scale": {"world": round(sc["world"], 3), "kr": round(sc["kr"], 3)},
                    "sample": sc["n"], "checked": date.today().isoformat()}
    if out:
        CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION.write_text(json.dumps({**old, **out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return sorted(out)


AI_INTERESTS = ("ai-coding", "data-science", "ml-engineering", "enterprise", "llm-apps")


def missing(cfg):
    """고른 관심사 중 발굴 기준이 아직 없는 것 (AI 관심사는 지금 기준이 곧 바탕값이라 빼고)."""
    from popme import catalog
    return [p["id"] for p in catalog.chosen(cfg) if p["id"] not in AI_INTERESTS and not rules_for(p["id"])["followers"]]


def due(days=7):
    """매주 보정할 때가 됐나 (field_rules.json을 마지막으로 쓴 지 days일)."""
    try:
        return (date.today() - date.fromtimestamp(CALIBRATION.stat().st_mtime)).days >= days
    except OSError:
        return True
