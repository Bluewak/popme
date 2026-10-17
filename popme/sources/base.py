"""출처 모듈 규격.

모듈(popme/sources/<종류>.py)은 함수 두 개만 갖는다.
  fetch(feed) -> list[Item]   feed: 설정의 출처 한 줄(name·url·group·max_chars·skip …). 실패하면 예외를 던진다.
  describe(feed) -> str       「소식 출처」 화면의 "이렇게 받아요" 문구
거르기(topic)·상한·태그는 모듈이 하지 않는다 — 브리핑에 넘길 때 공통으로 한다 (popme/briefing.py rss_items).
"""
from typing import TypedDict


class Item(TypedDict):
    source: str          # 출처 이름 (DB items.source)
    grp: str             # 묶음 (공식 변경내역·공식 발표 …)
    url: str
    title: str
    summary: str
    published: str | None   # UTC ISO, 모르면 None (그러면 처음 본 시각으로 판단)
