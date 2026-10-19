"""관심사 설정 (docs/INTERESTS-DESIGN.md 4장): 고르기·입력 → 범위·사람·기업·궁금한 것 → 묶음 만들기.

AI(설정의 브리핑 AI, 도구 끔)는 후보와 기준 문장만 만든다. AI가 말한 X 계정·사이트 주소는 그대로 믿지 않고
여기서 실제로 열어 확인한 것만 저장한다 (X는 oEmbed로 있는지, 공식 RSS는 정해진 규칙으로 찾아 열어 보고, 뉴스는 검색해 본다).
"""
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

SYSTEM = "너는 소식 출처를 고르는 도우미다. 요청한 JSON 하나만 출력한다. 실존하는 사람·기업만 쓰고, 모르면 적게 쓴다."
SUGGEST = """분야: {field}
범위: {scope}
이 분야 소식을 꾸준히 받고 싶은 사람에게 권할 것을 골라라.
- people: 이 분야 소식을 직접 전하거나 영향력이 큰 사람 6~8명. {mix}
- companies: 기업·단체·구단·기관 5~7곳. {mix}
- topics: 이 분야 사람들이 특히 궁금해하는 세부 주제 5~8개, 각 8자 이내.
people·companies 각 항목:
  {{"name": "한국어 표기", "why": "15자 이내 한 줄", "region": "kr" 또는 "world",
   "x": "X 계정 handle (확실하지 않으면 빈 문자열)", "site": "공식 사이트 도메인 (기업만, 모르면 빈 문자열)",
   "news": "한국어 뉴스 검색어 — 이름 (동명이인이 있으면 분야 단어를 붙여)",
   "news_en": "영어(원어) 뉴스 검색어 — 해외 사람·기업만, 국내면 빈 문자열"}}
출력: {{"people": [...], "companies": [...], "topics": ["..."]}}"""
FINAL = """분야: {field} / 범위: {scope}
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
            "x": x if HANDLE_RE.match(x) else "", "site": site if kind == "company" and SITE_RE.match(site) else "",
            "news": _short(e.get("news"), 40) or name, "news_en": _short(e.get("news_en"), 40)}


def suggest(cfg, field, scope):
    """③④⑤의 보기: {"people": [...], "companies": [...], "topics": [...]}. AI가 실패하면 빈 목록 (직접 입력만)."""
    prompt = SUGGEST.format(field=_short(field, 40), scope=SCOPES.get(scope, SCOPES["world"]), mix=MIX.get(scope, MIX["world"]))
    try:
        raw = briefing.parse_json(llm.ask(cfg, SYSTEM, prompt, timeout=180))
    except Exception:
        log.exception("관심사 후보 받기 실패")
        return {"people": [], "companies": [], "topics": [], "error": "AI가 지금 답하지 못했어요"}
    out = {k: [x for x in (_entity(e, kind) for e in (raw.get(k) or [])[:8]) if x]
           for k, kind in (("people", "person"), ("companies", "company"))}
    out["topics"] = list(dict.fromkeys(_short(t, 10) for t in (raw.get("topics") or [])[:8] if _short(t, 10)))
    return out


def finalize(cfg, field, scope, people, companies, topics):
    """⑥의 이름·태그·기준 문장. AI가 실패하면 정해진 문장으로."""
    names = lambda xs: ", ".join(x["name"] for x in xs) or "(없음)"
    prompt = FINAL.format(field=_short(field, 40), scope=SCOPES.get(scope, SCOPES["world"]), people=names(people),
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


def save_custom(field, scope, verified, topics, final):
    """새 관심사 → DATA_DIR/profiles/custom-<이름>.toml. 확인된 것만 담는다. 반환: 관심사 id."""
    pid = f"custom-{_slug(field)}"
    lines = [f"# 관심사 설정에서 만든 관심사 ({datetime.now():%Y-%m-%d}). 「{field}」",
             f"id = {_q(pid)}", f"name = {_q(final['name'])}", f"tag = {_q(final['tag'])}",
             f"summary = {_q(final['summary'])}", 'status = "ready"', f"scope = {_q(scope)}",
             "criteria = " + _q("\n".join(f"- {c}" for c in final["criteria"])),
             f"discovery = {_q(final['discovery'])}", f"topics = {_q(list(topics))}", "", "[caps]",
             f'"{catalog.NEWS_GROUP}" = 20', '"공식 발표" = 10', ""]
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
    catalog.reload()
    return pid


def save_picks(pid, scope, people, companies, topics, extra=()):
    """미리 만든 관심사에서 고친 것 → config.toml [news.picks.<id>] (사용자 몫만, 주석·다른 설정은 그대로)."""
    from popme.setup_window import base_text, set_value
    text = base_text()
    sec = f"news.picks.{pid}"
    for k, v in (("scope", scope), ("people", list(people)), ("companies", list(companies)), ("topics", list(topics)),
                 ("extra", [{k2: e[k2] for k2 in ("name", "why", "x", "region", "news", "news_en") if e.get(k2)} for e in extra])):
        text = set_value(text, sec, k, v, before=("llm",))
    tomllib.loads(text)
    config.CONFIG_PATH.write_text(text, encoding="utf-8", newline="\n")


def preset_view(pid, cfg):
    """미리 만든 관심사의 보기 (③④⑤가 체크된 채로): 고친 적이 있으면 그 값으로."""
    p = next(x for x in catalog.profiles() if x["id"] == pid)
    pk = catalog.picks(cfg, pid)
    chosen = {e["name"] for e in catalog.entities(cfg, p)}
    view = lambda key, kind: [{**e, "kind": kind, "on": e["name"] in chosen} for e in p.get(key, [])]
    return {"id": pid, "name": p["name"], "tag": p["tag"], "scope": catalog.scope(cfg, p),
            "people": view("people", "person") + [{**e, "kind": "person", "on": True} for e in pk.get("extra", [])],
            "companies": view("companies", "company"),
            "topics": [{"name": t, "on": t in pk["topics"] if "topics" in pk else True} for t in p.get("topics", [])]}
