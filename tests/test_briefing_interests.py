"""관심사별 브리핑 (popme/briefing.py): 기준 합치기, 분류 8칸, 카드 태그, 관심사 저장."""
import json
import re
import tomllib

from popme import briefing, catalog, config
from popme.db import DB


def news(*ids):
    return {"news": {"interests": list(ids)}}


def prompt(cfg):
    return briefing._fill(briefing.BRIEFING_RULES, **briefing.rules_for(cfg), title_rules="", plan="", tools="",
                          persona="", name="", n_lines=3)


def test_one_interest_uses_its_criteria_without_tags():
    ds = next(p for p in catalog.profiles() if p["id"] == "data-science")
    text = prompt(news("data-science"))
    assert ds["criteria"].strip() in text and ds["discovery"] in text
    assert "- tags:" not in text and '"tags"' not in text
    assert not re.search(r"__[A-Z_]+__", text)  # 안 채운 자리 없음


def test_nothing_chosen_falls_back_to_ai_coding():
    ai = next(p for p in catalog.profiles() if p["id"] == "ai-coding")
    assert ai["criteria"].strip() in prompt(news())


def test_two_interests_join_criteria_and_ask_tags():
    text = prompt(news("ai-coding", "data-science"))
    assert "[AI 코딩]" in text and "[데이터]" in text
    assert '- tags: 이 카드가 어느 관심사 이야기인지 0~2개, 이 중에서만: "AI 코딩", "데이터"' in text
    assert '"tags": ["AI 코딩"]' in text
    assert all(c in text for c in briefing.CATS)


def test_clean_items_keeps_only_chosen_tags_and_maps_old_cats():
    raw = {"items": [{"title": "가", "cat": "업데이트", "tags": ["데이터", "모르는 태그", "데이터", "AI 코딩"]},
                     {"title": "나", "cat": "새 모델·도구"}]}
    two = briefing.clean_items(raw, "", ["AI 코딩", "데이터"])["items"]
    assert two[0]["cat"] == "업데이트·단종" and two[0]["tags"] == ["데이터", "AI 코딩"]
    assert two[1]["tags"] == []
    one = briefing.clean_items(raw, "", ["데이터"])["items"]
    assert [it["tags"] for it in one] == [["데이터"], ["데이터"]]  # 관심사가 하나면 모든 카드에
    assert [it["tags"] for it in briefing.clean_items(raw, "")["items"]] == [[], []]


def test_old_briefings_shown_in_new_cats():
    old = {"items": [{"cat": c, "title": c} for c in ["혜택", "업데이트", "흐름", "오픈소스", "커뮤니티", "발굴"]]}
    view = briefing.for_view(old)
    assert [it["cat"] for it in view] == ["혜택·비용", "업데이트·단종", "방법·흐름", "새 모델·도구", "사례·커뮤니티", "발굴"]
    assert all(it["tags"] == [] for it in view)
    assert old["items"][0]["cat"] == "혜택"  # 저장된 건 그대로


def test_feed_tags_hint_only_for_partly_shared_feeds():
    assert briefing.feed_tags(news("ai-coding")) == {}
    hint = briefing.feed_tags(news("ai-coding", "data-science"))
    assert hint["Claude Code 릴리스"] == " · AI 코딩"
    assert "GeekNews" not in hint  # 둘 다 쓰는 출처는 단서가 안 된다
    ds_only = [f["name"] for f in catalog.active(news("data-science")) if "ai-coding" not in f["interests"]]
    assert hint[ds_only[0]] == " · 데이터"


def test_save_interests_writes_config_and_keeps_comments():
    saved = catalog.save_interests(["data-science", "없는-관심사", "ai-coding", "data-science"])
    assert saved == ["data-science", "ai-coding"]
    text = config.CONFIG_PATH.read_text(encoding="utf-8")  # conftest가 임시 경로로 돌려 둠
    assert tomllib.loads(text)["news"]["interests"] == ["data-science", "ai-coding"]
    assert "# 소식 출처 — 고른 관심사" in text and "[llm]" in text
    assert config.load()["news"]["interests"] == ["data-science", "ai-coding"]
    catalog.save_interests([])
    assert config.load()["news"]["interests"] == []


def test_card_tags_saved_on_that_briefing_only(tmp_path):
    db = DB(tmp_path / "t.db")
    db.save_briefing("2026-10-17", "# md", json.dumps({"items": [{"title": "가", "tags": []}], "lines": []}))
    before = db.briefing_for("2026-10-17")["created_at"]
    db.save_briefing_items("2026-10-17", json.dumps({"items": [{"title": "가", "tags": ["데이터"]}], "lines": []}))
    b = db.briefing_for("2026-10-17")
    assert briefing.load_payload(b)["items"][0]["tags"] == ["데이터"] and b["created_at"] == before
