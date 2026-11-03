"""질문에 맞는 자료를 쌓아 둔 전체 기록에서 찾는다 (캐릭터에게 질문하기의 '찾아오기' 단계).

글·RSS·지난 브리핑·일정을 검색어 부분 일치(LIKE)로 찾는다. 한국어는 조사가 붙어도("MCP를") 걸리고,
개인 DB 규모(1년 수십만 행)에선 이걸로 충분하다 (2026-11 글 2만·RSS 1.4만 행에서 50~160ms).
느려지면 SQLite FTS5로 바꾸되 trigram은 안 된다: 3글자 미만 검색어를 못 찾는다 (「금리」 LIKE 54건 → trigram 0건).
한글을 2글자씩 겹쳐 쪼개 넣으면 같은 건수를 찾았다.
"""
import bisect
import re
from datetime import datetime, timedelta, timezone

from popme import clock

STOP = {"누가", "누구", "뭐", "무엇", "무슨", "언제", "어디", "어떻게", "왜", "알려줘", "알려", "있었어", "있어", "했어",
        "했지", "얘기", "이야기", "관련", "소식", "요즘", "최근", "오늘", "어제", "지난", "이번", "정리", "해줘", "좀"}
PARTICLE = re.compile(r"(에서|으로|이랑|한테|에게|까지|부터|은|는|이|가|을|를|에|의|로|와|과|도|만|랑|야|지)$")
VERB_END = re.compile(r"(했|했어|했지|했나|했니|해줘|해|하나|할까|됐|됐어|있었|있었어|있어|있나|인가|일까|래|대|줘)$")
RECENT_DAYS = 30  # 고를 글·RSS의 절반은 기간 끝(없으면 지금)에서 이 날수 안의 것으로 (_pick)


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
    """'YYYY-MM-DD'(PC 시간대 날짜) → UTC ISO 범위. until은 그날 끝까지."""
    def utc(d, plus=0):
        return (datetime.fromisoformat(d).replace(tzinfo=clock.tz()) + timedelta(days=plus)).astimezone(timezone.utc).isoformat()
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


def lines_with(text, keywords):
    """마크다운 자료에서 검색어가 든 줄만, 그 줄이 속한 제목(#·##)과 함께. 제목에 계정 이름이 있어서 같이 남긴다."""
    keywords = [k.strip() for k in keywords if k and k.strip()]
    out, heads = [], {}
    for line in (text or "").splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            heads = {k: v for k, v in heads.items() if k < level}
            heads[level] = line
        elif keywords and _hits(line, keywords):
            out += [heads.pop(k) for k in sorted(heads)]  # 아직 안 쓴 제목만 (한 번 쓰면 빠진다)
            out.append(line)
    return "\n".join(out)


def _day(iso):
    return datetime.fromisoformat(iso).astimezone(clock.tz()).strftime("%Y-%m-%d") if iso else "?"


def _pick(rows, n, score, when, recent_from):
    """점수순 n개를 고르되 절반은 recent_from 이후 것에 먼저 준다. 점수(검색어 수·반응)만 보면 몇 달 전 인기 글이
    최근 글을 밀어냈다 (2026-11 「Claude」: 30일 안 후보 1,307개 중 14개만 뽑힘). 최신순으로만 고르지 않는 건
    순수 최신순이 답을 오히려 나쁘게 했다는 결과가 있어서 (Chronofy, arXiv 2607.20560). 최근 것이 모자라면 나머지로 채운다."""
    rows = sorted(rows, key=score, reverse=True)
    recent = [r for r in rows if (when(r) or "") >= recent_from][:n // 2]
    taken = {id(r) for r in recent}
    return recent + [r for r in rows if id(r) not in taken][:n - len(recent)]


def _post_score(rows, keywords):
    """글 점수: 반응 순위(플랫폼마다 따로, 0~1) ×2 + 계정 이름에 검색어가 있으면(당사자 계정) 1.5 + 검색어 수(2까지).
    예전엔 검색어 수 ×10이 거의 전부여서, 검색어를 여럿 담은 글이 Opus 5.5 출시·MCP Tasks(♥6천) 같은 공식 발표를
    밀어냈다. 좋아요는 X가 Threads보다 열 배 넘게 많아서 그대로 비교하지 않는다.
    순위만 바꿔선 답 품질 심사가 그대로였고(18문항 14:14), 답 규칙(QA_RULES의 「중요한 것」)과 같이 써야
    답이 반응 큰 글을 든다 (인용 글 ♥ 중앙값 X 7.8천 → 10.7천, Threads 25 → 63). 평가는 tools/ask_eval.py."""
    likes = {}
    for t in rows:
        likes.setdefault(t.get("platform") or "x", []).append(t["likes"] or 0)
    for v in likes.values():
        v.sort()

    def score(t):
        v = likes[t.get("platform") or "x"]
        rank = bisect.bisect_left(v, t["likes"] or 0) / len(v)
        own = 1.5 if _hits(f"{t['author']} {t['author_name']}", keywords) else 0
        return (min(_hits(f"{t['text']} {t['author']} {t['author_name']}", keywords), 2) + 2 * rank + own,
                t["created_at"] or "")
    return score


def find(db, keywords, since=None, until=None, limits=(60, 30, 20, 10)):
    """반환: (데이터 블록 텍스트, 찾은 개수 {"posts", "items", "briefing", "events"}).
    찾은 게 없으면 텍스트는 "". limits = (글, RSS, 브리핑 줄, 일정)."""
    keywords = [k.strip() for k in keywords if k and k.strip()]
    if not keywords:
        return "", {"posts": 0, "items": 0, "briefing": 0, "events": 0}
    lo, hi = _range(since, until)
    end = datetime.fromisoformat(hi) if until else datetime.now(timezone.utc)
    recent_from = (end - timedelta(days=RECENT_DAYS)).isoformat()
    n_posts, n_items, n_brief, n_events = limits
    parts = []

    where, args = _like(["text", "author", "author_name"], keywords)
    rows = db.q(f"SELECT * FROM tweets WHERE {where} AND created_at >= ? AND created_at < ?", *args, lo, hi)
    # 반응 큰 글·당사자 계정 글 순으로 고르고 (절반은 최근 글 몫), 보여줄 땐 날짜순
    rows = _pick(rows, n_posts, _post_score(rows, keywords), lambda t: t["created_at"], recent_from)
    rows.sort(key=lambda t: t["created_at"] or "")
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
    items = _pick(items, n_items, lambda i: (_hits(f"{i['title']} {i['summary']}", keywords), i["published"] or ""),
                  lambda i: i["published"] or i["collected_at"], recent_from)
    items.sort(key=lambda i: i["published"] or i["collected_at"] or "")
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

    counts = {"posts": len(rows), "items": len(items), "briefing": min(len(lines), n_brief), "events": len(events)}
    if not parts:
        return "", counts
    period = f"{since or '처음'} ~ {until or '지금'}"
    return f"# 질문 관련 기록 검색 (검색어: {', '.join(keywords)} / 기간: {period})\n" + "\n".join(parts), counts
