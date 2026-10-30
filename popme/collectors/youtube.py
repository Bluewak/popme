"""유튜브 채널 새 영상: 전용 Chrome에서 채널의 「동영상」 탭을 열고 페이지에 실려 오는 목록(ytInitialData)을 읽는다.

채널 RSS(feeds/videos.xml)는 2026-10 같은 주소가 200과 404를 오갈 만큼 불안정해서 쓰지 않는다 (설계 4-9).
사용자 본인 브라우저에서 사람 속도로 — 유튜브 탭은 하나, X·Threads와는 사이트가 달라 동시에 돈다 (jobs._browser_collect).
글은 RSS 글과 같은 items 표에 출처 이름(feeds.toml의 name)으로 저장된다 → 브리핑이 다른 출처처럼 고른다.
"""
import logging
import random
import re
from datetime import datetime, timedelta, timezone

from popme import chrome

log = logging.getLogger(__name__)

# 영상 하나 = {id, title, meta: ["조회수 5.9만회", "3일 전"]}. 예전 모양(videoRenderer)과 지금 모양(lockupViewModel) 둘 다.
READ_JS = r"""() => {
  const out = [];
  const walk = (o) => {
    if (!o || typeof o !== 'object') return;
    if (o.videoRenderer) {
      const v = o.videoRenderer;
      out.push({id: v.videoId, title: (v.title?.runs || []).map(r => r.text).join('') || v.title?.simpleText || '',
                meta: [v.viewCountText?.simpleText, v.publishedTimeText?.simpleText].filter(Boolean)});
      return;
    }
    if (o.lockupViewModel) {
      const l = o.lockupViewModel, m = l.metadata?.lockupMetadataViewModel;
      const parts = (m?.metadata?.contentMetadataViewModel?.metadataRows || [])
        .flatMap(r => (r.metadataParts || []).map(p => p.accessibilityLabel || p.text?.content)).filter(Boolean);
      out.push({id: l.contentId, title: m?.title?.content || '', meta: parts});
      return;
    }
    for (const k in o) walk(o[k]);
  };
  walk(window.ytInitialData);
  return out;
}"""

UNITS = {"초": "seconds", "분": "minutes", "시간": "hours", "일": "days", "주": "weeks", "개월": "months", "달": "months", "년": "years",
         "second": "seconds", "minute": "minutes", "hour": "hours", "day": "days", "week": "weeks", "month": "months", "year": "years"}
AGO = re.compile(r"(\d+)\s*(초|분|시간|일|주|개월|달|년|second|minute|hour|day|week|month|year)s?\s*(전|ago)")
PER_CHANNEL = 10  # 채널마다 최신 몇 개까지


def ago(text, now):
    """「3일 전」·「14시간 전」·「스트리밍 시간: 2주 전」·「3 days ago」 → 대략의 올린 시각 (UTC). 모르면 None."""
    m = AGO.search(text or "")
    if not m:
        return None
    n, unit = int(m[1]), UNITS[m[2]]
    if unit == "months":
        n, unit = n * 30, "days"
    elif unit == "years":
        n, unit = n * 365, "days"
    return now - timedelta(**{unit: n})


def items_from(videos, feed, now, since=None):
    """페이지에서 읽은 영상들 → items 표 한 줄씩. since보다 오래된 것(올린 시각을 알면)은 뺀다."""
    out = []
    for v in videos:
        if not v.get("id") or not v.get("title"):
            continue
        meta = [str(x) for x in v.get("meta") or []]
        when = next((t for t in (ago(x, now) for x in meta) if t), None)
        if since and when and when < since:
            continue
        out.append({"url": f"https://www.youtube.com/watch?v={v['id']}", "source": feed["name"], "grp": feed.get("group", "영상"),
                    "title": v["title"][:300], "summary": " · ".join(meta)[:200],
                    "published": when.isoformat(timespec="seconds") if when else None})
        if len(out) >= PER_CHANNEL:
            break
    return out


def collect(ctx, feeds, progress=lambda s: None, since=None):
    """feeds: type = "youtube"인 출처들 (url = 채널의 /videos 주소) → (글 목록, 실패 문구 목록)."""
    page = chrome.new_page(ctx)
    out, errs, empty = [], [], 0
    try:
        for i, f in enumerate(feeds):
            progress(f"유튜브 수집 중 {i + 1}/{len(feeds)}")
            try:
                page.goto(f["url"], wait_until="domcontentloaded", timeout=30000)
                videos = page.evaluate(READ_JS)
            except Exception as e:
                log.warning("유튜브 실패 %s: %s", f.get("name"), e)
                errs.append(f"{f.get('name')}: {e}")
                continue
            got = items_from(videos, f, datetime.now(timezone.utc), since)
            empty = empty + 1 if not videos else 0
            if empty >= 3:
                errs.append("유튜브에서 연속으로 영상 목록을 못 찾았어요. 페이지 구조가 바뀐 것 같아요.")
                break
            out += got
            page.wait_for_timeout(random.uniform(2000, 4000))
    finally:
        try:
            page.close()
        except Exception:
            log.info("유튜브 탭 닫기 실패 (이미 닫힘)", exc_info=True)
    progress("유튜브 완료")
    return out, errs
