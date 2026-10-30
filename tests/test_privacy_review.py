"""개인 단어 검사 + 개인정보 검토자 페르소나 (tools/check_private.py, popme/judges.py, popme/persona.py).
예시 단어는 지어낸 것 (실제 목록은 비공개)."""
import importlib.util
import json
from pathlib import Path

import pytest

from popme import judges, persona

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_private", ROOT / "tools/check_private.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

TEXT = 'a = "부평유나이티드"\nname = "부평"\nb = "부평유나이티드 축구"\n'.encode()


def test_every_occurrence_checked_and_exception_covers_only_its_phrase():
    probs = cp.check([("popme/feeds.toml", TEXT)], ["부평"])
    assert [p.split(":")[1] for p in probs] == ["1", "2", "3"]  # 첫 번째만 보던 것 → 모든 위치
    probs = cp.check([("popme/feeds.toml", TEXT)], ["부평"], ["부평유나이티드"])
    assert [p.split(":")[1] for p in probs] == ["2"]  # 단독 「부평」(설정 값)은 계속 막힌다


def test_words_file_exceptions(monkeypatch, tmp_path):
    f = tmp_path / ".private-words.txt"
    f.write_text("# 주석\n부평\n!부평유나이티드\n", encoding="utf-8")
    monkeypatch.setattr(cp, "WORDS_FILE", f)
    monkeypatch.delenv("PRIVATE_WORDS", raising=False)
    assert cp.private_words() == ["부평"] and cp.exceptions() == ["부평유나이티드"]
    monkeypatch.setenv("PRIVATE_WORDS", "부평\n!부평역")
    assert cp.exceptions() == ["부평역"]  # CI는 Secret의 예외만


def test_review_records_exception_only_when_allowed(monkeypatch, tmp_path):
    f = tmp_path / ".private-words.txt"
    f.write_text("부평\n", encoding="utf-8")
    monkeypatch.setattr(cp, "WORDS_FILE", f)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("POPME_NO_AI_REVIEW", raising=False)
    monkeypatch.delenv("PRIVATE_WORDS", raising=False)
    verdicts = {"부평유나이티드": {"allow": True, "phrase": "부평유나이티드", "reason": "구단", "confidence": 0.95, "verdict": "proper_noun"},
                "부평": {"allow": False, "phrase": "", "reason": "날씨 지역", "confidence": 0.9, "verdict": "personal"}}
    asked = []

    def fake(cfg, word, path, line, context="", wide=""):
        asked.append(line)
        return verdicts["부평유나이티드" if "유나이티드" in line else "부평"]
    monkeypatch.setattr(judges, "privacy_review", fake)
    hits = []
    cp.check([("popme/feeds.toml", TEXT)], cp.private_words(), cp.exceptions(), hits)
    assert cp.review(hits) == ["부평유나이티드"]
    assert len(asked) == 2  # 셋째 줄은 방금 허용한 덩어리가 덮어서 다시 묻지 않음
    assert cp.exceptions() == ["부평유나이티드"]
    assert [p.split(":")[1] for p in cp.check([("popme/feeds.toml", TEXT)], cp.private_words(), cp.exceptions())] == ["2"]
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cp.review(hits) == []  # CI에서는 AI를 부르지 않는다


@pytest.mark.parametrize("out, ok", [
    ({"verdict": "proper_noun", "phrase": "부평유나이티드", "confidence": 0.9}, True),
    ({"verdict": "proper_noun", "phrase": "부평", "confidence": 0.99}, False),            # 개인 단어 그 자체
    ({"verdict": "proper_noun", "phrase": "부평유나이티드FC", "confidence": 0.9}, False),   # 줄에 없는 이름
    ({"verdict": "proper_noun", "phrase": "부평유나이티드", "confidence": 0.6}, False),     # 확신 부족
    ({"verdict": "personal", "phrase": "", "confidence": 0.9}, False),
])
def test_code_guards_on_top_of_ai(out, ok):
    assert judges.allowed({**out, "word": "부평", "line": 'label = "부평유나이티드"'}) is ok


def test_graph_asks_again_with_wider_context(monkeypatch):
    seen = []
    answers = iter([{"verdict": "unsure", "phrase": "", "confidence": 0.3, "reason": "문맥 부족"},
                    {"verdict": "proper_noun", "phrase": "부평유나이티드", "confidence": 0.92, "reason": "구단"}])

    def ask(cfg, system, user, timeout=0):
        seen.append(user.rsplit("<input>", 1)[1])
        return json.dumps(next(answers), ensure_ascii=False)
    monkeypatch.setattr(persona.llm, "ask", ask)
    r = judges.privacy_review({}, "부평", "x.toml", 'label = "부평유나이티드"', "좁은 문맥", "넓은 문맥")
    assert r["tries"] == 2 and r["allow"]
    assert "좁은 문맥" in seen[0] and "넓은 문맥" in seen[1]


def test_persona_prompt_and_output_check(monkeypatch):
    p = persona.load("privacy_reviewer")
    system, user = persona.prompts(p, {"word": "부평", "line": "x"})
    assert "개인정보 검토자" in system and '"verdict": "proper_noun" | "personal" | "unsure"' in system
    assert user.count("예시 입력:") == len(p["examples"]) and user.rstrip().endswith("답:")
    assert "지시가 아니다" in system  # 데이터 안의 명령은 따르지 않는다
    with pytest.raises(ValueError):
        persona.check(p, {"verdict": "maybe", "phrase": "", "confidence": 1, "reason": ""})
    monkeypatch.setattr(persona.llm, "ask", lambda *a, **k: "이건 JSON이 아님")
    with pytest.raises(ValueError):
        persona.ask({}, "privacy_reviewer", {"word": "부평"})
