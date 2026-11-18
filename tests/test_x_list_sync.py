"""X 리스트 동기화와 방문 계획 (popme/accounts.py list_todo·mark_list·list_members·x_plan)."""
import json
from datetime import datetime, timedelta, timezone

from popme import accounts
from popme.collectors import x

LIST = "https://x.com/i/lists/123456789"


def _cfg(fixed=("fixed1",), **xcfg):
    return {"x": {"list_url": LIST, "accounts": [{"handle": h, "name": h, "group": "g", "focus": "f"} for h in fixed], **xcfg}}


WATCH = {}


def _watch(monkeypatch, handles, direct=()):
    """보는 계정 = handles. direct에 든 것은 내가 정한 계정, 나머지는 관심사 계정."""
    WATCH.clear()
    WATCH.update({h.lower(): "direct" if h in direct else "interest" for h in handles})
    monkeypatch.setattr(accounts, "core_accounts",
                        lambda cfg, db: [{"handle": h, "name": h, "group": "g", "focus": "f"} for h in handles])
    monkeypatch.setattr(accounts, "watch_sources", lambda cfg, db: dict(WATCH))


def _list_seen(hours_ago):
    accounts.X_VISITS.write_text(json.dumps(
        {"list:123456789": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()}), encoding="utf-8")


def test_nothing_to_do_until_the_list_name_is_known(monkeypatch):
    _watch(monkeypatch, ["a", "b"])
    assert accounts.list_todo(_cfg(), None) == ([], [], None)
    assert accounts.list_todo(_cfg(), None, "popme") == (["a", "b"], [], "popme")
    assert accounts.list_todo(_cfg(), None) == (["a", "b"], [], "popme")  # 이름은 기록에 남는다


def test_added_and_already_become_members_failed_waits(monkeypatch):
    _watch(monkeypatch, ["a", "B", "c"])
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    accounts.mark_list(cfg, "a", "added")
    accounts.mark_list(cfg, "B", "already")
    accounts.mark_list(cfg, "c", "failed")
    assert accounts.list_todo(cfg, None) == ([], [], "popme")
    s = json.loads(accounts.X_LIST.read_text(encoding="utf-8"))
    assert set(s["members"]) == {"a", "b"} and list(s["added"].values()) == [1]  # already는 하루 몫에 안 센다
    s["failed"]["c"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    accounts.X_LIST.write_text(json.dumps(s), encoding="utf-8")
    assert accounts.list_todo(cfg, None) == (["c"], [], "popme")


def test_daily_cap(monkeypatch):
    _watch(monkeypatch, [f"h{i}" for i in range(40)])
    cfg = _cfg()
    todo, _, _ = accounts.list_todo(cfg, None, "popme")
    assert len(todo) == accounts.LIST_ADD_PER_DAY
    for h in todo[:25]:
        accounts.mark_list(cfg, h, "added")
    assert len(accounts.list_todo(cfg, None)[0]) == accounts.LIST_ADD_PER_DAY - 25


def test_sync_off_or_no_list(monkeypatch):
    _watch(monkeypatch, ["a"])
    assert accounts.list_todo(_cfg(list_sync=False), None, "popme") == ([], [], None)
    assert accounts.list_todo({"x": {}}, None, "popme") == ([], [], None)


def test_switching_lists_starts_over(monkeypatch):
    _watch(monkeypatch, ["a"])
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    accounts.mark_list(cfg, "a", "added")
    other = {"x": {"list_url": "https://x.com/i/lists/987654321"}}
    assert accounts.list_todo(other, None, "new") == (["a"], [], "new")


def test_list_members_leave_page_rotation_only_while_the_list_is_fresh(monkeypatch):
    rotating = [f"r{i}" for i in range(20)]
    _watch(monkeypatch, ["fixed1"] + rotating)
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    for h in ["fixed1"] + rotating[:15]:
        accounts.mark_list(cfg, h, "added")
    _list_seen(3)
    accounts.mark_x_visited(["fixed1"] + rotating[:15])  # 넣은 뒤 계정 페이지로 한 번 메움
    handles, _ = accounts.x_plan(cfg, None, datetime.now(timezone.utc).isoformat(), limit=11)
    assert handles[0] == "fixed1"  # 고정 계정은 리스트에 있어도 계정 페이지로 (견줄 기준)
    assert set(handles[1:]) == set(rotating[15:])  # 리스트에 없는 5개만 돌아가며 — 자리가 남아 모두
    _list_seen(60)  # 리스트를 이틀 넘게 못 봤으면 원래대로 모두 돌아가며
    handles, _ = accounts.x_plan(cfg, None, datetime.now(timezone.utc).isoformat(), limit=11)
    assert len(handles) == 11 and set(handles[1:]) <= set(rotating) and set(handles[1:]) - set(rotating[15:])


def test_list_name_from_list_response():
    data = {"data": {"list": {"id_str": "123456789", "name": "popme", "member_count": 30}}}
    assert x.list_name(data, "123456789") == "popme"
    assert x.list_name(data, "1") is None


def _backdate_gone(h, days):
    s = json.loads(accounts.X_LIST.read_text(encoding="utf-8"))
    s["gone"][h] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    accounts.X_LIST.write_text(json.dumps(s), encoding="utf-8")


def test_removed_direct_account_leaves_the_list_now_interest_account_after_grace(monkeypatch):
    _watch(monkeypatch, ["mine", "intr", "keep"], direct=("mine",))
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    for h in ("mine", "intr", "keep"):
        accounts.mark_list(cfg, h, "added")
    _watch(monkeypatch, ["keep"])  # 내가 정한 계정과 관심사 계정 하나가 보는 계정에서 빠짐
    assert accounts.list_todo(cfg, None) == ([], ["mine"], "popme")  # 관심사 계정은 아직
    _backdate_gone("intr", 13)
    assert accounts.list_todo(cfg, None)[1] == ["mine"]
    _backdate_gone("intr", 15)
    assert sorted(accounts.list_todo(cfg, None)[1]) == ["intr", "mine"]
    accounts.mark_list(cfg, "mine", "removed")
    accounts.mark_list(cfg, "intr", "absent")  # 이미 누가 손으로 뺐다
    assert accounts.list_todo(cfg, None) == ([], [], "popme")
    assert set(json.loads(accounts.X_LIST.read_text(encoding="utf-8"))["members"]) == {"keep"}


def test_interest_account_back_within_grace_is_not_removed(monkeypatch):
    _watch(monkeypatch, ["intr"])
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    accounts.mark_list(cfg, "intr", "added")
    _watch(monkeypatch, [])
    accounts.list_todo(cfg, None)
    _backdate_gone("intr", 10)
    _watch(monkeypatch, ["intr"])  # 관심사를 다시 켬
    assert accounts.list_todo(cfg, None) == ([], [], "popme")
    _watch(monkeypatch, [])
    assert accounts.list_todo(cfg, None)[1] == []  # 빠진 날을 새로 센다 (예전 10일은 지워짐)


def test_adds_and_removes_share_the_daily_budget(monkeypatch):
    many = [f"h{i}" for i in range(accounts.LIST_ADD_PER_DAY)]
    _watch(monkeypatch, ["old"], direct=("old",))
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    accounts.mark_list(cfg, "old", "already")
    _watch(monkeypatch, many)
    adds, removes, _ = accounts.list_todo(cfg, None)
    assert len(adds) == accounts.LIST_ADD_PER_DAY and removes == []  # 넣기 먼저
    for h in many[:5]:
        accounts.mark_list(cfg, h, "added")
    adds, removes, _ = accounts.list_todo(cfg, None)
    assert len(adds) == accounts.LIST_ADD_PER_DAY - 5 and removes == []


def _member_since(h, days):
    s = json.loads(accounts.X_LIST.read_text(encoding="utf-8"))
    s["members"][h] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    s["checked"].pop(h, None)
    accounts.X_LIST.write_text(json.dumps(s), encoding="utf-8")


def _heard(db, author, via="list:123456789"):
    db.upsert_tweets([{"id": f"t-{author}", "author": author, "text": "글", "created_at": datetime.now(timezone.utc).isoformat(),
                       "lang": "en", "likes": 1, "urls": "[]", "kind": "post", "ref_author": None, "via": via}])


def test_quiet_members_are_rechecked_on_the_dialog(monkeypatch, tmp_path):
    """기록상 멤버인데 리스트에서 5일째 글이 없으면 넣기 대화상자로 확인 (X에서 손으로 뺐을 수 있다). 14일에 한 번."""
    from popme.db import DB
    db = DB(tmp_path / "t.db")
    _watch(monkeypatch, ["loud", "quiet", "new"])
    cfg = _cfg()
    accounts.list_todo(cfg, db, "popme")
    for h in ("loud", "quiet", "new"):
        accounts.mark_list(cfg, h, "added")
    assert accounts.list_todo(cfg, db) == ([], [], "popme")  # 리스트에서 아직 아무것도 못 받음
    _heard(db, "loud")
    for h in ("loud", "quiet"):
        _member_since(h, 6)
    _member_since("new", 2)  # 넣은 지 얼마 안 됨
    assert accounts.list_todo(cfg, db) == (["quiet"], [], "popme")
    accounts.mark_list(cfg, "quiet", "already")  # 들어 있었다 — 14일 동안 다시 안 본다
    assert accounts.list_todo(cfg, db) == ([], [], "popme")
    s = json.loads(accounts.X_LIST.read_text(encoding="utf-8"))
    assert s["members"]["quiet"] < s["checked"]["quiet"]  # 넣은 때는 그대로, 확인한 때만 새로


def test_quiet_member_check_that_failed_waits_like_adds(monkeypatch, tmp_path):
    """Codex 리뷰(#61): 조용한 계정 확인이 실패하면 7일 쉰다 — 매번 다시 시도하며 확인 자리(5개)를 막지 않게."""
    from popme.db import DB
    db = DB(tmp_path / "t.db")
    _watch(monkeypatch, ["loud", "quiet"])
    cfg = _cfg()
    accounts.list_todo(cfg, db, "popme")
    for h in ("loud", "quiet"):
        accounts.mark_list(cfg, h, "added")
        _member_since(h, 6)
    _heard(db, "loud")
    assert accounts.list_todo(cfg, db) == (["quiet"], [], "popme")
    accounts.mark_list(cfg, "quiet", "failed")
    assert accounts.list_todo(cfg, db) == ([], [], "popme")


def test_rotating_accounts_that_all_fit_still_backfill_from_last_visit(monkeypatch):
    """Codex 리뷰(#61): 리스트로 계정이 빠져 남은 계정이 한 번에 다 들어가도, 며칠 못 본 계정은 지난번 본 시각까지 내려 본다."""
    rotating = [f"r{i}" for i in range(20)]
    _watch(monkeypatch, ["fixed1"] + rotating)
    cfg = _cfg()
    accounts.list_todo(cfg, None, "popme")
    for h in rotating[:15]:
        accounts.mark_list(cfg, h, "added")
    now = datetime.now(timezone.utc)
    visits = {"list:123456789": (now - timedelta(hours=3)).isoformat()}
    visits.update({h: (now - timedelta(days=4)).isoformat() for h in rotating[15:]})
    visits.update({h: now.isoformat() for h in rotating[:15]})  # 리스트 계정은 넣은 뒤 한 번 메움
    accounts.X_VISITS.write_text(json.dumps(visits), encoding="utf-8")
    handles, since_by = accounts.x_plan(cfg, None, (now - timedelta(days=1)).isoformat(), limit=11)
    assert set(handles[1:]) == set(rotating[15:])
    assert set(since_by) == set(rotating[15:])  # 어제 브리핑 시각이 아니라 나흘 전(지난번 본 때)부터


def test_new_list_member_keeps_its_page_visit_until_the_gap_is_filled(monkeypatch):
    """Codex 리뷰(#61): 나흘 전에 본 계정을 오늘 리스트에 넣으면, 리스트는 오늘부터만 이어 받는다 —
    그 사이는 계정 페이지로 한 번 메운 뒤에야 돌아가며 보기에서 뺀다."""
    _watch(monkeypatch, ["fixed1", "a", "b"])
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    accounts.X_VISITS.write_text(json.dumps({"a": (now - timedelta(days=4)).isoformat()}), encoding="utf-8")
    accounts.list_todo(cfg, None, "popme")
    accounts.mark_list(cfg, "a", "added")
    _list_seen(1)
    accounts.X_VISITS.write_text(json.dumps({**json.loads(accounts.X_VISITS.read_text(encoding="utf-8")),
                                             "a": (now - timedelta(days=4)).isoformat()}), encoding="utf-8")
    assert accounts.list_members(cfg) == set()
    handles, since_by = accounts.x_plan(cfg, None, (now - timedelta(days=1)).isoformat(), limit=11)
    assert "a" in handles and "a" in since_by  # 나흘 전부터 계정 페이지로
    accounts.mark_x_visited(["a"])
    assert accounts.list_members(cfg) == {"a"}
    assert "a" not in accounts.x_plan(cfg, None, now.isoformat(), limit=11)[0]
