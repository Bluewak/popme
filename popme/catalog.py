"""소식 출처 카탈로그 — 무엇을 받을지 정한다.

popme/feeds.toml(출처 정의, id) + popme/profiles/*.toml(관심사: 출처·기준·상한) + config.toml [news](고른 관심사·끈 출처·조정)
+ config.toml [[rss]](내가 더한 출처) → 「지금 받을 출처」 목록과 묶음별 상한.
예전 config.toml(관심사 없이 [[rss]]만)은 migrate()로 한 번 옮긴다. 설계: docs/INTERESTS-DESIGN.md
"""
import re
import tomllib
from datetime import date
from functools import lru_cache

from popme import config

FEEDS_PATH = config.ROOT / "popme" / "feeds.toml"
PROFILES_DIR = config.ROOT / "popme" / "profiles"
NOTICE = config.DATA_DIR / "news-migrated.txt"  # 예전 config를 옮겼다는 알림 (한 번 보이고 지운다)
EXTRA_CAP = 15                       # 내가 더한 출처의 묶음이 관심사 상한표에 없을 때
TOTAL_CAP = {0: 140, 1: 140, 2: 200}  # 고른 관심사 수 → 브리핑에 넘길 RSS 전체 상한 (3개 이상은 240)
# 예전 config(관심사 없음)의 묶음별 상한 — 옮기기 전까지만 쓴다
LEGACY_CAPS = {"공식 변경내역": 40, "공식 발표": 30, "실사용 검증": 15, "새 오픈소스·화제": 20, "한국 커뮤니티": 20}


@lru_cache(maxsize=1)
def feeds():
    """출처 id → 정의 (id·name·url·group·max_chars·skip …)."""
    return {fid: {**f, "id": fid} for fid, f in tomllib.loads(FEEDS_PATH.read_text(encoding="utf-8"))["feed"].items()}


@lru_cache(maxsize=1)
def _profiles():
    out = [tomllib.loads(p.read_text(encoding="utf-8")) for p in sorted(PROFILES_DIR.glob("*.toml"))]
    return sorted(out, key=lambda p: p.get("order", 99))


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
        for e in p.get("feeds", []):
            fid = e["id"]
            if fid in off or (e.get("default", True) is False and fid not in on) or fid not in cat:
                continue
            f = acc.setdefault(fid, {**cat[fid], **tweak.get(fid, {}), "interests": {}})
            filt = e.get("filter")
            f["interests"][p["id"]] = p.get("topic") if filt is True else (filt or None)
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
    for f in active(cfg):
        total.setdefault(f["group"], EXTRA_CAP)
    limit = TOTAL_CAP.get(len(picked), 240)
    s = sum(total.values())
    if s > limit:
        total = {g: max(2, n * limit // s) for g, n in total.items()}
    return total


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
