"""질문에 맞는 자료를 쌓아 둔 전체 기록에서 찾는다 (캐릭터에게 질문하기의 '찾아오기' 단계).

글·RSS·지난 브리핑·일정을 검색어 부분 일치(LIKE)로 찾는다. 한국어는 조사가 붙어도("MCP를") 걸리고,
개인 DB 규모(1년 수십만 행)에선 이걸로 충분하다. 느려지면 SQLite FTS5(trigram)로 바꾸면 된다.
"""
import math
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
STOP = {"누가", "누구", "뭐", "무엇", "무슨", "언제", "어디", "어떻게", "왜", "알려줘", "알려", "있었어", "있어", "했어",
        "했지", "얘기", "이야기", "관련", "소식", "요즘", "최근", "오늘", "어제", "지난", "이번", "정리", "해줘", "좀"}
PARTICLE = re.compile(r"(에서|으로|이랑|한테|에게|까지|부터|은|는|이|가|을|를|에|의|로|와|과|도|만|랑|야|지)$")
VERB_END = re.compile(r"(했|했어|했지|했나|했니|해줘|해|하나|할까|됐|됐어|있었|있었어|있어|있나|인가|일까|래|대|줘)$")


def fallback_keywords(question):
    """Claude로 검색어를 못 뽑았을 때: 단어에서 조사를 떼고 의문사·흔한 말을 뺀다."""
    out = []
    for w in re.findall(r"[0-9A-Za-z][0-9A-Za-z._+-]*|[가-힣]+", question):
        if re.match(r"[가-힣]", w):
            if VERB_END.search(w):
                continue  # "얘기했지", "알려줘" 같은 서술어는 검색어가 아님
            w = PARTICLE.sub("", w)
        if len(w) >= 2 and w not in STOP and w not in out:
            out.append(w)
    return out[:8]


def _range(since, until):
    """'YYYY-MM-DD'(KST) → UTC ISO 범위. until은 그날 끝까지."""
    def utc(d, plus=0):
        return (datetime.fromisoformat(d).replace(tzinfo=KST) + timedelta(days=plus)).astimezone(timezone.utc).isoformat()
    return (utc(since) if since else "", utc(until, 1) if until else "9999")


def _like(cols, keywords):
    """(col LIKE ? OR ...) 를 검색어마다 OR로. %·_는 글자 그대로."""
    esc = [k.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for k in keywords]
    sql = " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for _ in esc for c in cols)
    args = [f"%{k}%" for k in esc for _ in cols]
    return f"({sql})", args


def _hits(text, keywords):
    t = (text or "").lower()
    return sum(1 for k in keywords if k.lower() in t)


def _day(iso):
    return datetime.fromisoformat(iso).astimezone(KST).strftime("%Y-%m-%d") if iso else "?"


def find(db, keywords, since=None, until=None, limits=(60, 30, 20, 10)):
    """반환: 데이터 블록 텍스트 (찾은 게 없으면 ""). limits = (글, RSS, 브리핑 줄, 일정)."""
    keywords = [k.strip() for k in keywords if k and k.strip()]
    if not keywords:
        return ""
    lo, hi = _range(since, until)
    n_posts, n_items, n_brief, n_events = limits
    parts = []

    where, args = _like(["text", "author", "author_name"], keywords)
    rows = db.q(f"SELECT * FROM tweets WHERE {where} AND created_at >= ? AND created_at < ?", *args, lo, hi)
    # 검색어를 많이 담은 글 → 반응 큰 글 순으로 고르고, 보여줄 땐 날짜순
    rows.sort(key=lambda t: (_hits(f"{t['text']} {t['author']} {t['author_name']}", keywords) * 10
                             + 2 * math.log10(1 + (t["likes"] or 0)), t["created_at"] or ""), reverse=True)
    rows = sorted(rows[:n_posts], key=lambda t: t["created_at"] or "")
    if rows:
        parts.append(f"## SNS 글 ({len(rows)}개)")
        for t in rows:
            link = t.get("link") or f"https://x.com/{t['author']}/status/{t['id']}"
            text = " ".join((t["text"] or "").split())[:400]
            parts.append(f"- [{_day(t['created_at'])}] {t.get('platform') or 'x'} @{t['author']}: {text} "
                         f"| ♥{t['likes'] or 0} | {link}")

    where, args = _like(["title", "summary", "source"], keywords)
    items = db.q(f"SELECT * FROM items WHERE {where} AND COALESCE(published, collected_at) >= ? "
                 f"AND COALESCE(published, collected_at) < ?", *args, lo, hi)
    items.sort(key=lambda i: (_hits(f"{i['title']} {i['summary']}", keywords), i["published"] or ""), reverse=True)
    items = sorted(items[:n_items], key=lambda i: i["published"] or i["collected_at"] or "")
    if items:
        parts.append(f"## RSS ({len(items)}개)")
        parts += [f"- [{_day(i['published'] or i['collected_at'])}] [{i['grp']} / {i['source']}] {i['title']} — "
                  f"{(i['summary'] or '')[:300]} | {i['url']}" for i in items]

    lines = []
    for b in db.q("SELECT date, markdown FROM briefings WHERE date >= ? AND date <= ? ORDER BY date",
                  (since or "0000"), (until or "9999")):
        lines += [f"- [{b['date']} 브리핑] {line.strip()[2:] if line.strip().startswith('- ') else line.strip()}"
                  for line in b["markdown"].splitlines() if line.strip() and _hits(line, keywords)]
    if lines:
        parts.append(f"## 지난 브리핑에서 ({min(len(lines), n_brief)}줄)")
        parts += lines[-n_brief:]

    where, args = _like(["title", "location"], keywords)
    events = db.q(f"SELECT title, start_at, all_day, deleted FROM events WHERE {where} AND start_at >= ? "
                  f"AND start_at < ? ORDER BY start_at DESC LIMIT ?", *args, lo, hi, n_events)
    if events:
        parts.append(f"## 일정 ({len(events)}개)")
        parts += [f"- [{_day(e['start_at'])}] {e['title']}{' (지워짐)' if e['deleted'] else ''}" for e in events]

    if not parts:
        return ""
    period = f"{since or '처음'} ~ {until or '지금'}"
    return f"# 질문 관련 기록 검색 (검색어: {', '.join(keywords)} / 기간: {period})\n" + "\n".join(parts)
