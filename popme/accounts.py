"""볼 계정 정하기: X 핵심 계정·관심사 계정·발굴로 추가한 계정, 30개가 넘으면 돌아가며 보기 (예전 briefing.py에서 옮김).
수집(jobs)과 브리핑 재료(briefing.build_data)가 함께 쓴다."""
import json
import logging
from datetime import datetime, timedelta, timezone

from popme import catalog, config, discovery
from popme.fileio import atomic_write_text

log = logging.getLogger(__name__)


def core_accounts(cfg, db):
    """볼 X 계정: 내 핵심 계정([x] accounts) + 고른 관심사의 사람·기업 + 발굴로 추가한 계정 (같은 계정은 하나로)."""
    accts = {a["handle"].lower(): a for a in cfg.get("x", {}).get("accounts", [])}
    for a in catalog.x_accounts(cfg):
        accts.setdefault(a["handle"].lower(), a)
    for c in discovery.added(db, "x"):
        accts.setdefault(c["handle"].lower(), {"handle": c["handle"], "name": c["name"] or c["handle"],
                                               "group": "발굴로 추가", "focus": c["reason"]})
    return list(accts.values())


X_VISIT_LIMIT = 30
X_VISITS = config.DATA_DIR / "x-visits.json"  # 계정 페이지를 마지막으로 본 시각 (돌아가며 보기) {handle 소문자: UTC ISO}
# 돌아가며 보는 계정은 며칠에 한 번 열린다 (2026-11: 89개를 16개씩 → 5~6일). 그 사이 글을 이만큼 거슬러 받고 브리핑에도 넣는다.
# 예전엔 수집도 브리핑도 「지난 브리핑 이후」만 봐서 사이 글이 빠졌다 — 2026-11-16 한 번에 226개
# (@sundarpichai의 Gemini 4 Carbon·TPU 발표, @deepseek_ai의 API 공개 등)
X_LOOKBACK = timedelta(days=7)
X_LIST = config.DATA_DIR / "x-list.json"  # 리스트 기록 {"list", "name", "members"·"failed"·"gone": {handle 소문자: ISO}, "src": {handle: direct|interest}, "added": {날짜: 수}}
LIST_ADD_PER_DAY = 30   # 하루에 리스트에 넣고 빼는 계정 수 (합) (사람이 리스트를 처음 만들 때 한자리에서 넣는 정도. X는 「공격적인 리스트 추가」를 금지한다)
LIST_RETRY = timedelta(days=7)  # 넣기·빼기 실패한 계정은 이만큼 뒤에 다시
LIST_QUIET = timedelta(days=5)   # 기록상 멤버인데 리스트에서 이만큼 글이 안 왔으면 진짜 들어 있는지 확인
LIST_RECHECK = timedelta(days=14)  # 같은 계정은 이만큼에 한 번만 확인 (원래 조용한 계정이 매번 걸리지 않게)
LIST_CHECKS = 5                 # 한 번에 확인하는 수
LIST_GRACE = timedelta(days=14)  # 관심사에 딸린 계정이 보는 계정에서 빠진 뒤 리스트에서 빼기까지
LIST_FRESH = timedelta(hours=48)  # 리스트를 이만큼 안에 정상으로 봤을 때만 리스트 멤버를 계정 페이지 방문에서 뺀다
X_LATE_PER_ACCOUNT = 5  # 사이 글은 계정당 반응 큰 이만큼만 (다 넣으면 2026-11-16 브리핑의 X 글이 42개 → 426개)


def _visits():
    try:
        return json.loads(X_VISITS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def x_plan(cfg, db, since, limit=X_VISIT_LIMIT, gaps=None):
    """이번 수집에서 열 X 계정 → (handle들, {handle(소문자): 그 계정 페이지를 어디까지 내려 볼지 UTC ISO}).
    내 핵심 계정([x] accounts)은 늘 보고, 발굴로 추가한 계정·관심사 계정은 남은 자리만큼 오래 안 본 것부터
    (사람 속도는 그대로 두고, 계정을 늘려도 수집 시간이 늘지 않게 — 설계 6-3). 발굴 계정도 돌아가며:
    2026-10-19 발굴 40개가 늘 보는 쪽에 있어서 한 번에 62개를 봤다.
    돌아가며 고른 계정은 지난번에 본 시각까지 내려 본다 (X_LOOKBACK 안에서). 나머지·처음 보는 계정은 since까지.
    본 시각은 실제로 연 뒤에 mark_x_visited가 남긴다 — 로그인이 풀려 못 본 날을 「봤다」로 치지 않게.
    gaps(list): 사이 글이 빠지는 경우를 사람 말로 넣는다 — 한 바퀴가 X_LOOKBACK보다 길 것 같거나, 지난번에 본 지
    X_LOOKBACK이 넘은 계정(그 앞 글은 받지도 브리핑에 넣지도 않는다). 예전엔 조용히 빠졌다 (2026-11-01 → 11-09 같은 9건)."""
    accts = core_accounts(cfg, db)
    mine = {a["handle"].lower() for a in cfg.get("x", {}).get("accounts", [])}
    fixed = [a["handle"] for a in accts if a["handle"].lower() in mine]
    on_list = list_members(cfg)  # 리스트로 매번 받는 계정 — 그 자리를 아직 리스트에 없는 계정에
    rotating = [a["handle"] for a in accts if a["handle"].lower() not in mine and a["handle"].lower() not in on_list]
    room = max(10, limit - len(fixed))
    if len(rotating) <= room:
        return fixed + rotating, {}
    seen = _visits()
    pick = sorted(rotating, key=lambda h: seen.get(h.lower(), ""))[:room]
    lap = -(-len(rotating) // room)  # 하루 한 번 모을 때 한 바퀴 날수
    if gaps is not None and lap > X_LOOKBACK.days:
        gaps.append(f"돌아가며 보는 X 계정이 {len(rotating)}개라 한 바퀴에 {lap}일쯤 걸려요. "
                    f"{X_LOOKBACK.days}일보다 오래된 사이 글은 빠져요 (발굴 계정 정리를 권해요)")
    late = 0
    floor = datetime.now(timezone.utc) - X_LOOKBACK
    s = datetime.fromisoformat(since)
    since_by = {}
    for h in pick:
        try:
            last = datetime.fromisoformat(seen[h.lower()]) - timedelta(hours=1)
        except (KeyError, TypeError, ValueError):
            continue  # 처음 보는 계정
        late += last < floor
        start = max(last, floor)
        if start < s:
            since_by[h.lower()] = start.isoformat()
    if gaps is not None and late:
        gaps.append(f"X 계정 {late}개는 {X_LOOKBACK.days}일 넘게 못 봐서 그 앞 글은 못 받았어요")
    return fixed + pick, since_by


def _list_state(lid):
    try:
        s = json.loads(X_LIST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    if s.get("list") != lid:  # 다른 리스트로 바꿨으면 처음부터
        s = {"list": lid}
    for k in ("members", "failed", "added", "src", "gone", "checked"):
        s.setdefault(k, {})
    return s


def _save_list_state(s):
    try:
        atomic_write_text(X_LIST, json.dumps(s, ensure_ascii=False, indent=1))
    except OSError:
        log.warning("X 리스트 기록 저장 실패", exc_info=True)


def list_members(cfg):
    """리스트에 넣은 계정(소문자) — 단 리스트를 LIST_FRESH 안에 정상으로 봤을 때만. 아니면 빈 집합
    (리스트를 못 읽는 동안 그 계정들을 계정 페이지로라도 보게)."""
    from popme.collectors.x import list_id
    lid = list_id(cfg.get("x", {}).get("list_url"))
    if not lid:
        return set()
    try:
        seen = datetime.fromisoformat(_visits().get(f"list:{lid}") or "")
    except ValueError:
        return set()
    if datetime.now(timezone.utc) - seen > LIST_FRESH:
        return set()
    return set(_list_state(lid)["members"])


def watch_sources(cfg, db):
    """보는 X 계정(소문자) → "direct"(내가 정한 핵심 계정·발굴로 추가한 계정) | "interest"(고른 관심사에 딸린 계정)."""
    src = {a["handle"].lower(): "direct" for a in cfg.get("x", {}).get("accounts", [])}
    for c in discovery.added(db, "x"):
        src.setdefault(c["handle"].lower(), "direct")
    for a in catalog.x_accounts(cfg):
        src.setdefault(a["handle"].lower(), "interest")
    return src


def list_todo(cfg, db, name=None):
    """리스트에 넣고 뺄 계정 → (넣을 handles, 뺄 handles, 리스트 이름). 둘을 합쳐 오늘 남은 몫(LIST_ADD_PER_DAY)만큼, 넣기 먼저.
    - 넣기: 보는 계정 중 아직 안 넣었고 최근(LIST_RETRY)에 실패하지 않은 것.
    - 빼기: 보는 계정에서 빠진 리스트 멤버. 내가 정한 계정(direct)을 지웠으면 바로, 관심사에 딸린 계정이면
      LIST_GRACE 뒤 (관심사를 껐다 켤 때 넣고 빼기를 되풀이하지 않게 — X에서 가장 자동화처럼 보이는 모양이다).
      그 사이 다시 보는 계정이 되면 빼지 않는다."""
    from popme.collectors.x import list_id
    xcfg = cfg.get("x", {})
    lid = list_id(xcfg.get("list_url"))
    if not lid or not xcfg.get("list_sync", True):
        return [], [], None
    s = _list_state(lid)
    if name:
        s["name"] = name
    now = datetime.now(timezone.utc)
    src = watch_sources(cfg, db)
    order = [a["handle"] for a in core_accounts(cfg, db)]
    s["src"].update(src)  # 보는 동안의 출처를 남겨 둔다 — 빠진 뒤에 바로 뺄지 기다릴지 정할 때
    for h in s["members"]:
        if h in src:
            s["gone"].pop(h, None)
        else:
            s["gone"].setdefault(h, now.isoformat())
    _save_list_state(s)
    name = s.get("name")
    if not name:
        return [], [], None  # 리스트를 한 번 정상으로 봐야 이름을 안다
    room = max(LIST_ADD_PER_DAY - s["added"].get(now.strftime("%Y-%m-%d"), 0), 0)

    def failed_recently(h):
        try:
            return now - datetime.fromisoformat(s["failed"][h]) < LIST_RETRY
        except (KeyError, ValueError):
            return False

    def due(h):
        if s["src"].get(h) == "direct":
            return True
        try:
            return now - datetime.fromisoformat(s["gone"][h]) >= LIST_GRACE
        except (KeyError, ValueError):
            return False
    adds = [h for h in order if h.lower() not in s["members"] and not failed_recently(h.lower())][:room]
    removes = [h for h in s["members"] if h in s["gone"] and due(h) and not failed_recently(h)][:room - len(adds)]
    quiet = _quiet_members(s, db, lid, src, now, room - len(adds) - len(removes), failed_recently)
    return adds + quiet, removes, name


def _quiet_members(s, db, lid, src, now, room, failed_recently=lambda h: False):
    """기록(x-list.json)상 멤버인데 리스트에서 LIST_QUIET 동안 글이 하나도 안 온 보는 계정 → 다시 넣어 본다(넣기 대화상자가
    이미 체크돼 있으면 저장하지 않고 닫음 = 확인만). 기록은 앱이 넣은 것만 알아서, X에서 손으로 뺀 계정은 리스트에도 없고
    계정 페이지 돌아가며 보기에서도 빠져 글을 못 받는다. 리스트를 LIST_QUIET 넘게 보고 있었을 때만, 같은 계정은 LIST_RECHECK에 한 번.
    확인이 실패한 계정은 넣기·빼기처럼 LIST_RETRY 동안 쉰다 — 안 그러면 매번 다시 시도하며 LIST_CHECKS 자리를 막는다."""
    if room <= 0 or db is None:
        return []
    try:
        first = min(datetime.fromisoformat(v) for v in s["members"].values()) if s["members"] else now
    except ValueError:
        return []
    since = (now - LIST_QUIET).isoformat()
    heard = {r["a"] for r in db.q("SELECT DISTINCT lower(t.author) AS a FROM sightings s JOIN tweets t ON t.id = s.id "
                                  "WHERE s.via = ? AND s.seen_at >= ?", f"list:{lid}", since)}
    if not heard:
        return []  # 리스트에서 아무 글도 못 받았으면 리스트를 못 본 것 — 계정 탓이 아니다

    def stale(h):
        try:
            joined = datetime.fromisoformat(s["members"][h])
            checked = datetime.fromisoformat(s["checked"].get(h) or "1970-01-01T00:00:00+00:00")
        except ValueError:
            return False
        return now - joined >= LIST_QUIET and now - checked >= LIST_RECHECK
    if now - first < LIST_QUIET:
        return []
    quiet = [h for h in s["members"] if h in src and h not in heard and stale(h) and not failed_recently(h)]
    return quiet[:min(room, LIST_CHECKS)]


def mark_list(cfg, handle, result):
    """set_list_membership 결과를 남긴다. X에 쓴 것(added·removed)만 하루 몫에 센다."""
    from popme.collectors.x import list_id
    lid = list_id(cfg.get("x", {}).get("list_url"))
    if not lid:
        return
    s = _list_state(lid)
    now = datetime.now(timezone.utc)
    h = handle.lower()
    if result in ("added", "already"):
        if result == "added" or h not in s["members"]:
            s["members"][h] = now.isoformat()
        s["checked"][h] = now.isoformat()
        s["failed"].pop(h, None)
    elif result in ("removed", "absent"):
        for k in ("members", "src", "gone", "failed", "checked"):
            s[k].pop(h, None)
    else:
        s["failed"][h] = now.isoformat()
    if result in ("added", "removed"):
        today = now.strftime("%Y-%m-%d")
        s["added"] = {today: s["added"].get(today, 0) + 1}  # 오늘 것만 남긴다 (넣기·빼기 합)
    _save_list_state(s)


def list_since(cfg, since):
    """X 리스트를 어디까지 내려 볼지: 지난번에 본 시각 1시간 전 (X_LOOKBACK 안). 처음이면 since."""
    from popme.collectors.x import list_id
    lid = list_id(cfg.get("x", {}).get("list_url"))
    seen = _visits().get(f"list:{lid}") if lid else None
    if not seen:
        return since
    try:
        start = datetime.fromisoformat(seen) - timedelta(hours=1)
    except (TypeError, ValueError):
        return since
    return max(start, datetime.now(timezone.utc) - X_LOOKBACK).isoformat()


def mark_x_visited(handles):
    """실제로 글을 받아 온 계정 페이지의 본 시각을 남긴다 (x_plan이 다음에 오래 안 본 것부터 고르고, 어디까지 내려 볼지 정할 때)."""
    if not handles:
        return
    seen = _visits()
    now = datetime.now(timezone.utc).isoformat()
    seen.update({h.lower(): now for h in handles})
    try:
        atomic_write_text(X_VISITS, json.dumps(seen))
    except OSError:
        log.warning("X 돌아가며 보기 기록 저장 실패", exc_info=True)


def threads_accounts(cfg, db):
    users = {u.lower(): u for u in cfg.get("threads", {}).get("accounts", [])}
    for c in discovery.added(db, "threads"):
        u = c["handle"].split("/", 1)[1]
        users.setdefault(u.lower(), u)
    return list(users.values())
