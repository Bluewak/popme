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
    rotating = [a["handle"] for a in accts if a["handle"].lower() not in mine]
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
