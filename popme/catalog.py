"""소식 출처 카탈로그 — 무엇을 받을지 정한다.

popme/feeds.toml(출처 정의, id) + popme/profiles/*.toml(관심사: 출처·기준·상한) + config.toml [news](고른 관심사·끈 출처·조정)
+ config.toml [[rss]](내가 더한 출처) → 「지금 받을 출처」 목록과 묶음별 상한.
예전 config.toml(관심사 없이 [[rss]]만)은 migrate()로 한 번 옮긴다. 설계: docs/INTERESTS-DESIGN.md
"""
import re
import tomllib
from datetime import date
from functools import lru_cache
from urllib.parse import quote_plus

from popme import config

FEEDS_PATH = config.ROOT / "popme" / "feeds.toml"
PROFILES_DIR = config.ROOT / "popme" / "profiles"
TAXONOMY_PATH = config.ROOT / "popme" / "taxonomy.toml"  # 분류표: 대분류 → 소분류 (→ 세부)
CUSTOM_DIR = config.DATA_DIR / "profiles"  # 관심사 설정에서 새로 만든 관심사 (custom-*.toml, 내 PC에만)
NOTICE = config.DATA_DIR / "news-migrated.txt"  # 예전 config를 옮겼다는 알림 (한 번 보이고 지운다)
EXTRA_CAP = 15                       # 내가 더한 출처의 묶음이 관심사 상한표에 없을 때
TOTAL_CAP = {0: 140, 1: 140, 2: 200}  # 고른 관심사 수 → 브리핑에 넘길 RSS 전체 상한 (3개 이상은 240)
# 사람·기업 이름으로 받는 뉴스 검색 (docs/INTERESTS-DESIGN.md 4-3). 키 없음, 지금 RSS 모듈로 받는다
NEWS_URL = "https://news.google.com/rss/search?q={q}+when:2d&{loc}"
NEWS_LOC = {"kr": "hl=ko&gl=KR&ceid=KR:ko", "world": "hl=en-US&gl=US&ceid=US:en"}
NEWS_GROUP, NEWS_CAP, NEWS_PER_FEED = "뉴스", 15, 5  # 관심사 하나당 뉴스 상한, 한 사람·기업당 상한 (한 명이 다 차지하지 않게)
# 구글 뉴스에 섞여 드는 엉뚱한 사이트 (제목 끝 「 - 언론사」). 2026-10-19 「삼성전자 HBM」 70건 중 37건이 이것
NEWS_SPAM = ["Calgary Roughnecks", "Vietnam.vn"]  # Vietnam.vn: 기계번역 기사 (2026-10 축구·야구 조사)
# 예전 config(관심사 없음)의 묶음별 상한 — 옮기기 전까지만 쓴다
LEGACY_CAPS = {"공식 변경내역": 40, "공식 발표": 30, "실사용 검증": 15, "새 오픈소스·화제": 20, "한국 커뮤니티": 20}


@lru_cache(maxsize=1)
def feeds():
    """출처 id → 정의 (id·name·url·group·max_chars·skip …)."""
    return {fid: {**f, "id": fid} for fid, f in tomllib.loads(FEEDS_PATH.read_text(encoding="utf-8"))["feed"].items()}


@lru_cache(maxsize=1)
def _profiles():
    out = [tomllib.loads(p.read_text(encoding="utf-8")) for p in sorted(PROFILES_DIR.glob("*.toml"))]
    for p in sorted(CUSTOM_DIR.glob("custom-*.toml")):  # 새로 만든 관심사 — 깨진 파일은 건너뛴다
        try:
            out.append({"order": 100, **tomllib.loads(p.read_text(encoding="utf-8")), "custom": True})
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return sorted(out, key=lambda p: p.get("order", 99))


@lru_cache(maxsize=1)
def _taxonomy():
    return tomllib.loads(TAXONOMY_PATH.read_text(encoding="utf-8")).get("group", [])


def taxonomy():
    """분류표 [{name, items: [{name, profile?, children?}]}]. 묶음(profile)이 아직 없는 id는 지우고 넘긴다
    — 그 소분류는 「AI 질문」 방식으로 새로 만든다."""
    have = {p["id"] for p in profiles()}

    def item(x):
        out = {"name": x["name"]}
        if x.get("profile") in have:
            out["profile"] = x["profile"]
        if x.get("children"):
            out["children"] = [item(c) for c in x["children"]]
        return out
    return [{"name": g["name"], "items": [item(x) for x in g.get("items", [])]} for g in _taxonomy()]


def reload():
    """관심사 파일을 새로 만들거나 고친 뒤 다시 읽게."""
    getattr(_profiles, "cache_clear", lambda: None)()  # 테스트가 _profiles를 바꿔 끼웠을 수도 있다


def profiles(drafts=False):
    """관심사 목록 (order 순). status = "draft"는 drafts일 때만."""
    return [p for p in _profiles() if drafts or p.get("status", "ready") != "draft"]


def news(cfg):
    return cfg.get("news", {})


def chosen(cfg):
    """고른 관심사들 (설정 순서 말고 order 순)."""
    n = news(cfg)
    ids = set(n.get("interests", []))
    return [p for p in profiles(n.get("drafts", False)) if p["id"] in ids]


def picks(cfg, pid):
    """관심사 설정에서 고친 사용자 몫 [news.picks.<id>]: scope·people·companies(체크한 이름)·topics·extra(직접 더한 것)."""
    return news(cfg).get("picks", {}).get(pid, {})


def subs(cfg, p):
    """묶음의 세부(해외축구·K리그 …) 중 고른 것. 고친 적이 없으면 전부. 세부가 없는 관심사면 []."""
    allowed = [s["label"] for s in p.get("subs", [])]
    got = picks(cfg, p["id"]).get("subs")
    return [s for s in got if s in allowed] if got is not None else allowed


def in_subs(item, chosen_subs):
    """출처·사람·기관이 고른 세부에 해당하나 (세부 표시가 없으면 전부에 해당)."""
    return not item.get("subs") or not chosen_subs or bool(set(item["subs"]) & set(chosen_subs))


def target_options(p, chosen_subs=None):
    """대상 보기 전부 — 세부와 상관없는 options + 고른 세부마다의 options_by (같은 이름은 하나)."""
    t = p.get("target", {})
    by = t.get("options_by", {})
    keys = [k for k in by if chosen_subs is None or k in chosen_subs]
    out = {}
    for o in [*t.get("options", []), *(o for k in keys for o in by[k])]:
        out.setdefault(o["label"], o)
    return list(out.values())


def uses(cfg, p):
    """「대상」 질문(쓰는 도구 등)에서 고른 보기 label들. 고친 적이 없으면 설정의 「주로 쓰는 도구」에 이름이 들어 있는 보기."""
    pk = picks(cfg, p["id"])
    if "uses" in pk:
        return list(pk["uses"])
    mine = (cfg.get("user", {}).get("tools") or "").lower()

    def named(part):  # 단어로: 「pi」가 「Copilot」 안에서 걸리지 않게
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(part.strip().lower())}(?![a-z0-9])", mine))
    return [o["label"] for o in target_options(p)
            if any(named(part) for part in o["label"].replace("·", "/").split("/") if part.strip())]


def used_options(cfg, p):
    want = set(uses(cfg, p))
    return [o for o in target_options(p) if o["label"] in want]


def user_tools(cfg):
    """브리핑의 「주로 쓰는 도구」: 설정 값 + 관심사 「대상」 질문에서 고른 것 (같은 이름은 한 번)."""
    names = [t.strip() for t in (cfg.get("user", {}).get("tools") or "").split(",") if t.strip()]
    for p in chosen(cfg):
        names += uses(cfg, p)
    out = list(dict.fromkeys(names))
    return ", ".join(out) or "Claude Code"


def entities(cfg, p):
    """그 관심사에서 받을 사람·기업 [{kind, name, why, x, region, news, feeds}]. 고친 적이 없으면 기본 체크된 것.
    「대상」 질문에서 고른 보기의 회사(예: Cursor를 쓰면 Cursor)는 기본 꺼짐이어도 넣는다."""
    pk = picks(cfg, p["id"])
    used = used_options(cfg, p)
    by_target = {c for o in used for c in o.get("companies", [])}
    chosen_subs = subs(cfg, p)
    out = []
    for kind, key in (("person", "people"), ("company", "companies"), ("media", "media"), ("search", "searches")):
        names = pk.get(key)
        for e in p.get(key, []):
            keep = (e["name"] in names if names is not None else e.get("default", True) is not False) or e["name"] in by_target
            if keep and (e["name"] in by_target or in_subs(e, chosen_subs)):
                out.append({**e, "kind": kind})
    have = {e["name"] for e in out}
    for o in used:  # 고른 대상(응원 팀·아티스트 …) 자체도 사람·기관처럼 X·뉴스로 받는다
        if o["label"] not in have and (o.get("x") or o.get("news")):
            out.append({"kind": "target", "name": o["label"], "why": "고른 대상", "region": o.get("region", "world"),
                        "x": o.get("x", ""), "news": o.get("news", ""), "news_en": o.get("news_en", "")})
    for e in pk.get("extra", []):  # 직접 더한 사람·기업 (kind가 없으면 사람)
        if e.get("name"):
            out.append({"kind": "person", **e})
    return out


def scope(cfg, p):
    """한국어만(kr) / 해외도(world). 세부가 있는 묶음은 묻지 않고 고른 세부로: 국내 세부만 골랐으면 kr."""
    if p.get("subs"):
        regions = {s["label"]: s.get("region", "world") for s in p["subs"]}
        chosen_subs = subs(cfg, p)
        return "kr" if chosen_subs and all(regions.get(s) == "kr" for s in chosen_subs) else "world"
    return picks(cfg, p["id"]).get("scope") or p.get("scope") or "world"


def news_feed(e, scope_):
    """사람·기업 하나의 뉴스 검색 출처. 범위가 「한국어만」이거나 국내 사람·기업이면 한국어 기사, 아니면 영어 기사."""
    loc = "kr" if scope_ == "kr" or e.get("region") == "kr" else "world"
    q = e.get("news_en") if loc == "world" and e.get("news_en") else e["news"]  # 해외 기사는 원어 이름으로 찾아야 나온다
    return {"id": f"news:{loc}:{q}", "name": f"뉴스 · {e['name']}" + ("" if loc == "kr" else " (해외)"),
            "url": NEWS_URL.format(q=quote_plus(q), loc=NEWS_LOC[loc]), "group": NEWS_GROUP, "max_chars": 200,
            "cap": NEWS_PER_FEED, "skip": " - (" + "|".join(re.escape(x) for x in NEWS_SPAM) + ")$"}


def entity_feeds(cfg, p):
    """사람·기업에서 나오는 출처: 뉴스 검색(news) + 확인된 공식 RSS(feeds)."""
    sc = scope(cfg, p)
    for e in entities(cfg, p):
        if e.get("news"):
            yield news_feed(e, sc)
        for url in e.get("feeds", []):  # 기업 공식 소식은 「공식 발표」, 전문 매체는 「실사용 검증」(분석·해설) 묶음
            media = e.get("kind") == "media"
            yield {"id": f"site:{url}", "name": e["name"] if media else f"{e['name']} 공식", "url": url,
                   "group": "실사용 검증" if media else "공식 발표", "max_chars": 600}


def x_accounts(cfg):
    """고른 관심사의 사람·기업 중 X 계정이 확인된 것 [{handle, name, group, focus, interest}]. 꺼 둔 것(off의 x:handle)은 뺀다."""
    off = {o.lower() for o in news(cfg).get("off", [])}
    out = {}
    for p in chosen(cfg):
        for e in entities(cfg, p):
            h = (e.get("x") or "").lstrip("@")
            if h and f"x:{h}".lower() not in off:
                out.setdefault(h.lower(), {"handle": h, "name": e["name"], "group": f"관심사 · {p['tag']}",
                                          "focus": e.get("why", ""), "interest": p["id"]})
    return list(out.values())


def active(cfg):
    """지금 받을 출처 목록. 각 출처에 interests({관심사 id: 거를 topic 또는 None})·topic(받기 전체 기준)을 붙인다.
    - 꺼 둔 출처(off)는 빼고, 기본은 꺼진 출처(default = false)는 켠 것(on)만
    - 같은 출처를 여러 관심사가 쓰면 하나로: 하나라도 거르지 않으면 전부, 모두 거르면 그 규칙들 중 하나라도 맞는 글
    - 조정(tweak)은 앱 값 위에 덮는다 (skip·max_chars)"""
    if "news" not in cfg:  # 옮기기 전 예전 config — [[rss]] 그대로
        return [{**r, "id": r.get("name"), "interests": {}} for r in cfg.get("rss", [])]
    n = news(cfg)
    off, on, tweak = set(n.get("off", [])), set(n.get("on", [])), n.get("tweak", {})
    cat, acc = feeds(), {}
    for p in chosen(cfg):
        turned = {fid for o in used_options(cfg, p) for fid in o.get("feeds", [])}  # 「대상」에서 고른 도구의 출처는 켠다
        chosen_subs = subs(cfg, p)
        for e in p.get("feeds", []):
            fid = e["id"]
            if fid in off or (e.get("default", True) is False and fid not in on and fid not in turned) or fid not in cat:
                continue
            if fid not in turned and not in_subs(e, chosen_subs):  # 고른 세부(K리그 …)의 출처만
                continue
            f = acc.setdefault(fid, {**cat[fid], **tweak.get(fid, {}), "interests": {}})
            filt = e.get("filter")
            f["interests"][p["id"]] = p.get("topic") if filt is True else (filt or None)
        for f in entity_feeds(cfg, p):  # 사람·기업 이름으로 받는 뉴스·공식 RSS (거르지 않음 — 검색어가 이미 좁다)
            if f["id"] not in off:
                acc.setdefault(f["id"], {**f, "interests": {}})["interests"][p["id"]] = None
    picked = [p["id"] for p in chosen(cfg)]
    for r in cfg.get("rss", []):  # 내가 더한 출처
        if not r.get("url"):
            continue
        ints = [r["interest"]] if r.get("interest") in picked else picked
        topic = r.get("topic") or None
        acc[f"user:{r.get('name')}"] = {**r, "id": f"user:{r.get('name')}", "group": r.get("group", "실사용 검증"),
                                        "interests": {i: topic for i in ints} or {"": topic}}
    out = list(acc.values())
    for f in out:
        filters = list(f["interests"].values())
        f["topic"] = None if not filters or any(t is None for t in filters) else "+".join(dict.fromkeys(t for x in filters for t in x.split("+")))
    return out


def caps(cfg):
    """묶음 → 브리핑에 넘길 상한. 고른 관심사들의 상한을 더하고, 전체 상한을 넘으면 같은 비율로 줄인다."""
    if "news" not in cfg:
        return dict(LEGACY_CAPS)
    total = {}
    picked = chosen(cfg)
    for p in picked:
        for g, n in p.get("caps", {}).items():
            total[g] = total.get(g, 0) + n
        if NEWS_GROUP not in p.get("caps", {}) and any(e.get("news") for e in entities(cfg, p)):
            total[NEWS_GROUP] = total.get(NEWS_GROUP, 0) + NEWS_CAP
    for f in active(cfg):
        total.setdefault(f["group"], EXTRA_CAP)
    limit = TOTAL_CAP.get(len(picked), 240)
    s = sum(total.values())
    if s > limit:
        total = {g: max(2, n * limit // s) for g, n in total.items()}
    return total


def save_interests(ids):
    """고른 관심사를 config.toml [news] interests에 바로 저장 (주석·다른 설정은 그대로). 모르는 id는 버린다.
    출처 목록은 다음 수집부터 바뀐다. 반환: 저장한 id들."""
    from popme.setup_window import base_text, set_value
    known = {p["id"] for p in profiles(drafts=True)}
    ids = [i for i in dict.fromkeys(ids) if i in known]
    text = set_value(base_text(), "news", "interests", ids, before=("llm",))
    tomllib.loads(text)  # 깨진 설정은 쓰지 않는다
    config.CONFIG_PATH.write_text(text, encoding="utf-8", newline="\n")
    return ids


def tags():
    """관심사 태그 → id (태그 고치기에서 허용할 값)."""
    return {p["tag"]: p["id"] for p in profiles(drafts=True)}


# ---------------------------------------------------------------- 예전 config 옮기기

def _norm(url):
    u = (url or "").strip().replace("http://", "https://").rstrip("/")
    return u.lower()


def needs_migration(cfg):
    return "news" not in cfg and bool(cfg.get("rss"))


def migrate(text, interests=("ai-coding",)):
    """[news] 없이 [[rss]]만 있는 config 텍스트 → (새 텍스트, 알림 문구). 앱 출처와 같은 주소는 지우고(설정이 다르면
    tweak으로), 다른 주소는 「내가 더한 출처」로 남긴다. 고른 관심사는 그동안의 대상이던 「AI 코딩」.
    [[rss]]가 있던 자리에 [news]를 쓴다.
    다시 돌려도 결과가 같다 ([news]가 생기면 needs_migration이 거짓)."""
    from popme.setup_window import _literal
    cfg = tomllib.loads(text)
    by_url = {}
    for fid, f in feeds().items():
        for u in [f["url"], *f.get("old_urls", [])]:
            by_url[_norm(u)] = f
    tweaks, extras = {}, []
    for r in cfg.get("rss", []):
        f = by_url.get(_norm(r.get("url")))
        if not f:
            extras.append({k: v for k, v in r.items()})
            continue
        t = {k: r[k] for k in ("skip", "max_chars") if k in r and r[k] != f.get(k)}
        if t:
            tweaks[f["id"]] = t
    lines = text.split("\n")
    heads = [i for i, l in enumerate(lines) if re.match(r"^\s*\[", l)]
    rss = [i for i in heads if re.match(r"^\s*\[\[\s*rss\s*\]\]", lines[i])]
    start = rss[0]
    while start > 0 and lines[start - 1].lstrip().startswith("#"):  # 바로 위 설명 주석도 같이
        start -= 1
    end = next((i for i in heads if i > rss[-1] and i not in rss), len(lines))
    while end > start and lines[end - 1].lstrip().startswith("#"):  # 다음 표의 설명 주석은 남긴다
        end -= 1
    block = ["# 소식 출처 — 고른 관심사(interests)마다 POPME가 출처를 챙긴다 (목록: popme/feeds.toml, 관심사: popme/profiles/).",
             "# off = 끈 출처 id, on = 기본은 꺼진 출처 중 켠 것, [news.tweak.<id>] = 그 출처만 skip·max_chars 바꾸기. 설정 창에서 고른다",
             "[news]", f"interests = {_literal(list(interests))}", ""]
    for fid, t in tweaks.items():
        block += [f"[news.tweak.{fid}]"] + [f"{k} = {_literal(v)}" for k, v in t.items()] + [""]
    block += ["# 내가 더한 소식 출처 — 형식은 예전과 같다 (name·url·group·max_chars·skip·topic, 관심사를 정하려면 interest = \"…\")"]
    for r in extras:
        block += ["[[rss]]"] + [f"{k} = {_literal(v)}" for k, v in r.items()] + [""]
    lines[start:end] = block + ([""] if not extras else [])
    out = "\n".join(lines)
    tomllib.loads(out)
    kept = len(cfg.get("rss", [])) - len(extras)
    note = (f"소식 출처를 정리했어요: {kept}곳은 이제 POPME가 챙기고, 내가 더한 {len(extras)}곳은 그대로예요." if extras
            else f"소식 출처 {kept}곳을 이제 POPME가 챙겨요.")
    return out, note


def migrate_file():
    """config.toml이 예전 형식이면 백업하고 옮긴다. 옮겼으면 알림 문구, 아니면 None."""
    path = config.CONFIG_PATH
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if not needs_migration(tomllib.loads(text)):
        return None
    new, note = migrate(text)
    backup = path.with_name(f"config.toml.bak-{date.today():%Y%m%d}")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(new, encoding="utf-8", newline="\n")
    NOTICE.write_text(note, encoding="utf-8")  # 다음 브리핑에서 한 번 알린다 (B1-3)
    return note
