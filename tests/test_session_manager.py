"""独立会话管理基础测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mini_nanobot.session import RunStatus, SessionManager


def test_create_activate_delete_and_reload(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    first = manager.create("第一段对话")
    second = manager.create("第二段对话")

    assert first.thread_id != second.thread_id
    assert manager.get(first.thread_id) == first
    assert manager.active_thread_id == first.thread_id
    assert {item.thread_id for item in manager.list()} == {
        first.thread_id,
        second.thread_id,
    }

    manager.activate(second.thread_id)
    reloaded = SessionManager(tmp_path)
    assert reloaded.active_thread_id == second.thread_id
    assert reloaded.get(first.thread_id) == first
    assert reloaded.get_active() == second

    assert reloaded.delete(second.thread_id) is True
    assert reloaded.active_thread_id == first.thread_id
    assert reloaded.delete("missing") is False

    final = SessionManager(tmp_path)
    assert final.active_thread_id == first.thread_id
    assert final.list() == [first]


def test_metadata_is_valid_atomic_json(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create("中文标题", thread_id="stable-thread")
    manager.activate(session.thread_id)

    metadata_path = tmp_path / "sessions.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["active_thread_id"] == "stable-thread"
    assert payload["sessions"]["stable-thread"] == {
        "thread_id": "stable-thread",
        "title": "中文标题",
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }
    assert list(tmp_path.glob("*.tmp")) == []


def test_pending_queue_fifo_limit_and_deduplication(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()

        first = await manager.enqueue(
            session.thread_id,
            "user_message",
            "你好",
            {"channel": "console"},
            event_id="event-1",
        )
        duplicate = await manager.enqueue(
            session.thread_id,
            "user_message",
            "不会重复入队",
            event_id="event-1",
        )
        # 执行锁被持有时，新事件仍应能进入 pending 队列。
        async with manager.lock_for(session.thread_id):
            second = await asyncio.wait_for(
                manager.enqueue(session.thread_id, "system", "继续"),
                timeout=0.5,
            )

        assert duplicate == first
        assert first.metadata == {"channel": "console"}
        assert first.event_id != second.event_id
        assert manager.pending_count(session.thread_id) == 2
        assert await manager.drain(session.thread_id, limit=1) == [first]
        assert await manager.drain(session.thread_id) == [second]
        assert await manager.drain(session.thread_id) == []

        # 已消费的 event_id 也不能再次进入队列。
        await manager.enqueue(
            session.thread_id,
            "user_message",
            "仍不重复",
            event_id="event-1",
        )
        assert manager.pending_count(session.thread_id) == 0

    asyncio.run(scenario())


def test_each_session_has_independent_lock(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    first = manager.create()
    second = manager.create()

    assert manager.lock_for(first.thread_id) is manager.lock_for(first.thread_id)
    assert manager.lock_for(first.thread_id) is not manager.lock_for(second.thread_id)


def test_run_status_and_cancellation_handle(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(wait_forever())
        manager.start_run(session.thread_id, task)
        assert manager.run_status(session.thread_id) is RunStatus.RUNNING
        assert manager.run_task(session.thread_id) is task
        assert manager.cancel_run(session.thread_id) is True
        assert manager.run_status(session.thread_id) is RunStatus.CANCELLING

        with pytest.raises(asyncio.CancelledError):
            await task
        manager.finish_run(session.thread_id)
        assert manager.run_status(session.thread_id) is RunStatus.IDLE
        assert manager.run_task(session.thread_id) is None
        assert manager.cancel_run(session.thread_id) is False

    asyncio.run(scenario())


def test_unknown_session_and_invalid_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()

        with pytest.raises(KeyError):
            manager.activate("missing")
        with pytest.raises(KeyError):
            manager.lock_for("missing")
        with pytest.raises(ValueError):
            await manager.drain(session.thread_id, limit=-1)

    asyncio.run(scenario())
