"""출처 모듈 모음. 종류(type)별 모듈을 골라 받고, 여러 출처를 동시에 받는다.

새 출처 종류 = 이 폴더에 모듈 하나(base.py 규격) + KINDS에 한 줄. 설정의 출처 한 줄에 type = "…" (없으면 "rss").
"""
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from popme.sources import rss

log = logging.getLogger(__name__)
KINDS = {"rss": rss}
WORKERS = 8


def module(feed):
    return KINDS[feed.get("type", "rss")]


def collect(feeds, progress=lambda s: None):
    """출처들을 받는다 → (글 목록, 실패 문구 목록). 서버(호스트)가 다르면 동시에, 같은 서버는 차례로
    — hnrss·Reddit은 같은 서버에 한꺼번에 요청하면 502·429를 낸다. 한 출처가 실패해도 나머지는 받는다."""
    by_host = defaultdict(list)
    for f in feeds:
        by_host[urlsplit(f.get("url", "")).netloc].append(f)
    done = []

    def run(host_feeds):
        out, errs = [], []
        for f in host_feeds:
            try:
                out += module(f).fetch(f)
            except Exception as e:
                log.warning("출처 실패 %s: %s", f.get("name"), e)
                errs.append(f"{f.get('name')}: {e}")
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
