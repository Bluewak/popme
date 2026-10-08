"""카드형 브리핑 검사 (popme/briefing.py): 형식 정리, 지어낸 링크 버리기, 제목 규칙."""
from popme import briefing

DATA = "- foo | https://x.com/a/status/1 | https://github.com/x/y/releases/tag/v2.1.294"

RAW = '''설명이 앞에 붙어도 됨 ```json
{"items": [
 {"cat": "업데이트", "status": "확인됨", "rel": 3, "title": "Claude Code 2.1.294 — hook 수정", "me": "해당",
  "todo": "업데이트", "body": "본문", "links": [
    {"label": "릴리스", "url": "https://github.com/x/y/releases/tag/v2.1.294"},
    {"label": "가짜", "url": "https://made.up/z"}]},
 {"cat": "이상한분류", "title": "아주아주아주아주 길어서 서른다섯 자를 훌쩍 넘겨버리는 제목이 여기 있습니다 정말로", "rel": 9},
 {"cat": "혜택", "status": "미확인", "urgent": true, "rel": 2, "title": "리셋권 지급", "chips": ["무료", "~11/7"],
  "links": [{"label": "원문", "url": "https://x.com/a/status/1"}]},
 {"title": ""}
], "lines": ["안녕", 3, ""]}
```'''


def payload():
    return briefing.clean_items(briefing.parse_json(RAW), DATA)


def test_parse_json_with_surrounding_text():
    assert len(briefing.parse_json(RAW)["items"]) == 4


def test_clean_fills_and_fixes_fields():
    items = payload()["items"]
    assert len(items) == 3  # 제목 없는 카드는 버림
    odd = items[1]
    assert odd["cat"] == "커뮤니티" and odd["rel"] == 2 and odd["status"] == "미확인"
    assert payload()["lines"] == ["안녕"]


def test_links_not_in_data_are_dropped():
    links = payload()["items"][0]["links"]
    assert [l["label"] for l in links] == ["릴리스"]


def test_title_rules():
    assert briefing.title_problems("Claude Code 2.1.294 — hook 수정") == ["버전 번호가 제목에 있음"]
    assert any("자" in p for p in briefing.title_problems("가" * 40))
    assert briefing.title_problems("Claude Code, '명령 막는 hook'이 안 막던 버그 고침") == []


def test_headlines_put_important_and_urgent_first():
    heads = briefing.headlines_from(payload()["items"])
    assert heads[0]["md"].startswith("Claude Code")
    assert heads[1]["urgent"] is True


def test_markdown_keeps_character_lines_section():
    md = briefing.to_markdown("# 제목\n", payload())
    assert "## 꼭 볼 것" in md and "## 캐릭터 멘트\n- 안녕" in md
    assert briefing.pop_section(md, "캐릭터 멘트")[0] == ["안녕"]
