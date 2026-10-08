"""발굴 모드: 핵심 계정이 자주 리포스트·인용·답글하는 사람, 한국어로 반응이 큰 글을 쓰는 사람을 후보로 올린다."""
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def update(db, core_handles):
    core = {h.lower() for h in core_handles}
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    stats = defaultdict(lambda: {"name": None, "inter": 0, "by": set(), "ko": 0, "ko_likes": 0})

    # 1) 핵심 계정이 리포스트·인용·답글한 대상
    for r in db.q("SELECT author, kind, ref_author FROM tweets WHERE kind IN ('retweet','quote','reply') "
                  "AND ref_author IS NOT NULL AND created_at >= ?", since):
        if r["author"].lower() in core and r["ref_author"].lower() not in core | {r["author"].lower()}:
            s = stats[r["ref_author"]]
            s["inter"] += 1
            s["by"].add(r["author"])

    # 2) 한국어 검색에서 반응이 큰 글의 작성자
    for r in db.q("SELECT author, author_name, likes, lang FROM tweets WHERE via LIKE 'search:%' "
                  "AND created_at >= ?", since):
        if r["author"].lower() in core or r["lang"] != "ko":
            continue
        s = stats[r["author"]]
        s["name"] = r["author_name"]
        s["ko"] += 1
        s["ko_likes"] += r["likes"] or 0

    names = {r["author"].lower(): r["author_name"] for r in db.q(
        "SELECT author, author_name FROM tweets WHERE created_at >= ?", since)}
    for handle, s in stats.items():
        if s["inter"] < 2 and not (s["ko"] >= 2 or s["ko_likes"] >= 200):
            continue
        score = 3 * s["inter"] + 5 * len(s["by"]) + 2 * s["ko"] + 2 * math.log10(1 + s["ko_likes"])
        reasons = []
        if s["inter"]:
            reasons.append(f"핵심 계정 {len(s['by'])}명({', '.join('@' + b for b in sorted(s['by']))})이 "
                           f"{s['inter']}번 리포스트·인용·답글")
        if s["ko"]:
            reasons.append(f"한국어 글 {s['ko']}개, 반응 합계 ♥{s['ko_likes']:,}")
        db.upsert_candidate(handle, s["name"] or names.get(handle.lower()), " · ".join(reasons), round(score, 1))


def added(db, platform):
    return [c for c in candidates(db, status="added", limit=500) if (c.get("platform") or "x") == platform]


def update_threads(db, core_usernames):
    """Threads 발굴 (사용자 승인 로직): 여러 검색어·태그에 반복 등장 + 반응 큰 한국어 작성자.
    점수 = 2 × 등장한 검색어·태그 수 + 좋아요 합계 / 100
    """
    core = {u.lower() for u in core_usernames}
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    stats = defaultdict(lambda: {"name": None, "vias": set(), "likes": 0, "best": (0, "")})
    for r in db.q("SELECT t.id, t.author, t.author_name, t.likes, t.text, s.via FROM sightings s "
                  "JOIN tweets t ON t.id = s.id WHERE t.platform='threads' AND t.lang='ko' "
                  "AND (s.via LIKE 'th-search:%' OR s.via LIKE 'th-tag:%') AND s.seen_at >= ?", since):
        if r["author"].lower() in core:
            continue
        s = stats[r["author"]]
        s["name"] = r["author_name"]
        s["vias"].add(r["via"])
        s.setdefault("ids", set())
        if r["id"] not in s["ids"]:  # 같은 글이 여러 검색에 나와도 좋아요는 한 번만
            s["ids"].add(r["id"])
            s["likes"] += r["likes"] or 0
            s["best"] = max(s["best"], (r["likes"] or 0, " ".join((r["text"] or "").split())[:80]))
    for user, s in stats.items():
        if len(s["vias"]) < 2 and s["likes"] < 1000:
            continue
        score = 2 * len(s["vias"]) + s["likes"] / 100
        where = ", ".join(sorted(v.split(":", 1)[1] for v in s["vias"]))[:80]
        reason = (f"Threads 검색·태그 {len(s['vias'])}곳 등장({where}), 글 {len(s['ids'])}개, "
                  f"♥{s['likes']:,} · 대표글: {s['best'][1]}")
        db.upsert_candidate(f"threads/{user}", s["name"], reason, round(score, 1), platform="threads")


def candidates(db, status="new", limit=10):
    return db.q("SELECT * FROM candidates WHERE status=? ORDER BY score DESC LIMIT ?", status, limit)
