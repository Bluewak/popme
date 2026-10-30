"""페르소나 답의 모양 (pydantic). 페르소나 파일의 `schema = "이름"`이 여기 클래스를 가리킨다 (설계 4-10).

[output]은 AI에게 보여 줄 답 형식, 스키마는 받은 답을 검사·정리하는 코드 쪽 약속. 형이 조금 틀린 건 고쳐 받고(「0.9」 → 0.9),
허용값 밖이면 ValueError (pydantic ValidationError도 ValueError) → persona.ask가 한 번 다시 묻는다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


def _unit(v):
    """0~1 확신. 숫자가 아니면 0, 95처럼 백분율로 오면 0.95로."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if 1 < v <= 100:
        v /= 100
    return min(max(v, 0.0), 1.0)


def _text(n):
    return lambda v: " ".join(str(v or "").split())[:n]


class PrivacyVerdict(BaseModel):
    """개인정보 검토자 (popme/personas/privacy_reviewer.toml)."""
    model_config = ConfigDict(extra="ignore")
    verdict: Literal["proper_noun", "personal", "unsure"]
    phrase: str = ""
    confidence: float = 0.0
    reason: str = ""

    _conf = field_validator("confidence", mode="before")(_unit)
    _phrase = field_validator("phrase", mode="before")(_text(80))
    _reason = field_validator("reason", mode="before")(_text(60))


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    status: Literal["supported", "contradicted", "unknown"]
    note: str = ""

    _note = field_validator("note", mode="before")(_text(30))


class Pick(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    category: str = ""

    _cat = field_validator("category", mode="before")(_text(12))


class EditorPicks(BaseModel):
    """편집자 (popme/personas/editor.toml)."""
    model_config = ConfigDict(extra="ignore")
    picks: list[Pick] = []


class Taste(BaseModel):
    """취향 분석가 (popme/personas/taste_analyst.toml)."""
    model_config = ConfigDict(extra="ignore")
    likes: list[str] = []
    less: list[str] = []
    summary: str = ""

    _summary = field_validator("summary", mode="before")(_text(80))

    @field_validator("likes", "less", mode="before")
    @classmethod
    def _names(cls, v):
        return [" ".join(str(x).split())[:12] for x in (v or []) if str(x).strip()][:6]


class FactCheck(BaseModel):
    """사실 확인 (popme/personas/fact_checker.toml)."""
    model_config = ConfigDict(extra="ignore")
    verdicts: list[Verdict] = []
