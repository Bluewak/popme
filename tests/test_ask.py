"""질문 답변: 진행 단계 알림, 정리된 답(JSON) 검사, 캐릭터 문구 기본값."""
import json

from popme import briefing, character
from popme.db import DB

DATA = "- foo | https://x.com/a/status/1 | https://github.com/x/y/releases"


def test_clean_answer_filters_links_and_caps_points():
    raw = {"found": True, "summary": "  MCP 얘기\n많았어 ", "points": [
        {"date": "2026-09-08", "text": "블렌더 MCP 공유", "status": "확인됨",
         "links": [{"label": "원문", "url": "https://x.com/a/status/1"}, {"label": "가짜", "url": "https://made.up"}]},
        {"date": "9월 8일", "text": "날짜 형식 틀림", "status": "아마"},
        {"text": ""}, {"text": "c"}, {"text": "d"}, {"text": "e"}]}
    a = briefing.clean_answer(raw, DATA)
    assert a["summary"] == "MCP 얘기 많았어" and a["found"]
    assert len(a["points"]) == 3  # 앞 4개 중 빈 것 하나 빠짐
    assert a["points"][0]["links"] == [{"label": "원문", "url": "https://x.com/a/status/1"}]  # 지어낸 링크 버림
    assert a["points"][1]["date"] == "" and a["points"][1]["status"] == "미확인"


def test_clean_answer_not_found():
    a = briefing.clean_answer({"found": False, "summary": "수집한 자료에서는 확인 못 했어", "points": []}, DATA)
    assert not a["found"] and a["points"] == []


def test_answer_reports_stages_and_search(tmp_path, monkeypatch):
    db = DB(tmp_path / "t.db")
    db.upsert_tweets([{"id": "1", "author": "simonw", "text": "MCP server", "created_at": "2026-09-10T03:00:00+00:00",
                       "likes": 5, "via": "user:simonw", "kind": "post"}])
    replies = iter([
        '{"keywords": ["MCP"], "since": "2026-09-01", "until": "2026-09-30"}',
        json.dumps({"found": True, "summary": "simonw가 MCP 서버 얘기함", "points": [
            {"date": "2026-09-10", "text": "simonw가 MCP 서버 소개", "status": "미확인",
             "links": [{"label": "원문", "url": "https://x.com/simonw/status/1"}]}]}),
    ])
    systems = []
    monkeypatch.setattr(briefing.llm, "ask", lambda cfg, system, *a, **k: (systems.append(system), next(replies))[1])
    stages = []
    res = briefing.answer({}, db, "지난달 MCP 누가 얘기했어?", lambda s, i=None: stages.append((s, i)))
    assert [s for s, _ in stages] == ["plan", "search", "write"]
    assert stages[1][1] == {"keywords": ["MCP"], "since": "2026-09-01", "until": "2026-09-30"}
    assert res["search"]["counts"]["posts"] == 1
    assert res["points"][0]["links"][0]["url"] == "https://x.com/simonw/status/1"  # 검색 결과에 있던 링크라 남음
    assert "높임말" in systems[1] and "반말" in briefing.SYSTEM  # 답은 높임말, 브리핑 카드는 반말 그대로


def test_character_ask_defaults(tmp_path, monkeypatch):
    f = tmp_path / "c.toml"
    f.write_text('[persona]\nname = "X"\ncall_user = "님"\n[behavior]\nchatter_bubble_sec = 5\n'
                 '[lines]\nask_start = ["「{q}」 볼게요"]\n', encoding="utf-8")
    monkeypatch.setattr(character, "character_path", lambda: f)
    c = character.Character()
    assert c.line("ask_start", q="MCP") == "「MCP」 볼게요"  # 질문 시작: 옆 말풍선
    assert c.line("ask_failed", msg="x") == "답을 못 했어요: x"  # 캐릭터 파일에 없으면 기본 대사
