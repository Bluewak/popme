"""계정 페이지 '열자마자 판단': 지난 브리핑 이전 글까지 내려왔으면 스크롤을 멈춘다."""
import pytest

from popme.collectors import reached_since, threads, x

SINCE = "2026-10-08T00:00:00+00:00"
OLD, NEW = "2026-10-01T00:00:00+00:00", "2026-10-08T12:00:00+00:00"


def post(author, created_at):
    return {"author": author, "created_at": created_at}


def test_three_old_owner_posts_stop():
    assert reached_since([post("Me", NEW), post("me", OLD), post("me", OLD), post("me", OLD)], "me", SINCE)


def test_pinned_old_posts_do_not_stop():
    # 고정글 2개가 오래됐어도 나머지가 새 글이면 계속 내려간다
    assert not reached_since([post("me", OLD), post("me", OLD)] + [post("me", NEW)] * 10, "me", SINCE)


def test_other_authors_old_posts_ignored():
    # 리트윗·인용 원문(다른 사람 글)이 오래된 건 판단에 쓰지 않는다
    assert not reached_since([post("other", OLD)] * 5 + [post("me", NEW)], "me", SINCE)


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def wheel(self, dx, dy):
        self.page.scrolls += 1
        self.page.emit()


class FakeLocator:
    def __init__(self, page):
        self.page = page

    def all_text_contents(self):
        return self.page.scripts


class FakePage:
    """goto·스크롤 때마다 batches에서 응답 하나씩 흘려보내는 가짜 페이지."""

    def __init__(self, batches, scripts=()):
        self.batches, self.scripts, self.handlers, self.scrolls, self.url = list(batches), list(scripts), [], 0, ""
        self.mouse = FakeMouse(self)

    def on(self, _event, fn):
        self.handlers.append(fn)

    def remove_listener(self, _event, fn):
        self.handlers.remove(fn)

    def goto(self, url, **_):
        self.url = url
        self.emit()

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, _sel):
        return FakeLocator(self)

    def emit(self):
        if self.batches:
            r = FakeResponse(self.url, self.batches.pop(0))
            for h in self.handlers:
                h(r)


class FakeResponse:
    def __init__(self, url, data):
        self.url, self.data = url, data

    def json(self):
        return self.data


def x_tweet(i, author, created):
    from datetime import datetime
    ts = datetime.fromisoformat(created).strftime("%a %b %d %H:%M:%S +0000 %Y")
    return {"__typename": "Tweet", "rest_id": str(i),
            "core": {"user_results": {"result": {"core": {"screen_name": author, "name": author}}}},
            "legacy": {"full_text": "t", "created_at": ts}}


def x_batch(start, created, n=5):
    return {"data": [x_tweet(start + i, "me", created) for i in range(n)]}


GQL = "https://x.com/i/api/graphql/abc/UserTweets"


def test_x_quiet_account_no_scroll(monkeypatch):
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = FakePage([x_batch(0, OLD), x_batch(10, OLD)])
    page.goto = lambda url, **_: (setattr(page, "url", GQL), page.emit())
    out = []
    x._visit(page, "https://x.com/me", "user:me", 3, out, set(), "me", SINCE)
    assert page.scrolls == 0 and len(out) == 5


def test_x_active_account_scrolls_until_old(monkeypatch):
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = FakePage([x_batch(0, NEW), x_batch(10, NEW), x_batch(20, OLD), x_batch(30, OLD)])
    page.goto = lambda url, **_: (setattr(page, "url", GQL), page.emit())
    out = []
    x._visit(page, "https://x.com/me", "user:me", 3, out, set(), "me", SINCE)
    assert page.scrolls == 2 and len(out) == 15


def test_x_logs_when_scrolls_run_out_before_since(monkeypatch, caplog):
    """스크롤을 다 써도 since까지 못 닿으면 (돌아가며 보는 계정의 사이 글이 잘림) 로그를 남긴다."""
    import logging
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = FakePage([x_batch(0, NEW), x_batch(10, NEW), x_batch(20, NEW)])
    page.goto = lambda url, **_: (setattr(page, "url", GQL), page.emit())
    with caplog.at_level(logging.INFO, logger="popme.collectors.x"):
        x._visit(page, "https://x.com/me", "user:me", 2, [], set(), "me", SINCE)
    assert page.scrolls == 2 and "못 내려감" in caplog.text


def test_x_search_page_always_scrolls(monkeypatch):
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = FakePage([x_batch(0, OLD)] * 4)
    page.goto = lambda url, **_: (setattr(page, "url", GQL), page.emit())
    x._visit(page, "https://x.com/search?q=a", "search:0", 3, [], set())
    assert page.scrolls == 3


def th_post(code, created):
    from datetime import datetime
    return {"code": code, "user": {"username": "me"}, "caption": {"text": "t"},
            "taken_at": int(datetime.fromisoformat(created).timestamp())}


def test_threads_quiet_account_no_scroll(monkeypatch):
    import json
    monkeypatch.setattr(threads, "dump_raw", lambda *a, **k: None)
    first_screen = json.dumps({"posts": [th_post(f"c{i}", OLD) for i in range(4)]})  # HTML 안 JSON
    page = FakePage([], scripts=[first_screen])
    out = []
    threads._visit(page, "https://www.threads.com/@me", "th-user:me", 3, out, "me", SINCE)
    assert page.scrolls == 0 and len(out) == 4


def test_threads_profile_owner_followers_from_separate_script(monkeypatch):
    # 프로필 주인 정보는 게시물이 없는 JSON 덩어리에 따로 실려 온다 (2026-10-09 실제 실행에서 놓쳤던 경우)
    import json
    monkeypatch.setattr(threads, "dump_raw", lambda *a, **k: None)
    owner = json.dumps({"user": {"username": "me", "full_name": "Me", "follower_count": 12345}})
    posts = json.dumps({"posts": [th_post(f"c{i}", OLD) for i in range(4)]})
    page = FakePage([], scripts=[owner, posts])
    users = {}
    threads._visit(page, "https://www.threads.com/@me", "th-check:me", 2, [], users=users)
    assert users["me"]["followers"] == 12345


class ListPage(FakePage):
    """리스트 페이지: 휠 대신 페이지 안 스크롤(evaluate)로 더 불러온다."""

    def evaluate(self, _js):
        self.scrolls += 1
        self.emit()


LIST_GQL = "https://x.com/i/api/graphql/abc/ListLatestTweetsTimeline"


def list_batch(entries):
    """리스트 응답: 타임라인 글마다 itemContent.tweet_results 한 칸."""
    return {"data": {"list": {"tweets_timeline": {"timeline": {"instructions": [{"entries": [
        {"content": {"itemContent": {"tweet_results": {"result": t}}}} for t in entries]}]}}}}}


def retweet_of_old_quote(i, when):
    """최근 리트윗 → 오래된 인용 글 → 더 오래된 원문. walk로 펼치면 셋 중 둘이 오래된 글."""
    original = x_tweet(f"o{i}", "c", OLD)
    quote = {**x_tweet(f"q{i}", "b", OLD), "quoted_status_result": {"result": original}}
    rt = x_tweet(f"r{i}", "a", when)
    rt["legacy"]["retweeted_status_result"] = {"result": quote}
    return rt


def test_x_list_scrolls_until_since_then_stops(monkeypatch):
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    batch = lambda start, when: list_batch([x_tweet(start + i, "me", when) for i in range(5)])
    page = ListPage([batch(0, NEW), batch(10, NEW), batch(20, OLD), batch(30, OLD)])
    page.goto = lambda url, **_: (setattr(page, "url", LIST_GQL), page.emit())
    out = []
    got, complete, reached, pages = x._visit_list(page, "https://x.com/i/lists/123456789", "list:123456789", SINCE, out, set())
    assert page.scrolls == 2 and got == 15 and complete and {t["via"] for t in out} == {"list:123456789"}
    assert reached == OLD and pages == 3


def test_x_list_not_complete_when_scrolls_run_out(monkeypatch):
    """since까지 못 내려갔으면 받은 글은 두고 「끝까지 못 봤다」와 내려간 데까지의 시각을 알린다."""
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    monkeypatch.setattr(x, "LIST_SCROLLS", 2)
    batch = lambda start: list_batch([x_tweet(start + i, "me", NEW) for i in range(5)])
    page = ListPage([batch(0), batch(10), batch(20), batch(30)])
    page.goto = lambda url, **_: (setattr(page, "url", LIST_GQL), page.emit())
    got, complete, reached, pages = x._visit_list(page, "https://x.com/i/lists/123456789", "list:123456789", SINCE, [], set())
    assert page.scrolls == 2 and got == 15 and not complete and reached == NEW and pages == 3


def test_x_list_counts_only_pages_that_loaded(monkeypatch):
    """스크롤해도 더 불러와지지 않으면(429·화면 구조 변경) 글 담긴 응답은 첫 장 하나 — 「깊이 읽었다」가 아니다."""
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = ListPage([list_batch([x_tweet(i, "me", NEW) for i in range(5)])])
    page.goto = lambda url, **_: (setattr(page, "url", LIST_GQL), page.emit())
    got, complete, _, pages = x._visit_list(page, "https://x.com/i/lists/123456789", "list:123456789", SINCE, [], set())
    assert got == 5 and not complete and pages == 1


def _collect_with_list(monkeypatch, complete, pages):
    from tests.test_collect_feedback import FakeCtx
    monkeypatch.setattr(x, "_visit", lambda *a, **k: 0)
    monkeypatch.setattr(x, "_visit_list", lambda *a, **k: (7, complete, NEW, pages))
    monkeypatch.setattr(x.random, "uniform", lambda a, b: 0)
    visited, gaps = [], []
    x.collect(FakeCtx(), {"x": {"list_url": "https://x.com/i/lists/123456789"}}, [], since=SINCE,
              visited=visited, list_since=OLD, gaps=gaps)
    return visited, gaps


def test_x_list_marked_seen_when_read_deep_but_not_to_the_end(monkeypatch):
    """2026-11-23~26 고착: 글이 많아 못 내려갔다고 「봤다」로 안 남기면 기준 시각이 그대로라 날마다 더 멀어져 다시는 닿지 못했다.
    깊이 읽었으면 남기고, 빠진 구간은 수집 경고로."""
    visited, gaps = _collect_with_list(monkeypatch, complete=False, pages=x.LIST_DEEP)
    assert visited == ["list:123456789"]
    assert len(gaps) == 1 and gaps[0].startswith("X 리스트를 ") and "빠졌어요" in gaps[0]


def test_x_list_not_marked_when_it_stops_loading(monkeypatch):
    """더 불러와지지 않아 얕게 끝났으면 남기지 않는다 — 계속되면 48시간 뒤 계정 페이지 돌아가며 보기로 (자가 복구)."""
    visited, gaps = _collect_with_list(monkeypatch, complete=False, pages=1)
    assert visited == [] and len(gaps) == 1 and "더 불러와지지 않아" in gaps[0]


def test_x_list_complete_has_no_gap_warning(monkeypatch):
    visited, gaps = _collect_with_list(monkeypatch, complete=True, pages=2)
    assert visited == ["list:123456789"] and gaps == []


def test_x_list_does_not_stop_on_old_originals_inside_new_retweets(monkeypatch):
    """Codex 리뷰(#61): 최근 리트윗이 오래된 인용 글을 담고 있으면 펼친 글의 2/3가 오래된 글 — 그래도 멈추지 않는다."""
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = ListPage([list_batch([retweet_of_old_quote(i, NEW) for i in range(5)]),
                     list_batch([retweet_of_old_quote(10 + i, NEW) for i in range(5)]),
                     list_batch([x_tweet(20 + i, "me", OLD) for i in range(5)])])
    page.goto = lambda url, **_: (setattr(page, "url", LIST_GQL), page.emit())
    x._visit_list(page, "https://x.com/i/lists/123456789", "list:123456789", SINCE, [], set())
    assert page.scrolls == 2


def test_x_list_reached_needs_most_of_the_last_batch_old():
    assert not x._list_reached([NEW, NEW, OLD], SINCE)
    assert x._list_reached([NEW, OLD, OLD], SINCE)
    assert not x._list_reached([], SINCE)


def test_list_id_accepts_url_or_number():
    assert x.list_id("https://x.com/i/lists/2122938785910669775") == "2122938785910669775"
    assert x.list_id("2122938785910669775") == "2122938785910669775"
    assert x.list_id("") is None and x.list_id("https://x.com/me") is None


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, has=None, has_text=None):
        if has is not None:
            return _Rows([r for r in self.rows if r["name"] == has])  # get_by_text(exact=True) → 이름 그대로
        return _Rows([r for r in self.rows if has_text in r["name"]])

    def count(self):
        return len(self.rows)

    @property
    def first(self):
        return _Row(self.rows[0])


class _Row:
    def __init__(self, r):
        self.r = r

    def get_attribute(self, _name, **_):
        return "true" if self.r["checked"] else "false"

    def click(self, **_):
        self.r["checked"] = not self.r["checked"]


class _Clickable:
    def __init__(self, page, rows=None):
        self.page, self.rows = page, rows

    def locator(self, _sel):
        return _Rows(self.page.lists) if self.rows is None else self

    def filter(self, **_):
        return self

    def get_by_role(self, *_a, **_k):
        return self

    @property
    def first(self):
        return self

    def click(self, **_):
        pass


class MembershipPage:
    """프로필 「…」 → List → 대화상자. lists: [{"name", "checked"}]."""

    def __init__(self, lists):
        self.lists, self.url, self.pressed = lists, "", []
        self.keyboard = type("K", (), {"press": lambda _s, k: self.pressed.append(k)})()

    def goto(self, url, **_):
        self.url = url

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, _sel):
        return _Clickable(self, rows=False)

    def get_by_role(self, role, **_):
        return _Clickable(self) if role == "dialog" else _Clickable(self, rows=False)

    def get_by_text(self, text, exact=False):
        assert exact
        return text


def test_list_membership_picks_exact_name_not_substring():
    """Codex 리뷰(#61): 「AI」를 찾을 때 「AI research」를 고르면 엉뚱한 리스트를 바꾼다."""
    lists = [{"name": "AI research", "checked": False}, {"name": "AI", "checked": False}]
    assert x.set_list_membership(MembershipPage(lists), "someone", "AI") == "added"
    assert lists == [{"name": "AI research", "checked": False}, {"name": "AI", "checked": True}]


def test_list_membership_refuses_when_name_is_ambiguous_or_missing():
    for lists in ([{"name": "AI", "checked": False}, {"name": "AI", "checked": False}], [{"name": "AI research", "checked": False}]):
        page = MembershipPage(lists)
        with pytest.raises(RuntimeError):
            x.set_list_membership(page, "someone", "AI")
        assert page.pressed == ["Escape"] and not any(r["checked"] for r in lists)
