"""独立子代理任务管理测试。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from mini_nanobot.subagents import SubagentManager, SubagentStatus
from mini_nanobot.tools.spawn import make_spawn_tool


class FakeSessions:
    """记录注入事件的内存会话队列。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail = fail

    async def enqueue(
        self,
        thread_id: str,
        event_type: str,
        content: Any,
        metadata: Any = None,
        *,
        event_id: str | None = None,
    ) -> None:
        if self.fail:
            raise RuntimeError("队列不可用")
        self.events.append(
            {
                "thread_id": thread_id,
                "event_type": event_type,
                "content": content,
                "metadata": metadata,
                "event_id": event_id,
            }
        )


class FakeRunner:
    """通过注入的协程执行测试场景。"""

    def __init__(self, run) -> None:
        self._run = run

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return await self._run(input)


def make_manager(
    run,
    sessions: FakeSessions | None = None,
    *,
    max_concurrent: int = 3,
    timeout: float = 1,
    tools=(),
) -> tuple[SubagentManager, FakeSessions]:
    """建立不访问真实模型的 Manager。"""
    queue = sessions or FakeSessions()
    manager = SubagentManager(
        None,  # type: ignore[arg-type]
        tools,
        queue,
        max_concurrent=max_concurrent,
        timeout=timeout,
        runner_factory=lambda _model, _tools: FakeRunner(run),
    )
    return manager, queue


async def wait_done(manager: SubagentManager, task_id: str) -> None:
    """等待任务进入最终状态。"""
    for _ in range(200):
        record = manager.get(task_id)
        assert record is not None
        if record.status in {
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.CANCELLED,
        }:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("子任务未在预期时间内结束")


def test_spawn_immediately_returns_task_id_from_config() -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        async def run(_input):
            await release.wait()
            return {"messages": [AIMessage(content="完成")]}

        manager, _queue = make_manager(run)
        spawn = make_spawn_tool(manager)
        result = await asyncio.wait_for(
            spawn.ainvoke(
                {"task": "后台任务"},
                config={"configurable": {"thread_id": "parent-1"}},
            ),
            timeout=0.1,
        )

        task_id = result.rsplit("：", 1)[1]
        assert manager.get(task_id) is not None
        assert manager.get(task_id).parent_session_id == "parent-1"  # type: ignore[union-attr]
        release.set()
        await wait_done(manager, task_id)
        await manager.close()

    asyncio.run(scenario())


def test_concurrency_limit() -> None:
    async def scenario() -> None:
        active = 0
        peak = 0
        release = asyncio.Event()

        async def run(_input):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await release.wait()
            active -= 1
            return {"messages": [AIMessage(content="完成")]}

        manager, _queue = make_manager(run, max_concurrent=2)
        records = [manager.submit("parent", f"任务 {index}") for index in range(5)]
        await asyncio.sleep(0.03)
        assert peak == 2
        release.set()
        for record in records:
            await wait_done(manager, record.task_id)
        await manager.close()

    asyncio.run(scenario())


def test_success_injects_stable_event_and_filters_recursive_tools() -> None:
    async def scenario() -> None:
        captured_names: list[str] = []

        @tool
        def spawn() -> str:
            """不应传给子代理。"""
            return ""

        @tool
        def lookup() -> str:
            """允许传给子代理。"""
            return ""

        async def run(_input):
            return {"messages": [AIMessage(content="查询结果")]}

        queue = FakeSessions()

        def factory(_model, safe_tools):
            captured_names.extend(item.name for item in safe_tools)
            return FakeRunner(run)

        manager = SubagentManager(
            None,  # type: ignore[arg-type]
            [spawn, lookup],
            queue,
            runner_factory=factory,
        )
        record = manager.submit("parent", "查询")
        await wait_done(manager, record.task_id)

        assert captured_names == ["lookup"]
        assert record.status is SubagentStatus.COMPLETED
        assert record.result == "查询结果"
        assert queue.events[0]["event_id"] == f"subagent:{record.task_id}:result"
        assert queue.events[0]["event_type"] == "subagent_result"
        assert "已完成" in queue.events[0]["content"]
        await manager.close()

    asyncio.run(scenario())


def test_timeout_and_runner_failure_are_recorded_and_injected() -> None:
    async def scenario() -> None:
        async def slow(_input):
            await asyncio.sleep(1)

        timeout_manager, timeout_queue = make_manager(slow, timeout=0.01)
        timed_out = timeout_manager.submit("parent", "慢任务")
        await wait_done(timeout_manager, timed_out.task_id)
        assert timed_out.status is SubagentStatus.FAILED
        assert "超时" in (timed_out.error or "")
        assert "执行失败" in timeout_queue.events[0]["content"]
        await timeout_manager.close()

        async def broken(_input):
            raise ValueError("模型故障")

        failed_manager, failed_queue = make_manager(broken)
        failed = failed_manager.submit("parent", "失败任务")
        await wait_done(failed_manager, failed.task_id)
        assert failed.status is SubagentStatus.FAILED
        assert failed.error == "ValueError：模型故障"
        assert failed_queue.events[0]["metadata"]["status"] == "failed"
        await failed_manager.close()

    asyncio.run(scenario())


def test_cancel_and_close_cancel_background_tasks() -> None:
    async def scenario() -> None:
        async def forever(_input):
            await asyncio.Event().wait()

        manager, queue = make_manager(forever, max_concurrent=1)
        first = manager.submit("parent", "运行中")
        second = manager.submit("parent", "排队中")
        await asyncio.sleep(0.01)

        assert manager.cancel(first.task_id) is True
        await wait_done(manager, first.task_id)
        assert first.status is SubagentStatus.CANCELLED
        assert manager.cancel(first.task_id) is False

        await manager.close()
        assert second.status is SubagentStatus.CANCELLED
        assert len(queue.events) == 2
        assert manager.list() == [first, second]

    asyncio.run(scenario())


def test_injection_failure_is_recorded_without_changing_success() -> None:
    async def scenario() -> None:
        async def run(_input):
            return {"messages": [AIMessage(content="完成")]}

        manager, _queue = make_manager(run, FakeSessions(fail=True))
        record = manager.submit("parent", "任务")
        await wait_done(manager, record.task_id)
        await manager.close()

        assert record.status is SubagentStatus.COMPLETED
        assert "队列不可用" in (record.injection_error or "")

    asyncio.run(scenario())
