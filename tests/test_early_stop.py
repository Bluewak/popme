"""계정 페이지 '열자마자 판단': 지난 브리핑 이전 글까지 내려왔으면 스크롤을 멈춘다."""
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


def test_x_list_scrolls_until_since_then_stops(monkeypatch):
    monkeypatch.setattr(x, "dump_raw", lambda *a, **k: None)
    page = ListPage([x_batch(0, NEW), x_batch(10, NEW), x_batch(20, OLD), x_batch(30, OLD)])
    page.goto = lambda url, **_: (setattr(page, "url", LIST_GQL), page.emit())
    out = []
    got = x._visit_list(page, "https://x.com/i/lists/123456789", "list:123456789", SINCE, out, set())
    assert page.scrolls == 2 and got == 15 and {t["via"] for t in out} == {"list:123456789"}


def test_x_list_reached_needs_most_of_the_last_batch_old():
    """인용·리트윗 원문(오래된 글)이 몇 개 섞여도 멈추지 않는다."""
    new, old = post("a", NEW), post("b", OLD)
    assert not x._list_reached([new, new, old], SINCE)
    assert x._list_reached([new, old, old], SINCE)
    assert not x._list_reached([], SINCE)


def test_list_id_accepts_url_or_number():
    assert x.list_id("https://x.com/i/lists/2122938785910669775") == "2122938785910669775"
    assert x.list_id("2122938785910669775") == "2122938785910669775"
    assert x.list_id("") is None and x.list_id("https://x.com/me") is None
