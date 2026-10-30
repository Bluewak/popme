"""유튜브 채널 새 영상 (popme/collectors/youtube.py): 「동영상」 탭의 목록 → items 표. RSS 단계에선 건너뛴다."""
from datetime import datetime, timedelta, timezone

from popme import catalog, sources
from popme.collectors import youtube

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
FEED = {"name": "K리그 유튜브", "group": "영상", "type": "youtube", "url": "https://www.youtube.com/@kleague/videos"}


def test_ago_reads_korean_and_english():
    assert youtube.ago("14시간 전", NOW) == NOW - timedelta(hours=14)
    assert youtube.ago("스트리밍 시간: 2주 전", NOW) == NOW - timedelta(weeks=2)
    assert youtube.ago("1개월 전", NOW) == NOW - timedelta(days=30)
    assert youtube.ago("3 days ago", NOW) == NOW - timedelta(days=3)
    assert youtube.ago("조회수 5.9만회", NOW) is None


def test_items_from_videos():
    videos = [{"id": "a1", "title": "하이라이트", "meta": ["조회수 2만회", "14시간 전"]},
              {"id": "b2", "title": "옛날 영상", "meta": ["조회수 9만회", "3주 전"]},
              {"id": "", "title": "아이디 없음", "meta": []},
              {"id": "c3", "title": "시각 모름", "meta": ["조회수 1회"]}]
    got = youtube.items_from(videos, FEED, NOW, since=NOW - timedelta(days=2))
    assert [g["url"][-2:] for g in got] == ["a1", "c3"]  # 오래된 것·아이디 없는 것 빼고, 시각을 모르면 남긴다
    assert got[0] == {"url": "https://www.youtube.com/watch?v=a1", "source": "K리그 유튜브", "grp": "영상", "title": "하이라이트",
                      "summary": "조회수 2만회 · 14시간 전", "published": (NOW - timedelta(hours=14)).isoformat(timespec="seconds")}
    assert got[1]["published"] is None


def test_rss_step_skips_browser_kinds(monkeypatch):
    called = []
    monkeypatch.setattr(sources.rss, "fetch", lambda f, **k: called.append(f["name"]) or [])
    items, errs = sources.collect([FEED, {"name": "보통 RSS", "url": "https://example.com/rss"}])
    assert called == ["보통 RSS"] and errs == []


def test_packs_link_youtube_channels():
    yt = {k for k, f in catalog.feeds().items() if f.get("type") == "youtube"}
    assert len(yt) >= 20 and all(catalog.feeds()[k]["url"].endswith("/videos") for k in yt)
    linked = {f["id"] for p in catalog._profiles() for f in p.get("feeds", [])}
    assert yt <= linked  # 어느 묶음에도 안 붙은 채널은 없다
