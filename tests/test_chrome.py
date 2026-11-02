"""전용 Chrome 새 탭 (popme/chrome.py): 수집 스레드들이 동시에 열어도 자기 탭만 받는다.
2026-10-20~11-02: 창이 최소화된 채로 Threads·TimeTree가 같은 탭을 받아 둘 다 ERR_ABORTED로 실패하고 빈 탭이 남았다."""
from contextlib import contextmanager
from types import SimpleNamespace

from popme import chrome


class FakeCtx:
    """스레드마다 연결이 달라 다른 스레드가 연 탭 소식도 온다 → arrived에 먼저 온 탭을 넣어 둔다."""

    def __init__(self, arrived=()):
        self.arrived = list(arrived)
        self.pages = [SimpleNamespace(url="about:blank")]
        self.browser = SimpleNamespace(new_browser_cdp_session=lambda: SimpleNamespace(send=self.send))

    def send(self, method, params):
        assert method == "Target.createTarget" and params["background"] is True
        self.arrived.append(SimpleNamespace(url=params["url"]))

    @contextmanager
    def expect_page(self, predicate):
        info = SimpleNamespace()
        yield info
        info.value = next(p for p in self.arrived if predicate(p))


def test_new_page_skips_tab_opened_by_other_thread(monkeypatch):
    monkeypatch.setattr(chrome, "_window_state", lambda page: "minimized")
    other = SimpleNamespace(url="about:blank#popme-other")
    ctx = FakeCtx([other])
    page = chrome.new_page(ctx)
    assert page is not other and page.url.startswith("about:blank#popme-")


def test_new_page_marks_every_tab_differently(monkeypatch):
    monkeypatch.setattr(chrome, "_window_state", lambda page: "minimized")
    assert chrome.new_page(FakeCtx()).url != chrome.new_page(FakeCtx()).url
