"""관심사 설정 (docs/INTERESTS-DESIGN.md 4장): 고르기·입력 → (새 관심사) 보는 방식·분야별 질문 → 사람·기업 → 묶음 만들기.

AI(설정의 브리핑 AI, 도구 끔)는 후보와 기준 문장만 만든다. AI가 말한 X 계정·사이트 주소는 그대로 믿지 않고
여기서 실제로 열어 확인한 것만 저장한다 (X는 oEmbed로 있는지, 공식 RSS는 정해진 규칙으로 찾아 열어 보고, 뉴스는 검색해 본다).
"""
import json
import logging
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from popme import briefing, catalog, config, llm
from popme.sources import rss

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) POPME/0.1"
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
SITE_RE = re.compile(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/[\w./-]*)?$", re.I)
FEED_PATHS = ["/feed", "/rss.xml", "/rss", "/feed.xml"]
TIMEOUT = 6  # 공식 사이트 RSS 찾기 — 대부분 없거나 막아서, 오래 기다리지 않는다
RECENT_DAYS = 90  # 공식 RSS는 이 안에 글이 있어야 받는다 (멈춘 피드 거르기)

# 보는 방식 (고정 질문 — AI가 만들지 않음) → 칸 순서. 칸의 순서·종류는 코드가 정하고 AI는 그 분야 말로 문장·보기만 만든다 (일관성)
LENSES = {"overview": "업계 흐름 전반", "target": "특정 대상 위주", "both": "둘 다"}
SLOTS = {"overview": ["범위", "변화"], "target": ["범위", "대상"], "both": ["범위", "변화", "대상"]}
# 분야 유형 → 칸 이름 (AI가 유형을 고르고, 질문을 이 말에 맞춰 만든다)
TYPES = {
    "스포츠": {"범위": "리그·대회", "대상": "응원하는 팀", "변화": "관심 있는 소식(이적·전술·규정 …)", "사람": "선수·감독·전문 기자"},
    "산업·기업": {"범위": "세부 산업", "대상": "관심 기업", "변화": "관심 있는 변화(규제·신제품·투자·공급망 …)", "사람": "경영진·분석가·전문 기자"},
    "엔터·문화": {"범위": "장르", "대상": "좋아하는 그룹·작품", "변화": "관심 있는 소식(컴백·공연·흥행 …)", "사람": "아티스트·제작자·평론가"},
    "투자·시장": {"범위": "시장", "대상": "관심 종목·자산", "변화": "관심 있는 변화(실적·금리·정책 …)", "사람": "분석가·운용역·경제 기자"},
    "기술·제품": {"범위": "제품군", "대상": "관심 브랜드·제품", "변화": "관심 있는 변화(신제품·업데이트·가격 …)", "사람": "개발자·리뷰어·전문 기자"},
    "취미·생활": {"범위": "세부 분야", "대상": "관심 대상", "변화": "관심 있는 소식", "사람": "전문가·크리에이터"},
    "기타": {"범위": "세부 분야", "대상": "관심 대상", "변화": "관심 있는 변화", "사람": "전문가·기자"},
}
NO_ASK = re.compile(r"정치|성향|지지\s*정당|종교|건강|질병|병력|주소|사는\s*곳|거주|나이|연령|소득|연봉|재산|성별|성적\s*지향")
HEADLINES = 30  # 질문을 만들 때 근거로 주는 최근 1주 헤드라인 수 (AI 지식은 오래됐을 수 있다 — 이적·감독 교체 등)
QUESTIONS = """{persona}분야: {field}{where}
보는 방식: {lens}
아래는 이 분야의 최근 1주 뉴스 헤드라인이다 (지금 상황의 근거):
{headlines}

이 분야 소식을 받으려는 사람에게 할 질문을 만들어라.
1) type: 이 분야의 유형 하나 — {types}
2) questions: 아래 칸을 이 순서대로 하나씩 (칸 이름은 유형별 말로 바꿔 묻는다)
{slots}
각 질문: {{"slot": "칸 이름(범위·변화·대상 중 하나)", "text": "질문 문장 25자 이내", "multi": true,
  "options": [{{"label": "보기 20자 이내", "region": "kr 또는 world", "en": "영어(원어) 이름 — 해외만, 없으면 빈 문자열"}}],
  "options_by": {{"범위 보기 label": [보기 …]}}  ← 「대상」 칸만: 「범위」에서 고른 것마다 다른 보기 (없으면 생략)}}
- 보기는 4~8개, 실존하는 것만, 지금 기준으로 (헤드라인을 참고). 「상관없어요」·「직접 입력」은 넣지 마라 (코드가 붙인다).
- 정치 성향·종교·건강·사는 곳·나이·소득은 묻지 마라.
출력: {{"type": "...", "questions": [...]}}"""
SYSTEM = "너는 소식 출처를 고르는 도우미다. 요청한 JSON 하나만 출력한다. 실존하는 사람·기업만 쓰고, 모르면 적게 쓴다."
SUGGEST = """{persona}분야: {field}{where}
범위: {scope}
{context}이 분야 소식을 꾸준히 받고 싶은 사람에게 권할 것을 골라라.
- people: {who} 6~8명. {mix}
- companies: {what} 5~7곳. {mix}
- topics: 이 분야 사람들이 특히 궁금해하는 세부 주제 5~8개, 각 8자 이내.{extra}
people·companies 각 항목:
  {{"name": "한국어 표기", "why": "15자 이내 한 줄", "region": "kr" 또는 "world",
   "x": "X 계정 handle (확실하지 않으면 빈 문자열)", "site": "공식 사이트 도메인 (기업만, 모르면 빈 문자열)",
   "news": "한국어 뉴스 검색어 — 이름 (동명이인이 있으면 분야 단어를 붙여)",
   "news_en": "영어(원어) 뉴스 검색어 — 해외 사람·기업만, 국내면 빈 문자열"}}
출력: {{"people": [...], "companies": [...], "topics": ["..."]{extra_out}}}"""
WHO = {"overview": "이 분야 흐름을 잘 정리해 전하는 사람 (전문 기자·분석가·업계 리더)",
       "target": "고른 대상의 당사자 (선수·감독, 창업자·경영진 등)와 그 소식을 잘 전하는 기자",
       "both": "고른 대상의 당사자와, 이 분야 흐름을 잘 정리하는 기자·분석가를 섞어서"}
WHAT = {"overview": "이 분야를 대표하는 기업·단체·협회·기관", "target": "고른 대상(기업·팀·브랜드)과 관련 단체", "both": "고른 대상과 이 분야 대표 기업·단체"}
EXTRA = """
- media: 이 분야 전문 매체·뉴스레터 3~5곳 {{"name": "...", "why": "15자 이내", "region": "kr 또는 world", "site": "도메인"}}
- searches: 이 분야 흐름을 받을 뉴스 검색어 2~3개 (예: "반도체 업계", "HBM"). 너무 넓은 한 단어는 피한다"""

FINAL = """분야: {field} / 범위: {scope} / 보는 방식: {lens}
고른 것: {answers}
고른 사람: {people}
고른 기업·단체: {companies}
특히 궁금한 것: {topics}
이 사람의 아침 브리핑에서 이 분야 소식을 고를 기준을 만들어라.
출력: {{"name": "관심사 이름 12자 이내", "tag": "짧은 태그 6자 이내", "summary": "한 줄 설명 30자 이내",
 "criteria": ["고르는 기준 문장 3~5개 — 무엇을 먼저, 무엇을 버리는지. 같은 소식은 카드 하나, 공식 발표 전은 「~라는 보도」"],
 "discovery": "새로 볼 만한 사람을 고를 기준 한 문장"}}"""
SCOPES = {"kr": "한국어 소식만", "world": "해외 소식도 함께"}
MIX = {"kr": "국내 위주로.", "world": "해외와 국내를 섞어서."}


def _short(s, n):
    s = " ".join(str(s or "").split())
    return s[:n]


def _entity(e, kind):
    """AI가 준 사람·기업 한 줄 정리. 이상한 값은 비운다 (확인 단계에서 다시 본다)."""
    if not isinstance(e, dict) or not _short(e.get("name"), 30):
        return None
    x = str(e.get("x") or "").strip().lstrip("@")
    site = str(e.get("site") or "").strip()
    name = _short(e["name"], 30)
    return {"kind": kind, "name": name, "why": _short(e.get("why"), 24),
            "region": "kr" if e.get("region") == "kr" else "world",
            "x": x if HANDLE_RE.match(x) else "", "site": site if kind in ("company", "media") and SITE_RE.match(site) else "",
            "news": _short(e.get("news"), 40) or name, "news_en": _short(e.get("news_en"), 40)}


def answers_text(answers):
    """{칸: [보기 …]} → "범위: 해외축구·국가대표 / 대상: 토트넘" (AI 지시문·브리핑 기준에 쓴다)."""
    return " / ".join(f"{k}: {'·'.join(v)}" for k, v in (answers or {}).items() if v) or "(없음)"


def where(context):
    """분류표에서 고른 자리 → 지시문 한 줄 (「축구」만으로는 넓으니 「스포츠 › 축구」로 좁힌다)."""
    return f" (분류: {_short(context, 60)})" if context else ""


def with_persona(domain, task):
    """분야 페르소나(popme/domains.py)가 있으면 「이 사람의 눈으로」 블록을 지시문 앞에."""
    from popme import domains
    text = domains.brief(domain, task) if domain else ""
    return f"{text}\n\n" if text else ""


def suggest(cfg, field, scope, lens=None, answers=None, headlines=None, context="", domain=None):
    """사람·기업·궁금한 것 보기: {"people", "companies", "topics", "media", "searches"}. AI가 실패하면 빈 목록 (직접 입력만).
    lens(보기 방식)가 업계 흐름이면 정리하는 사람·대표 기업 + 전문 매체·분야 검색어, 대상 위주면 고른 대상의 당사자."""
    lens = lens if lens in LENSES else "target"
    if domain and not domain.get("checked"):  # 질문 때 뒤에서 시작한 사실 확인을 여기서 받는다 (사람·기관 추천엔 확인된 사실이 필요)
        from popme import fieldcard
        domain = fieldcard.verified(field) or domain
    given = ""
    if answers:
        given += f"보는 방식: {LENSES[lens]} / 고른 것: {answers_text(answers)}\n"
    if headlines:
        given += "최근 1주 헤드라인 (지금 상황의 근거):\n" + "\n".join(f"- {h}" for h in headlines[:15]) + "\n"
    flow = lens in ("overview", "both")
    prompt = SUGGEST.format(persona=with_persona(domain, "people"), field=_short(field, 40), where=where(context), scope=SCOPES.get(scope, SCOPES["world"]), mix=MIX.get(scope, MIX["world"]),
                            context=given, who=WHO[lens], what=WHAT[lens], extra=EXTRA if flow else "",
                            extra_out=', "media": [...], "searches": ["..."]' if flow else "")
    empty = {"people": [], "companies": [], "topics": [], "media": [], "searches": []}
    try:
        raw = briefing.parse_json(llm.ask(cfg, SYSTEM, prompt, timeout=180))
    except Exception:
        log.exception("관심사 후보 받기 실패")
        return {**empty, "error": "AI가 지금 답하지 못했어요"}
    out = {k: [x for x in (_entity(e, kind) for e in (raw.get(k) or [])[:8]) if x]
           for k, kind in (("people", "person"), ("companies", "company"), ("media", "media"))}
    out["media"] = [m for m in out["media"] if m["site"]][:5]  # 매체는 사이트가 있어야 RSS를 찾는다
    out["topics"] = list(dict.fromkeys(_short(t, 10) for t in (raw.get("topics") or [])[:8] if _short(t, 10)))
    out["searches"] = list(dict.fromkeys(_short(q, 30) for q in (raw.get("searches") or [])[:3] if _short(q, 30))) if flow else []
    out["domain"] = domain  # 확인된 카드로 바뀌었으면 화면이 이걸 들고 있다가 저장
    return out


# ---------------------------------------------------------------- 보는 방식 → 분야별 질문 (설계 4-6)

def research(field, limit=HEADLINES, days=7):
    """최근 days일 헤드라인 (구글 뉴스, 키 없음). 질문·후보를 지금 상황에 맞추는 근거, 분야 카드 사실 확인(fieldcard)의 근거. 실패하면 []."""
    loc = "world" if re.fullmatch(r"[\x00-\x7f]+", field.strip()) else "kr"
    url = catalog.NEWS_URL.replace("when:2d", f"when:{int(days)}d").format(q=urllib.parse.quote_plus(field.strip()), loc=catalog.NEWS_LOC[loc])
    try:
        return [i["title"] for i in rss.peek(url, limit=limit)]
    except Exception:
        log.warning("최근 소식 조사 실패: %s", field, exc_info=True)
        return []


def _option(o):
    if isinstance(o, str):
        o = {"label": o}
    if not isinstance(o, dict):
        return None
    label = _short(o.get("label"), 20)
    if not label or label in ("상관없어요", "직접 입력", "기타"):
        return None
    return {"label": label, "region": "kr" if o.get("region") == "kr" else "world", "en": _short(o.get("en"), 40)}


def _options(xs):
    out, seen = [], set()
    for o in (xs or [])[:12]:
        o = _option(o)
        if o and o["label"] not in seen:
            seen.add(o["label"])
            out.append(o)
    return out[:8]


def fixed_questions(lens, kind="기타"):
    """AI가 실패하거나 형식이 깨졌을 때: 칸 이름만 있는 질문 (직접 입력만)."""
    words = TYPES.get(kind, TYPES["기타"])
    return [{"slot": s, "text": f"{words[s]}이 있나요?" if s == "대상" else f"어떤 {words[s].split('(')[0]}?",
             "multi": True, "options": [], "options_by": {}} for s in SLOTS[lens]]


def clean_questions(raw, lens):
    """AI가 준 질문 검사: 유형, 칸 순서는 틀대로(빠지면 고정 질문으로 채움), 보기 정리, 묻지 않는 것 버리기."""
    kind = raw.get("type") if raw.get("type") in TYPES else "기타"
    got = {}
    for q in raw.get("questions") or []:
        if not isinstance(q, dict) or q.get("slot") not in SLOTS[lens] or q["slot"] in got:
            continue
        text = _short(q.get("text"), 30)
        if not text or NO_ASK.search(text):
            continue
        opts = _options(q.get("options"))
        if NO_ASK.search(" ".join(o["label"] for o in opts)):
            continue
        by = {}
        if q["slot"] == "대상" and isinstance(q.get("options_by"), dict):
            by = {_short(k, 20): _options(v) for k, v in list(q["options_by"].items())[:8] if _short(k, 20)}
            by = {k: v for k, v in by.items() if v}
        got[q["slot"]] = {"slot": q["slot"], "text": text, "multi": q.get("multi") is not False, "options": opts, "options_by": by}
    fixed = {q["slot"]: q for q in fixed_questions(lens, kind)}
    return {"type": kind, "questions": [got.get(s) or fixed[s] for s in SLOTS[lens]]}


def check_options(questions, scope="world"):
    """「대상」 보기는 뉴스 검색으로 기사가 나오는 것만 남긴다 (지어낸 팀·기업 거르기). 한국어로 없으면 원어 이름으로."""
    def alive(o):
        e = {"name": o["label"], "news": o["label"], "news_en": o.get("en"), "region": o["region"]}
        return news_count({**e, "region": "kr"}, "kr") > 0 or (bool(o.get("en")) and news_count(e, "world") > 0)
    for q in questions:
        if q["slot"] != "대상":
            continue
        groups = [q["options"], *q["options_by"].values()]
        flat = [o for g in groups for o in g]
        with ThreadPoolExecutor(max_workers=8) as pool:
            ok = {o["label"] for o, good in zip(flat, pool.map(alive, flat)) if good}
        q["options"] = [o for o in q["options"] if o["label"] in ok]
        q["options_by"] = {k: [o for o in v if o["label"] in ok] for k, v in q["options_by"].items()}
        q["options_by"] = {k: v for k, v in q["options_by"].items() if v}
    return questions


def questions(cfg, field, lens, heads=None, context="", domain=None):
    """보는 방식 → 그 분야의 질문: {type, questions, headlines}. 최근 헤드라인을 근거로 주고, 받은 질문을 검사한다.
    heads: 화면이 먼저 받아 둔 헤드라인 (기다리는 동안 「읽는 중」으로 보여 준다). 없으면 여기서 받는다."""
    lens = lens if lens in LENSES else "target"
    heads = research(field) if heads is None else [str(h)[:200] for h in heads][:HEADLINES]
    words = "\n".join(f"  - {s}" for s in SLOTS[lens])
    if domain is None:  # 새 분야: 프로필 작가가 이 분야를 오래 본 사람을 먼저 그린다 (최근 헤드라인을 사실의 근거로)
        from popme import domains, fieldcard
        domain = domains.write(cfg, field, context, heads)
        if domain:  # 사실 확인은 뒤에서 — 사용자가 질문에 답하는 동안 (③ 대상 추천에서 받아 쓴다)
            fieldcard.start(cfg, field, domain, context)
    prompt = QUESTIONS.format(persona=with_persona(domain, "questions"), field=_short(field, 40), where=where(context), lens=LENSES[lens], headlines="\n".join(f"- {h}" for h in heads) or "(없음)",
                              types=" / ".join(f"{k}({v['범위']}·{v['대상']})" for k, v in TYPES.items()), slots=words)
    try:
        raw = briefing.parse_json(llm.ask(cfg, SYSTEM, prompt, timeout=180))
        out = clean_questions(raw, lens)
    except Exception:
        log.exception("관심사 질문 만들기 실패 (고정 질문으로)")
        out = {"type": "기타", "questions": fixed_questions(lens), "error": "AI가 지금 답하지 못했어요"}
    out["questions"] = check_options(out["questions"])
    out["headlines"] = heads
    out["domain"] = domain  # 화면이 들고 있다가 후보 받기·저장에 넘긴다
    return out


def scope_from(questions_, answers):
    """한국어/해외는 묻지 않고 고른 보기의 region으로: 해외 보기를 하나라도 고르면 해외도, 아무것도 안 골랐으면 해외도."""
    regions = {}
    for q in questions_ or []:
        for o in q.get("options", []) + [o for v in q.get("options_by", {}).values() for o in v]:
            regions[o["label"]] = o["region"]
    picked = [regions.get(a) for v in (answers or {}).values() for a in v if a in regions]
    return "kr" if picked and all(r == "kr" for r in picked) else "world"


def finalize(cfg, field, scope, people, companies, topics, lens=None, answers=None):
    """⑥의 이름·태그·기준 문장. AI가 실패하면 정해진 문장으로."""
    names = lambda xs: ", ".join(x["name"] for x in xs) or "(없음)"
    prompt = FINAL.format(field=_short(field, 40), scope=SCOPES.get(scope, SCOPES["world"]), people=names(people),
                          lens=LENSES.get(lens, "특정 대상 위주"), answers=answers_text(answers),
                          companies=names(companies), topics="·".join(topics) or "(없음)")
    base = {"name": _short(field, 12), "tag": _short(field, 6), "summary": f"{_short(field, 20)} 소식",
            "criteria": [f"{_short(field, 20)}에서 고른 사람·기업의 공식 발표와 중요한 변화를 먼저 고른다.",
                         "같은 소식은 여러 기사라도 카드 하나로, 공식 발표 전 소식은 「~라는 보도」로 쓴다.",
                         "광고·홍보·가십은 버린다."],
            "discovery": f"{_short(field, 20)} 소식을 꾸준히 전하는 사람"}
    try:
        raw = briefing.parse_json(llm.ask(cfg, SYSTEM, prompt, timeout=180))
    except Exception:
        log.exception("관심사 기준 만들기 실패 (정해진 문장으로)")
        return base
    crit = [_short(c, 120) for c in (raw.get("criteria") or []) if _short(c, 120)][:5]
    return {"name": _short(raw.get("name"), 12) or base["name"], "tag": _short(raw.get("tag"), 6) or base["tag"],
            "summary": _short(raw.get("summary"), 30) or base["summary"],
            "criteria": crit if len(crit) >= 2 else base["criteria"],
            "discovery": _short(raw.get("discovery"), 80) or base["discovery"]}


# ---------------------------------------------------------------- 확인 (AI가 말한 것을 그대로 믿지 않는다)

def x_exists(handle):
    """X 계정이 있나 — 로그인 없이 oEmbed로 (있으면 200, 없으면 404). 네트워크 문제면 None."""
    url = "https://publish.x.com/oembed?url=" + urllib.parse.quote(f"https://x.com/{handle}", safe="")
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=12).close()
        return True
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except OSError:
        return None


def _recent(items):
    dated = [i["published"] for i in items if i.get("published")]
    if not dated:
        return bool(items)
    return max(dated) >= (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat()


def find_feed(site):
    """기업 공식 사이트의 RSS를 정해진 규칙으로 찾는다: 첫 화면의 <link rel=alternate> → 흔한 주소들. 열어서 최근 글이 있는 것만."""
    base = site if site.startswith("http") else "https://" + site
    base = base.rstrip("/")
    cands = []
    try:
        with urllib.request.urlopen(urllib.request.Request(base, headers={"User-Agent": UA}), timeout=TIMEOUT) as r:
            page = r.read(600_000).decode("utf-8", "ignore")
        for tag in re.findall(r"<link\b[^>]*>", page, re.I):
            if re.search(r"rel=[\"']?alternate", tag, re.I) and re.search(r"(rss|atom)\+xml", tag, re.I):
                m = re.search(r"href=[\"']([^\"']+)", tag, re.I)
                if m:
                    cands.append(urllib.parse.urljoin(base + "/", m.group(1)))
    except OSError:
        pass
    cands = list(dict.fromkeys(cands + [base + p for p in FEED_PATHS]))

    def ok(url):
        try:
            return _recent(rss.peek(url, timeout=TIMEOUT))
        except Exception:
            return False
    with ThreadPoolExecutor(max_workers=len(cands)) as pool:  # 후보를 한꺼번에 열어 보고 앞 순서대로 고른다
        return next((u for u, good in zip(cands, pool.map(ok, cands)) if good), None)


def news_count(e, scope):
    try:
        return len(rss.peek(catalog.news_feed({**e, "news": e.get("news") or e["name"]}, scope)["url"], limit=100))
    except Exception:
        return 0


def verify(entities, scope):
    """사람·기업마다 실제로 받을 수 있는 것: {name, kind, x: "ok"|"none"|"unknown"|"", feed: 주소|None, news: 건수}."""
    def one(e):
        if e.get("kind") == "media":  # 전문 매체: 공식 RSS가 있어야 받는다
            return {**e, "x_status": "", "feed": find_feed(e["site"]) if e.get("site") else None, "news_count": 0}
        if e.get("kind") == "search":  # 분야 검색어
            return {**e, "x_status": "", "feed": None, "news_count": news_count(e, scope)}
        x = ""
        if e.get("x"):
            ok = x_exists(e["x"])
            x = "ok" if ok else "none" if ok is False else "unknown"
        feed = find_feed(e["site"]) if e.get("kind") == "company" and e.get("site") else None
        return {**e, "x_status": x, "feed": feed, "news_count": news_count(e, scope)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, entities))


# ---------------------------------------------------------------- 저장

def _slug(field):
    return re.sub(r"[^0-9A-Za-z가-힣]+", "-", field).strip("-").lower()[:24] or "interest"


def _q(v):
    from popme.setup_window import _literal
    return _literal(v)


def save_custom(field, scope, verified, topics, final, lens=None, kind=None, answers=None, domain=None, taste=None):
    """새 관심사 → DATA_DIR/profiles/custom-<이름>.toml. 확인된 것만 담는다. 반환: 관심사 id.
    taste: ④ 취향 문답 결과 {summary, likes, less} → [taste] (브리핑 지시문의 「취향」 줄)."""
    pid = f"custom-{_slug(field)}"
    lines = [f"# 관심사 설정에서 만든 관심사 ({datetime.now():%Y-%m-%d}). 「{field}」",
             f"id = {_q(pid)}", f"name = {_q(final['name'])}", f"tag = {_q(final['tag'])}",
             f"summary = {_q(final['summary'])}", 'status = "ready"', f"scope = {_q(scope)}",
             "criteria = " + _q("\n".join(f"- {c}" for c in final["criteria"])),
             f"discovery = {_q(final['discovery'])}", f"topics = {_q(list(topics))}",
             f"lens = {_q(lens if lens in LENSES else 'target')}", f"type = {_q(kind if kind in TYPES else '기타')}",
             "", "[answers]                     # 관심사 설정에서 고른 것 (칸 → 보기)"]
    lines += [f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(list(v), ensure_ascii=False)}" for k, v in (answers or {}).items() if v]
    if taste and (taste.get("summary") or taste.get("likes")):
        lines += ["", "[taste]                       # ④ 취향 문답: 읽고 싶다고 누른 헤드라인에서",
                  f"summary = {_q(taste.get('summary', ''))}", f"likes = {_q(list(taste.get('likes', [])))}",
                  f"less = {_q(list(taste.get('less', [])))}"]
    lines += ["", "[caps]",
             f'"{catalog.NEWS_GROUP}" = 20', '"공식 발표" = 10', '"실사용 검증" = 10', ""]
    for e in verified:  # 전문 매체 (확인된 RSS만), 분야 검색어 (기사가 나오는 것만)
        if e.get("kind") == "media" and e.get("feed"):
            lines += ["[[media]]", f"name = {_q(e['name'])}", f"why = {_q(e.get('why', ''))}", f"region = {_q(e['region'])}",
                      f"feeds = {_q([e['feed']])}", ""]
        if e.get("kind") == "search" and e.get("news_count"):
            lines += ["[[searches]]", f"name = {_q(e['name'])}", f"region = {_q(e['region'])}", f"news = {_q(e['name'])}", ""]
    for kind, key in (("person", "people"), ("company", "companies")):
        for e in verified:
            if e["kind"] != kind:
                continue
            lines += [f"[[{key}]]", f"name = {_q(e['name'])}", f"why = {_q(e.get('why', ''))}", f"region = {_q(e['region'])}"]
            if e.get("x_status") == "ok":
                lines.append(f"x = {_q(e['x'])}")
            if e.get("news_count"):
                lines.append(f"news = {_q(e.get('news') or e['name'])}")
                if e.get("news_en"):
                    lines.append(f"news_en = {_q(e['news_en'])}")
            if e.get("feed"):
                lines.append(f"feeds = {_q([e['feed']])}")
            lines.append("")
    text = "\n".join(lines)
    tomllib.loads(text)  # 깨진 파일은 쓰지 않는다
    catalog.CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    (catalog.CUSTOM_DIR / f"{pid}.toml").write_text(text, encoding="utf-8", newline="\n")
    if domain:  # 이 관심사의 분야 페르소나 — 다음에 고치기·취향 문답에서 다시 쓴다
        from popme import domains
        domains.save_local(pid, domains.clean(domain, pid))
    catalog.reload()
    return pid


def save_picks(pid, scope, people, companies, topics, extra=()):
    """미리 만든 관심사에서 고친 것 → config.toml [news.picks.<id>] (사용자 몫만, 주석·다른 설정은 그대로)."""
    from popme.setup_window import base_text, set_value
    text = base_text()
    sec = f"news.picks.{pid}"
    for k, v in (("scope", scope), ("people", list(people)), ("companies", list(companies)), ("topics", list(topics)),
                 ("extra", [{k2: e[k2] for k2 in ("kind", "name", "why", "x", "region", "news", "news_en") if e.get(k2)} for e in extra])):
        text = set_value(text, sec, k, v, before=("llm",))
    tomllib.loads(text)
    config.CONFIG_PATH.write_text(text, encoding="utf-8", newline="\n")


def preset_view(pid, cfg):
    """미리 만든 관심사의 보기 (③④⑤가 체크된 채로): 고친 적이 있으면 그 값으로."""
    p = next(x for x in catalog.profiles() if x["id"] == pid)
    pk = catalog.picks(cfg, pid)
    chosen = {e["name"] for e in catalog.entities(cfg, p)}
    view = lambda key, kind: [{**e, "kind": kind, "on": e["name"] in chosen} for e in p.get(key, []) + (p.get("media", []) if key == "companies" else [])]
    used = set(catalog.uses(cfg, p))
    target = p.get("target")
    chosen_subs = set(catalog.subs(cfg, p))
    flat = {o["label"] for o in catalog.target_options(p)}
    opt = lambda o: {"label": o["label"], "on": o["label"] in used}
    from popme import domains
    domain = domains.load(pid) if not p.get("custom") else None  # 분야 페르소나가 있는 묶음은 ①전반 ②국내외 ③대상 ④취향
    return {"id": pid, "name": p["name"], "tag": p["tag"], "scope": catalog.scope(cfg, p), "domain": domain,
            "taste": {"summary": pk.get("taste", ""), "likes": pk.get("likes", []), "less": pk.get("less", [])} if "taste" in pk else None,
            "subs": [{**s, "on": s["label"] in chosen_subs} for s in p.get("subs", [])],
            "target": {"text": target["text"], "options": [opt(o) for o in target.get("options", [])]
                                                 + [{"label": u, "on": True, "added": True} for u in used if u not in flat],
                       "options_by": {k: [opt(o) for o in v] for k, v in target.get("options_by", {}).items()}}
            if target else None,
            "people": view("people", "person") + [{"kind": "person", **e, "on": True, "added": True}
                                                  for e in pk.get("extra", []) if e.get("kind", "person") == "person"],
            "companies": view("companies", "company") + [{**e, "on": True, "added": True}
                                                         for e in pk.get("extra", []) if e.get("kind") == "company"],
            "topics": [{"name": t, "on": t in pk["topics"] if "topics" in pk else True} for t in p.get("topics", [])]}
