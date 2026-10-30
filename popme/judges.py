"""페르소나 판단 흐름 (LangGraph). 노드는 그냥 파이썬 함수 — AI는 popme.persona.ask로 지금 쓰는 구독 CLI를 부른다.

privacy_review: 개인 단어가 걸린 줄 → [개인정보 검토자] → 확신이 낮으면 앞뒤 줄을 더 붙여 한 번 더 → 결정.
"""
from typing import TypedDict

from langgraph.graph import END, StateGraph

from popme import persona

ALLOW_AT = 0.8  # 이 확신 이상이고 고유명사일 때만 예외로 남긴다
MAX_TRIES = 2


class Review(TypedDict, total=False):
    word: str
    path: str
    line: str
    context: str      # 앞뒤 2줄
    wide: str         # 다시 물을 때 쓰는 앞뒤 8줄
    tries: int
    verdict: str
    phrase: str
    confidence: float
    reason: str


def _graph(cfg):
    def judge(s):
        out = persona.ask(cfg, "privacy_reviewer", {"word": s["word"], "path": s["path"], "line": s["line"],
                                                    "context": s["wide"] if s.get("tries") else s["context"]})
        return {**out, "tries": s.get("tries", 0) + 1}  # 형·길이는 스키마(schemas.PrivacyVerdict)가 정리

    def again(s):  # 애매하면 앞뒤를 더 보여 주고 한 번 더 (최대 MAX_TRIES번)
        unsure = s["verdict"] == "unsure" or (s["verdict"] == "proper_noun" and s["confidence"] < ALLOW_AT)
        return "judge" if unsure and s["tries"] < MAX_TRIES and s.get("wide") else END

    g = StateGraph(Review)
    g.add_node("judge", judge)
    g.set_entry_point("judge")
    g.add_conditional_edges("judge", again, {"judge": "judge", END: END})
    return g.compile()


def allowed(s):
    """예외로 남겨도 되나 — AI 판단 + 코드가 지키는 선: 고유명사·확신·그 이름이 줄에 그대로 있고 개인 단어보다 길어야 한다."""
    word, phrase = s.get("word", ""), s.get("phrase", "")
    return (s.get("verdict") == "proper_noun" and s.get("confidence", 0) >= ALLOW_AT and word.lower() in phrase.lower()
            and len(phrase) > len(word) and phrase in s.get("line", ""))


def privacy_review(cfg, word, path, line, context="", wide=""):
    """→ {verdict, phrase, confidence, reason, tries, allow}."""
    out = _graph(cfg).invoke({"word": word, "path": path, "line": line, "context": context, "wide": wide, "tries": 0})
    return {**out, "allow": allowed(out)}
