from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from mini_nanobot.memory import dream
from mini_nanobot.memory.store import FileMemoryBackend


class _FakeDreamAgent:
    def __init__(self, tools: list[Any], *, mode: str) -> None:
        self.tools = {item.name: item for item in tools}
        self.mode = mode

    async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "write":
            await self.tools["write_memory_file"].ainvoke(
                {"name": "USER.md", "content": "# USER\n\n- 用户偏好中文。"}
            )
        elif self.mode == "fail":
            await self.tools["write_memory_file"].ainvoke(
                {"name": "USER.md", "content": "不应保留的半成品"}
            )
            raise RuntimeError("模拟 Dream 中断")
        return {"messages": [AIMessage(content="已整理用户偏好。")]}


async def _prepared_backend(tmp_path: Path) -> FileMemoryBackend:
    backend = FileMemoryBackend(tmp_path)
    await backend.initialize()
    await backend.append_history("用户偏好使用中文。")
    return backend


@pytest.mark.asyncio
async def test_dream_updates_memory_and_advances_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _prepared_backend(tmp_path)
    monkeypatch.setattr(
        dream,
        "create_agent",
        lambda _llm, *, tools, **_kwargs: _FakeDreamAgent(tools, mode="write"),
    )

    result = await dream.run_dream(object(), backend)  # type: ignore[arg-type]

    assert "推进游标" in result
    assert "用户偏好中文" in await backend.read_memory_file("USER.md")
    assert await backend.get_dream_cursor() == 1


@pytest.mark.asyncio
async def test_dream_no_change_does_not_advance_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _prepared_backend(tmp_path)
    monkeypatch.setattr(
        dream,
        "create_agent",
        lambda _llm, *, tools, **_kwargs: _FakeDreamAgent(tools, mode="noop"),
    )

    result = await dream.run_dream(object(), backend)  # type: ignore[arg-type]

    assert "没有产生长期记忆变化" in result
    assert await backend.get_dream_cursor() == 0


@pytest.mark.asyncio
async def test_dream_failure_restores_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _prepared_backend(tmp_path)
    before = await backend.snapshot()
    monkeypatch.setattr(
        dream,
        "create_agent",
        lambda _llm, *, tools, **_kwargs: _FakeDreamAgent(tools, mode="fail"),
    )

    with pytest.raises(RuntimeError, match="模拟 Dream 中断"):
        await dream.run_dream(object(), backend)  # type: ignore[arg-type]

    assert await backend.snapshot() == before
    assert await backend.get_dream_cursor() == 0
