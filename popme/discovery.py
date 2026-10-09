"""발굴: 계속 지켜볼 만한 계정을 찾아 후보로 올린다. 목적은 AI 업계의 중요한 소식을 놓치지 않는 것.

0단계 후보 풀 (추가 요청 없음)
  X: 핵심 계정이 리트윗·인용·답글한 계정, 한국어 검색에서 반응 큰 작성자.
     같은 회사 사람끼리의 반응은 한 번으로 세고, 자기 회사 직원의 반응은 세지 않는다 (X 소속 배지).
     기업 계정은 더 엄격하게: 서로 다른 회사 3곳 이상이 8주 중 3주 이상에 걸쳐 반응해야 한다.
  Threads: 여러 태그·검색에 반복 등장하고 반응 큰 한국어 작성자 (사용자 승인 로직).
1단계 인기: 카테고리별 팔로워 기준. 시장 크기가 달라 세계 기업·세계 개인·국내 X·Threads 기준이 다르다.
2단계 꾸준함: 국내 X·Threads만. 세계 기업·개인은 가끔 올려도 하나하나 파급력이 커서 면제.
  후보 프로필을 한 번 열어 최근 글로 판단한다 (실행마다 몇 명씩, 한 번 확인하면 recheck_days 동안 재사용).
두 단계를 통과한 후보(stage='pass')만 화면·브리핑에 보인다.
승인한 국내 X·Threads 계정도 같은 꾸준함 기준으로 점검해, 4주 넘게 못 미치면 정리 추천으로 보여준다.
"""
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

DEFAULTS = {
    # 카테고리별 팔로워 [통과, 지켜보기]. 2026-10 표본(X 119명·Threads 43명)의 상위 25%~10% 선
    "followers": {"company": [10000, 10000], "global": [30000, 15000], "ko": [15000, 7000],
                  "threads": [10000, 3000]},
    "person_min_orgs": 2, "person_window_days": 14,  # 세계·국내 개인: 서로 다른 회사 2곳 이상 (최근 14일)
    "company_min_orgs": 3, "company_min_weeks": 3, "company_window_days": 56,  # 기업: 3곳 이상, 8주 중 3주 이상
    "recent_days": 14, "min_posts_30d": 4, "min_active_weeks": 3,  # 꾸준함
    "min_topic_share": 0.5, "liked_min": 10, "min_liked_posts": 3,
    "checks_per_run": 5, "recheck_days": 14, "review_after_days": 28,
}
EXEMPT = ("company", "global")  # 꾸준함 검사 면제
KIND_LABEL = {"company": "세계 기업", "global": "세계 개인", "ko": "국내 X", "threads": "Threads"}
# 넓은 태그는 아무나 걸리므로 등장 횟수를 절반만 인정
BROAD_TAGS = {"AI Threads", "Tech Threads", "AI", "인공지능AI", "AGI", "GPT", "AI 소식"}
TOPIC = re.compile(r"AI|인공지능|에이전트|agent|LLM|GPT|Claude|클로드|Codex|코덱스|Gemini|제미나이|MCP|바이브|"
                   r"코딩|개발|오픈소스|open.?source|프롬프트|prompt|모델|model|자동화|API|Cursor|커서|GitHub|깃허브|"
                   r"OpenAI|Anthropic|앤트로픽|오픈AI|딥러닝|머신러닝|deep.?learning|machine.?learning", re.I)


def settings(cfg):
    d = (cfg or {}).get("discovery", {})
    s = {**DEFAULTS, **{k: v for k, v in d.items() if k != "followers"}}
    s["followers"] = {**DEFAULTS["followers"], **d.get("followers", {})}
    return s


def _now():
    return datetime.now(timezone.utc)


def _ago(days):
    return (_now() - timedelta(days=days)).isoformat()


def _week(iso):
    y, w, _ = date.fromisoformat(iso[:10]).isocalendar()
    return f"{y}-W{w:02d}"


def _split(handle, platform):
    """후보 handle → 프로필·글의 작성자 이름 (Threads 후보는 'threads/username')."""
    return handle.split("/", 1)[1] if platform == "threads" else handle


# ---------------- 0단계: 후보 풀 ----------------
def kind_of(db, handle, platform, korean=False):
    if platform == "threads":
        return "threads"
    p = db.profile("x", handle)
    if p and (p["verified"] == "Business"
              or db.q("SELECT 1 FROM profiles WHERE platform='x' AND org=? COLLATE NOCASE LIMIT 1", handle)):
        return "company"  # 공식 기업 인증이거나, 다른 계정들의 소속 배지에 회사로 등장
    if korean or (p and p["lang"] == "ko"):
        return "ko"
    return "global"


def growth(db, platform, handle, days=30):
    """팔로워 증가율 (days일 전 기록이 있을 때만)."""
    now = db.profile(platform, handle)
    old = db.q("SELECT followers FROM profile_snapshots WHERE platform=? AND handle=? AND day <= ? "
               "ORDER BY day DESC LIMIT 1", platform, handle, _ago(days)[:10])
    if not now or not old or not old[0]["followers"]:
        return None
    return (now["followers"] - old[0]["followers"]) / old[0]["followers"]


def _drop_stale(db, platform, keep):
    """이번에 조건을 못 채운 대기 후보는 풀에서 뺀다 (다시 채우면 upsert_candidate가 되돌림)."""
    for r in db.q("SELECT handle FROM candidates WHERE status='new' AND COALESCE(platform,'x')=? "
                  "AND COALESCE(stage,'pool') != 'gone'", platform):
        if r["handle"].lower() not in keep:
            db.set_candidate_stage(r["handle"], "gone", "후보 조건에서 빠짐")


def update(db, core_handles, cfg=None):
    s = settings(cfg)
    core = {h.lower() for h in core_handles}
    orgs = {r["handle"].lower(): (r["org"] or "").lower() for r in db.q("SELECT handle, org FROM profiles WHERE platform='x'")}
    org_of = lambda h: orgs.get(h.lower()) or h.lower()  # 소속 배지가 없으면 그 사람 자체가 하나의 출처
    person_since = _ago(s["person_window_days"])
    stats = defaultdict(lambda: {"name": None, "by": set(), "orgs": set(), "orgs_recent": set(), "weeks": set(),
                                 "inter": 0, "ko": 0, "ko_likes": 0})

    # 1) 핵심 계정이 리포스트·인용·답글한 대상
    for r in db.q("SELECT author, ref_author, created_at FROM tweets WHERE kind IN ('retweet','quote','reply') "
                  "AND ref_author IS NOT NULL AND created_at >= ?", _ago(s["company_window_days"])):
        a, ref = r["author"].lower(), r["ref_author"]
        if a not in core or ref.lower() in core | {a}:
            continue
        src, ref_org = org_of(a), org_of(ref)
        if src in (ref_org, ref.lower()) or a == ref_org:
            continue  # 같은 회사 사람끼리, 회사와 그 직원 사이의 반응은 독립된 추천이 아님
        st = stats[ref]
        st["inter"] += 1
        st["by"].add(r["author"])
        st["orgs"].add(src)
        st["weeks"].add(_week(r["created_at"]))
        if r["created_at"] >= person_since:
            st["orgs_recent"].add(src)

    # 2) 한국어 검색에서 반응이 큰 글의 작성자
    for r in db.q("SELECT author, author_name, likes, lang FROM tweets WHERE via LIKE 'search:%' "
                  "AND created_at >= ?", person_since):
        if r["author"].lower() in core or r["lang"] != "ko":
            continue
        st = stats[r["author"]]
        st["name"] = r["author_name"]
        st["ko"] += 1
        st["ko_likes"] += r["likes"] or 0

    names = {r["author"].lower(): r["author_name"] for r in db.q(
        "SELECT author, author_name FROM tweets WHERE created_at >= ?", person_since)}
    keep = set()
    for handle, st in stats.items():
        kind = kind_of(db, handle, "x", korean=st["ko"] > 0)
        if kind == "company":
            if len(st["orgs"]) < s["company_min_orgs"] or len(st["weeks"]) < s["company_min_weeks"]:
                continue
            g = growth(db, "x", handle) or 0
            score = 5 * len(st["orgs"]) + 3 * len(st["weeks"]) + min(max(g, 0) * 50, 10)  # 떠오르는 회사는 가산
            reason = (f"서로 다른 회사 {len(st['orgs'])}곳의 핵심 계정({', '.join('@' + b for b in sorted(st['by']))})이 "
                      f"{len(st['weeks'])}주에 걸쳐 {st['inter']}번 리포스트·인용")
            if g >= 0.1:
                reason += f" · 떠오르는 중(30일 팔로워 +{g:.0%})"
        else:
            recent = len(st["orgs_recent"])
            if recent < s["person_min_orgs"] and not (st["ko"] >= 2 or st["ko_likes"] >= 200):
                continue
            score = 5 * recent + 3 * st["inter"] + 2 * st["ko"] + 2 * math.log10(1 + st["ko_likes"])
            reasons = []
            if recent >= s["person_min_orgs"]:
                reasons.append(f"서로 다른 회사 {recent}곳의 핵심 계정({', '.join('@' + b for b in sorted(st['by']))})이 "
                               f"리포스트·인용·답글")
            if st["ko"]:
                reasons.append(f"한국어 글 {st['ko']}개, 반응 합계 ♥{st['ko_likes']:,}")
            reason = " · ".join(reasons)
        db.upsert_candidate(handle, st["name"] or names.get(handle.lower()), reason, round(score, 1), "x", kind)
        keep.add(handle.lower())
    _drop_stale(db, "x", keep)


def update_threads(db, core_usernames, cfg=None):
    """Threads 발굴 (사용자 승인 로직): 여러 검색어·태그에 반복 등장 + 반응 큰 한국어 작성자.
    같은 단어의 검색·태그는 한 곳으로, 넓은 태그는 반 곳으로 센다. 좋아요는 로그로 줄여 글 하나가 점수를 지배하지 않게."""
    core = {u.lower() for u in core_usernames}
    since = _ago(7)
    stats = defaultdict(lambda: {"name": None, "kw": set(), "likes": 0, "ids": set(), "best": (0, "")})
    for r in db.q("SELECT t.id, t.author, t.author_name, t.likes, t.text, s.via FROM sightings s "
                  "JOIN tweets t ON t.id = s.id WHERE t.platform='threads' AND t.lang='ko' "
                  "AND (s.via LIKE 'th-search:%' OR s.via LIKE 'th-tag:%') AND s.seen_at >= ?", since):
        if r["author"].lower() in core:
            continue
        st = stats[r["author"]]
        st["name"] = r["author_name"]
        st["kw"].add(r["via"].split(":", 1)[1])
        if r["id"] not in st["ids"]:  # 같은 글이 여러 검색에 나와도 좋아요는 한 번만
            st["ids"].add(r["id"])
            st["likes"] += r["likes"] or 0
            st["best"] = max(st["best"], (r["likes"] or 0, " ".join((r["text"] or "").split())[:80]))
    keep = set()
    for user, st in stats.items():
        if len(st["kw"]) < 2 and st["likes"] < 1000:
            continue
        specific = [k for k in st["kw"] if k not in BROAD_TAGS]
        places = len(specific) + 0.5 * (len(st["kw"]) - len(specific))
        score = 2 * places + 3 * math.log10(1 + st["likes"])
        reason = (f"Threads 검색·태그 {len(st['kw'])}곳 등장({', '.join(sorted(st['kw']))[:80]}), "
                  f"글 {len(st['ids'])}개, ♥{st['likes']:,} · 대표글: {st['best'][1]}")
        db.upsert_candidate(f"threads/{user}", st["name"], reason, round(score, 1), "threads", "threads")
        keep.add(f"threads/{user}".lower())
    _drop_stale(db, "threads", keep)


# ---------------- 1·2단계: 인기 → 꾸준함 ----------------
def consistency(db, platform, user, s):
    """최근 글로 꾸준함 판단. s = settings(cfg). 반환: (통과 여부, 설명)."""
    rows = db.q("SELECT text, likes, created_at FROM tweets WHERE COALESCE(platform,'x')=? AND author=? COLLATE NOCASE "
                "AND created_at >= ?", platform, user, _ago(30))
    if not rows:
        last = db.q("SELECT MAX(created_at) m FROM tweets WHERE COALESCE(platform,'x')=? AND author=? COLLATE NOCASE",
                    platform, user)[0]["m"]
        return False, f"꾸준함 미달: 최근 30일 글 없음 (마지막 글 {last[:10] if last else '모름'})"
    last = max(r["created_at"] for r in rows)
    weeks = {_week(r["created_at"]) for r in rows if r["created_at"] >= _ago(28)}
    topic = sum(1 for r in rows if TOPIC.search(r["text"] or "")) / len(rows)
    liked = sum(1 for r in rows if (r["likes"] or 0) >= s["liked_min"])
    checks = [
        (last >= _ago(s["recent_days"]), f"마지막 글 {last[:10]}"),
        (len(rows) >= s["min_posts_30d"], f"30일 글 {len(rows)}개"),
        (len(weeks) >= s["min_active_weeks"], f"최근 4주 중 {len(weeks)}주 활동"),
        (topic >= s["min_topic_share"], f"AI·개발 글 {topic:.0%}"),
        (liked >= s["min_liked_posts"], f"♥{s['liked_min']}+ 글 {liked}개"),
    ]
    if all(ok for ok, _ in checks):
        return True, f"꾸준함 통과: 30일 글 {len(rows)}개 · {len(weeks)}주 활동 · AI·개발 글 {topic:.0%}"
    return False, "꾸준함 미달: " + ", ".join(t for ok, t in checks if not ok)


def _judge(db, s, platform, user, kind, checked_at):
    pass_f, watch_f = s["followers"][kind]
    p = db.profile(platform, user)
    f = p["followers"] if p else None
    label = KIND_LABEL[kind]
    if f is None:
        return "pool", f"{label} · 팔로워 확인 대기"
    head = f"{label} 팔로워 {f:,}명"
    if f < watch_f:
        return "fail", f"{head} (기준 {pass_f:,})"
    if f < pass_f:
        return "watch", f"{head} · 지켜보기 (기준 {pass_f:,})"
    if kind in EXEMPT:
        return "pass", f"{head} · 꾸준함 검사 면제"
    if not checked_at or checked_at < _ago(s["recheck_days"]):
        return "pool", f"{head} · 꾸준함 확인 대기"
    ok, why = consistency(db, platform, user, s)
    return ("pass" if ok else "fail"), f"{head} · {why}"


def evaluate(db, cfg=None, platform=None):
    """대기 중인 후보마다 1단계(팔로워)·2단계(꾸준함) 판정을 다시 매긴다."""
    s = settings(cfg)
    for c in db.q("SELECT * FROM candidates WHERE status='new' AND COALESCE(stage,'pool') != 'gone'"):
        plat = c["platform"] or "x"
        if platform and plat != platform:
            continue
        user = _split(c["handle"], plat)
        kind = c["kind"] or kind_of(db, user, plat)
        stage, note = _judge(db, s, plat, user, kind, c["checked_at"])
        db.set_candidate_stage(c["handle"], stage, note, kind)


def needs_check(db, cfg, platform):
    """프로필을 열어 확인할 후보 (점수 순, 실행마다 checks_per_run명).
    X는 팔로워가 수집 응답에 실려 오니 꾸준함 확인 대기만, Threads는 팔로워도 프로필에서만 보이니
    지켜보기는 recheck_days, 탈락은 30일 지나면 다시 본다 (팔로워가 지켜보기 기준도 못 넘은 탈락은 제외)."""
    s = settings(cfg)
    fresh = _ago(s["recheck_days"])
    out = []
    for c in db.q("SELECT * FROM candidates WHERE status='new' AND COALESCE(platform,'x')=? "
                  "AND stage IN ('pool','watch','fail') ORDER BY score DESC", platform):
        if c["stage"] == "pool":
            due = (c["checked_at"] or "") < fresh
        elif platform != "threads":
            due = False
        else:  # 지켜보기·탈락은 팔로워를 마지막으로 본 때 기준
            p = db.profile("threads", _split(c["handle"], platform))
            seen = (p["seen_at"] if p else "") or ""
            if c["stage"] == "watch":
                due = seen < fresh
            else:
                due = seen < _ago(30) and p is not None and p["followers"] >= s["followers"]["threads"][1]
        if due:
            out.append(c["handle"])
        if len(out) >= s["checks_per_run"]:
            break
    return out


# ---------------- 승인 계정 정기 점검 ----------------
def review(db, cfg=None, platform=None):
    """승인한 국내 X·Threads 계정에 꾸준함 기준을 다시 적용. 처음 못 미친 때를 기록해 둔다."""
    s = settings(cfg)
    for c in db.q("SELECT * FROM candidates WHERE status='added'"):
        plat = c["platform"] or "x"
        if platform and plat != platform:
            continue
        user = _split(c["handle"], plat)
        if (c["kind"] or kind_of(db, user, plat)) in EXEMPT:
            continue
        ok, why = consistency(db, plat, user, s)
        db.set_fail_since(c["handle"], None if ok else (c["fail_since"] or _now().isoformat()), why)


def to_review(db, cfg=None):
    """정리 추천: 꾸준함 기준에 review_after_days(4주) 넘게 못 미쳤거나, 마지막 글이 그보다 오래된 승인 계정."""
    s = settings(cfg)
    cutoff = _ago(s["review_after_days"])
    out = []
    for c in db.q("SELECT * FROM candidates WHERE status='added' AND fail_since IS NOT NULL ORDER BY handle"):
        plat = c["platform"] or "x"
        last = db.q("SELECT MAX(created_at) m FROM tweets WHERE COALESCE(platform,'x')=? AND author=? COLLATE NOCASE",
                    plat, _split(c["handle"], plat))[0]["m"]
        if c["fail_since"] <= cutoff or (last or "") <= cutoff:
            out.append({**c, "last_post": last})
    return out


def added(db, platform):
    return [c for c in db.q("SELECT * FROM candidates WHERE status='added' ORDER BY score DESC")
            if (c.get("platform") or "x") == platform]


def candidates(db, limit=10):
    """화면·브리핑에 보일 후보: 1·2단계를 통과한 것만."""
    return db.q("SELECT * FROM candidates WHERE status='new' AND stage='pass' ORDER BY score DESC LIMIT ?", limit)
