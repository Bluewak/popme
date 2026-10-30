"""구독 CLI(claude/codex/gemini)를 LangChain 채팅 모델 하나로 감싼다 (설계 4-10).

메시지·체인으로 다루되 실제 호출은 popme.llm.ask 그대로 — 도구(셸·파일·웹)는 늘 꺼져 있고 API 키도 쓰지 않는다.
CLI라 도구 호출(tool calling)·스트리밍은 없다. 검색·수집은 그래프 단계가 직접 하고, AI는 받은 자료로 판단만 한다.
"""
import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from popme import llm

# LangChain·LangGraph가 외부(LangSmith)로 흐름을 보내지 않게 — 기본도 꺼짐이지만 확실히 (LangSmith는 개발 도구에서만)
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

ROLE = {"human": "사용자", "ai": "AI"}


class CliChatModel(BaseChatModel):
    """cfg의 [llm] provider로 고른 구독 CLI. 시스템 메시지는 시스템 프롬프트로, 나머지는 한 덩어리 프롬프트로."""
    cfg: dict = {}
    timeout: int = 120

    @property
    def _llm_type(self) -> str:
        return f"popme-cli-{llm.provider(self.cfg)}"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = "\n\n".join(m.content for m in messages if isinstance(m, SystemMessage))
        rest = [m for m in messages if not isinstance(m, SystemMessage)]
        if len(rest) == 1:
            prompt = rest[0].content
        else:  # 주고받은 말이 여럿이면 누가 한 말인지 붙여 한 덩어리로
            prompt = "\n\n".join(f"{ROLE.get(m.type, m.type)}: {m.content}" for m in rest)
        text = llm.ask(self.cfg, system, prompt, timeout=self.timeout)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])
