"""브리핑마다 「얼마나 들어왔나」를 재고, 평소와 크게 다르면 수집 경고에 한 줄 더한다.

수집 실패는 경고가 뜨지만, 소식이 조용히 덜 들어오거나 되풀이되는 일은 아무 데도 안 보였다.
2026-10 돌아가며 보는 계정의 사이 글이 28일 동안 빠졌고, 고르기 규칙을 바꾼 뒤 X·Threads 링크 카드가 47% → 20%가 됐지만
둘 다 몇 주 뒤에야 알았다. 재료 본문은 저장하지 않고 수만 카드 JSON 옆("stats")에 남긴다.
"""
import json
import re
from urllib.parse import urlsplit

SOCIAL = {"x.com", "twitter.com", "threads.com", "threads.net"}
HISTORY = 7       # 견줄 지난 브리핑 수 (이만큼 쌓여야 경고)
MIN_MEAN = 5      # 평소 값이 이보다 작으면 흔들림이 커서 견주지 않는다
# 사람 말 이름. 여기 없는 값은 재기만 하고 경고하지 않는다
LABELS = {
    "재료:X 핵심 계정": "X 계정 글", "재료:한국어 X 반응 큰 글": "한국어 X 검색 글", "재료:Threads 계정": "Threads 계정 글",
    "재료:한국어 Threads 반응 큰 글": "Threads 검색·태그 글", "재료:RSS": "RSS 글",
    "받음:X 계정": "새로 받은 X 계정 글", "받음:X 검색": "새로 받은 X 검색 글", "받음:Threads 계정": "새로 받은 Threads 계정 글",
    "받음:Threads 검색·태그": "새로 받은 Threads 검색·태그 글", "받음:RSS": "새로 받은 RSS 글",
    "카드:X·Threads 링크": "X·Threads 링크를 단 카드",
}
VIA_GROUP = [("user:", "X 계정"), ("search:", "X 검색"), ("th-user:", "Threads 계정"),
             ("th-search:", "Threads 검색·태그"), ("th-tag:", "Threads 검색·태그")]


def sections(data):
    """재료 텍스트 → {"재료:<구획 이름>": 글 줄 수}. 구획 이름은 "# " 제목의 괄호 앞."""
    out, cur = {}, None
    for line in data.split("\n"):
        if line.startswith("# "):
            cur = "재료:" + re.split(r" \(", line[2:], maxsplit=1)[0].strip()
            out.setdefault(cur, 0)
        elif cur and line.startswith("- "):
            out[cur] += 1
    return out


def collected(db, after):
    """after(지난 브리핑 created_at) 뒤에 처음 받은 글 수: 경로별, 그리고 그중 올라온 지 하루 넘어 받은 것."""
    out = {}
    if not after:
        return out
    for r in db.q("SELECT via, created_at, collected_at FROM tweets WHERE collected_at > ?", after):
        group = next((g for p, g in VIA_GROUP if (r["via"] or "").startswith(p)), None)
        if not group:
            continue
        out["받음:" + group] = out.get("받음:" + group, 0) + 1
        if r["created_at"] and _hours(r["created_at"], r["collected_at"]) > 24:
            out["하루 넘어 받음:" + group] = out.get("하루 넘어 받음:" + group, 0) + 1
    n = db.q("SELECT COUNT(*) AS n FROM items WHERE collected_at > ?", after)[0]["n"]
    out["받음:RSS"] = n
    return out


def _hours(a, b):
    from datetime import datetime
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600
    except ValueError:
        return 0


def _links(items):
    return {l["url"] for it in items if it.get("cat") != "발굴" for l in it.get("links") or []}


def cards(items, prev_items):
    """카드 수, X·Threads 링크를 단 카드 수, 앞 브리핑 카드와 같은 링크 수 (발굴 카드는 뺀다)."""
    news = [it for it in items if it.get("cat") != "발굴"]
    social = sum(any(urlsplit(l["url"]).netloc.lower().removeprefix("www.") in SOCIAL for l in it.get("links") or [])
                 for it in news)
    return {"카드:전체": len(news), "카드:X·Threads 링크": social,
            "카드:전날과 같은 링크": len(_links(news) & _links(prev_items or []))}


def measure(db, data, items, prev_at):
    """이번 브리핑의 stats. prev_at: 오늘 전 마지막 브리핑 created_at (없으면 받은 수는 비운다)."""
    prev = db.q("SELECT items FROM briefings WHERE created_at = ? LIMIT 1", prev_at) if prev_at else []
    prev_items = (_load(prev[0]["items"]) or {}).get("items", []) if prev else []
    return {**sections(data), **collected(db, prev_at), **cards(items, prev_items)}


def _load(s):
    try:
        return json.loads(s) if s else None
    except ValueError:
        return None


def _ga(word):
    """받침이 있으면 「이」, 없으면 「가」."""
    c = word[-1]
    return "이" if "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 else "가"


def warnings(db, stats, today):
    """지난 HISTORY개 브리핑(오늘 전)의 stats 평균과 견줘, 절반 아래거나 두 배 위인 값을 사람 말로."""
    rows = db.q("SELECT items FROM briefings WHERE date < ? ORDER BY date DESC LIMIT ?", today, HISTORY)
    past = [p["stats"] for p in (_load(r["items"]) for r in rows) if p and isinstance(p.get("stats"), dict)]
    out = []
    if len(past) < HISTORY:
        return out
    for key, label in LABELS.items():
        mean = sum(p.get(key, 0) for p in past) / len(past)
        now = stats.get(key, 0)
        if mean < MIN_MEAN:
            continue
        if now < mean / 2:
            out.append(f"{label}{_ga(label)} 평소보다 적게 들어왔어요 ({now}개, 평소 {mean:.0f}개)")
        elif now > mean * 2:
            out.append(f"{label}{_ga(label)} 평소보다 많이 들어왔어요 ({now}개, 평소 {mean:.0f}개)")
    if stats.get("카드:전날과 같은 링크", 0) >= 3:
        out.append(f"전날 카드와 같은 링크가 {stats['카드:전날과 같은 링크']}개예요 (같은 소식이 되풀이됐을 수 있어요)")
    return out
