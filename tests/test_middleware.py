from __future__ import annotations

from pathlib import Path

import pytest
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.runtime import Runtime

from mini_nanobot.memory.store import FileMemoryBackend
from mini_nanobot.middleware import (
    EmptyResponseRecoveryMiddleware,
    PendingInjectionMiddleware,
    SummaryArchiveMiddleware,
)
from mini_nanobot.session import SessionManager
from mini_nanobot.state import AgentContext


class _FakeMemory:
    def __init__(self) -> None:
        self.history: list[dict] = []

    def read_all_memory_files(self) -> dict[str, str]:
        return {"SOUL.md": "", "USER.md": "", "MEMORY.md": ""}

    def append_history(self, summary: str, *, kind: str = "summary") -> dict:
        entry = {"kind": kind, "content": summary}
        self.history.append(entry)
        return entry


class _SummaryModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "test-summary"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="保留的重要历史摘要"))]
        )


def _runtime(memory: _FakeMemory | None = None) -> Runtime:
    return Runtime(
        context=AgentContext(session_id="thread", memory=memory or _FakeMemory())
    )


@pytest.mark.asyncio
async def test_pending_injection_drains_session_events(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.create()
    memory = FileMemoryBackend(tmp_path / "memory")
    await memory.initialize()
    await sessions.enqueue(session.thread_id, "subagent_result", "后台分析完成", event_id="e1")
    runtime = Runtime(
        context=AgentContext(
            session_id=session.thread_id,
            memory=memory,
            pending=sessions,
        )
    )

    update = await PendingInjectionMiddleware().abefore_model(
        {"messages": [HumanMessage(content="开始")]},
        runtime,
    )

    assert update is not None
    assert "后台分析完成" in update["messages"][0].content
    assert update["messages"][0].additional_kwargs["event_id"] == "e1"
    assert sessions.pending_count(session.thread_id) == 0


@pytest.mark.asyncio
async def test_empty_response_jumps_then_gives_up() -> None:
    mw = EmptyResponseRecoveryMiddleware()
    runtime = _runtime()
    blank = AIMessage(content="   ")

    await mw.abefore_agent({"messages": []}, runtime)

    first = await mw.aafter_model(
        {"messages": [blank], "empty_response_count": 0}, runtime
    )
    assert first is not None
    assert first["jump_to"] == "model"
    assert first["empty_response_count"] == 1

    second = await mw.aafter_model(
        {"messages": [blank], "empty_response_count": 1}, runtime
    )
    assert second is not None
    assert second["jump_to"] == "model"

    third = await mw.aafter_model(
        {"messages": [blank], "empty_response_count": 2}, runtime
    )
    assert third is None


@pytest.mark.asyncio
async def test_non_blank_resets_empty_count() -> None:
    mw = EmptyResponseRecoveryMiddleware()
    runtime = _runtime()
    update = await mw.aafter_model(
        {"messages": [AIMessage(content="你好")], "empty_response_count": 1},
        runtime,
    )
    assert update == {"empty_response_count": 0}


@pytest.mark.asyncio
async def test_summary_is_archived_once() -> None:
    memory = _FakeMemory()
    runtime = _runtime(memory)
    archive = SummaryArchiveMiddleware()
    summary = HumanMessage(
        content="保留的重要历史摘要",
        additional_kwargs={"lc_source": "summarization"},
    )
    state = {"messages": [summary]}

    first = await archive.abefore_model(state, runtime)
    assert first is not None
    second = await archive.abefore_model(
        {**state, "last_archived_summary_hash": first["last_archived_summary_hash"]},
        runtime,
    )

    assert second is None
    assert len(memory.history) == 1
    assert memory.history[0]["kind"] == "summary"


@pytest.mark.asyncio
async def test_generated_summary_is_archived_once(tmp_path: Path) -> None:
    memory = FileMemoryBackend(tmp_path / "memory")
    await memory.initialize()
    runtime = Runtime(context=AgentContext(session_id="thread", memory=memory))
    summarizer = SummarizationMiddleware(
        _SummaryModel(),
        trigger=("tokens", 5),
        keep=("messages", 1),
    )
    archive = SummaryArchiveMiddleware()
    state = {
        "messages": [
            HumanMessage(content="很长的历史消息一，需要被压缩"),
            AIMessage(content="很长的历史回复一，需要被压缩"),
            HumanMessage(content="最近消息需要保留"),
        ]
    }
    compacted = await summarizer.abefore_model(state, runtime)
    assert compacted is not None
    messages = [
        message for message in compacted["messages"] if not isinstance(message, RemoveMessage)
    ]
    compacted_state = {"messages": messages}

    first = await archive.abefore_model(compacted_state, runtime)
    assert first is not None
    second = await archive.abefore_model(
        {
            **compacted_state,
            "last_archived_summary_hash": first["last_archived_summary_hash"],
        },
        runtime,
    )

    assert second is None
    history = await memory.read_history()
    assert len(history) == 1
    assert history[0]["kind"] == "summary"
    assert "重要历史摘要" in history[0]["content"]
