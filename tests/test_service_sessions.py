"""运行时会话接入、并发、pending 与取消测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from mini_nanobot.bus import InboundMessage, MessageBus
from mini_nanobot.channels.console import ConsoleChannel
from mini_nanobot.service import AgentService
from mini_nanobot.session import RunStatus, SessionManager


class _Hooks:
    async def before_run(self, session_id: str) -> None:
        return None

    async def after_run(self, session_id: str, content: str) -> None:
        return None

    async def on_finally(self, session_id: str) -> None:
        return None


class _BlockingGraph:
    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.contexts: list[Any] = []

    async def ainvoke(self, data: dict, *, config: dict, context: Any) -> dict:
        session_id = config["configurable"]["thread_id"]
        self.contexts.append(context)
        self.started.setdefault(session_id, asyncio.Event()).set()
        await asyncio.Event().wait()
        return {"messages": [AIMessage(content="不会到达")]}

    async def aget_state(self, config: dict) -> Any:
        return SimpleNamespace(values={"messages": []})


class _ImmediateGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, Any]] = []

    async def ainvoke(self, data: dict, *, config: dict, context: Any) -> dict:
        self.calls.append((data, config, context))
        return {"messages": [AIMessage(content="完成")]}

    async def aget_state(self, config: dict) -> Any:
        return SimpleNamespace(values={"messages": [AIMessage(content="完成")]})


class _StreamingGraph:
    def __init__(self, *, with_delta: bool) -> None:
        self.with_delta = with_delta
        self.contexts: list[Any] = []

    async def astream(self, data: dict, *, config: dict, context: Any, **kwargs: Any):
        self.contexts.append(context)
        if self.with_delta:
            yield (
                ("agent:测试",),
                (AIMessageChunk(content="增量"), {"langgraph_node": "model"}),
            )

    async def aget_state(self, config: dict) -> Any:
        return SimpleNamespace(values={"messages": [AIMessage(content="最终回复")]})


def _runtime(tmp_path: Path, graph: Any) -> SimpleNamespace:
    return SimpleNamespace(
        graph=graph,
        memory=object(),
        sessions=SessionManager(tmp_path),
        hooks=_Hooks(),
        consolidator=object(),
        llm=object(),
        run_timeout_seconds=10.0,
        dream_auto_threshold=0,
    )


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def test_console_restores_and_persists_active_session(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    active = sessions.create("已有会话")
    console = ConsoleChannel(MessageBus(), sessions)

    console._restore_active_session()
    assert console.session_id == active.thread_id

    console._create_session()
    new_session_id = console.session_id
    assert new_session_id != active.thread_id
    assert SessionManager(tmp_path).active_thread_id == new_session_id


def test_service_runs_sessions_concurrently_and_queues_same_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        bus = MessageBus()
        graph = _BlockingGraph()
        runtime = _runtime(tmp_path, graph)
        first = runtime.sessions.create("第一会话")
        second = runtime.sessions.create("第二会话")
        service = AgentService(bus, runtime)
        service_task = asyncio.create_task(service.run())

        await bus.publish_inbound(InboundMessage("test", first.thread_id, "第一条"))
        await _wait_until(lambda: first.thread_id in graph.started)
        await bus.publish_inbound(InboundMessage("test", second.thread_id, "并发消息"))
        await _wait_until(lambda: second.thread_id in graph.started)

        await bus.publish_inbound(InboundMessage("test", first.thread_id, "运行中补充"))
        await _wait_until(lambda: runtime.sessions.pending_count(first.thread_id) == 1)
        assert runtime.sessions.run_status(first.thread_id) is RunStatus.RUNNING
        assert runtime.sessions.run_status(second.thread_id) is RunStatus.RUNNING

        await bus.publish_inbound(InboundMessage("test", first.thread_id, "/status"))
        status = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
        assert "运行状态：running" in status.content
        assert "待处理消息：1" in status.content
        assert "迭代计数" not in status.content

        await bus.publish_inbound(InboundMessage("test", first.thread_id, "/stop"))
        await bus.publish_inbound(InboundMessage("test", second.thread_id, "/stop"))
        await _wait_until(
            lambda: runtime.sessions.run_status(first.thread_id) is RunStatus.IDLE
            and runtime.sessions.run_status(second.thread_id) is RunStatus.IDLE
        )
        assert service_task.done() is False

        service_task.cancel()
        await asyncio.gather(service_task, return_exceptions=True)

    asyncio.run(scenario())


def test_goal_and_regular_messages_receive_explicit_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        bus = MessageBus()
        graph = _ImmediateGraph()
        runtime = _runtime(tmp_path, graph)
        session = runtime.sessions.create()
        service = AgentService(bus, runtime)

        goal = InboundMessage("test", session.thread_id, "/goal 整理项目")
        await service._dispatch(goal)
        await _wait_until(lambda: not service._tasks)
        goal_data, _, goal_context = graph.calls[-1]
        assert goal_context.session_id == session.thread_id
        assert goal_context.memory is runtime.memory
        assert goal_context.pending is runtime.sessions
        assert goal_context.goal_creation_allowed is True
        assert goal_data["messages"][0].content == "请创建并持续执行以下目标：\n整理项目"

        regular = InboundMessage("test", session.thread_id, "你好")
        await service._dispatch(regular)
        await _wait_until(lambda: not service._tasks)
        assert graph.calls[-1][2].goal_creation_allowed is False

    asyncio.run(scenario())


def test_streaming_handles_nested_events_and_final_fallback(tmp_path: Path) -> None:
    async def scenario() -> None:
        for with_delta in (True, False):
            bus = MessageBus()
            graph = _StreamingGraph(with_delta=with_delta)
            runtime = _runtime(tmp_path / str(with_delta), graph)
            session = runtime.sessions.create()
            service = AgentService(bus, runtime)
            msg = InboundMessage(
                "test",
                session.thread_id,
                "你好",
                {"supports_stream": True},
            )

            await service._dispatch(msg)
            await _wait_until(lambda svc=service: not svc._tasks)
            events = [await bus.consume_outbound(), await bus.consume_outbound()]

            assert graph.contexts[0].pending is runtime.sessions
            assert events[-1].event == "stream_end"
            if with_delta:
                assert (events[0].event, events[0].content) == ("delta", "增量")
            else:
                assert (events[0].event, events[0].content) == ("final", "最终回复")

    asyncio.run(scenario())
