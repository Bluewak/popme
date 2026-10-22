"""출처 모듈 모음. 종류(type)별 모듈을 골라 받고, 여러 출처를 동시에 받는다.

새 출처 종류 = 이 폴더에 모듈 하나(base.py 규격) + KINDS에 한 줄. 설정의 출처 한 줄에 type = "…" (없으면 "rss").
"""
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from popme.sources import hn, rss

log = logging.getLogger(__name__)
KINDS = {"rss": rss, "hn": hn}
WORKERS = 8
# 잠깐 오류면 한 번 더: 서버 과부하(5xx)·요청 많음(429)·Cloudflare 간헐 차단(403)·시간 초과. 429는 조금 더 기다린다
RETRY_CODES = {403, 429, 500, 502, 503, 504}
RETRY_WAIT = {429: 8}


def transient(e):
    code = getattr(e, "code", None)
    return code in RETRY_CODES or isinstance(e, TimeoutError) or "timed out" in str(e)


def module(feed):
    return KINDS[feed.get("type", "rss")]


def collect(feeds, progress=lambda s: None):
    """출처들을 받는다 → (글 목록, 실패 문구 목록). 서버(호스트)가 다르면 동시에, 같은 서버는 차례로
    — Reddit 등은 같은 서버에 한꺼번에 요청하면 429를 낸다. 잠깐 오류면 몇 초 뒤 한 번 더. 한 출처가 실패해도 나머지는 받는다."""
    by_host = defaultdict(list)
    for f in feeds:
        by_host[urlsplit(f.get("url", "")).netloc].append(f)
    done = []

    def run(host_feeds):
        out, errs = [], []
        for f in host_feeds:
            for attempt in (1, 2):
                try:
                    out += module(f).fetch(f)
                    break
                except Exception as e:
                    if attempt == 1 and transient(e):
                        log.info("출처 잠깐 오류, 다시 받기 %s: %s", f.get("name"), e)
                        time.sleep(RETRY_WAIT.get(getattr(e, "code", None), 3))
                        continue
                    log.warning("출처 실패 %s: %s", f.get("name"), e)
                    errs.append(f"{f.get('name')}: {e}")
                    break
            done.append(f.get("name"))
            progress(f"소식 받는 중 {len(done)}/{len(feeds)}")
        return out, errs

    items, errors = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for out, errs in pool.map(run, by_host.values()):
            items += out
            errors += errs
    for m in set(KINDS.values()):
        try:
            getattr(m, "flush", lambda: None)()  # 조건부 요청용 ETag 등 저장
        except OSError as e:
            log.warning("출처 캐시 저장 실패: %s", e)
    return items, errors
