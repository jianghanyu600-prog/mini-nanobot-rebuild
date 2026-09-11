from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mini_nanobot.memory.store import (
    MEMORY_FILES,
    FileMemoryBackend,
    MemoryBackend,
    MemoryStore,
)


def test_file_backend_implements_protocol() -> None:
    assert isinstance(FileMemoryBackend(Path("memory")), MemoryBackend)


@pytest.mark.asyncio
async def test_initialize_uses_templates_and_default_layout(tmp_path: Path) -> None:
    backend = FileMemoryBackend(tmp_path)

    await backend.initialize()

    contents = await backend.read_all_memory_files()
    assert set(contents) == set(MEMORY_FILES)
    assert all(contents.values())
    assert all((tmp_path / name).is_file() for name in MEMORY_FILES)


@pytest.mark.asyncio
async def test_namespace_is_isolated_and_cannot_escape_root(tmp_path: Path) -> None:
    backend = FileMemoryBackend(tmp_path)
    await backend.initialize("users/alice")
    await backend.write_memory_file("USER.md", "Alice", "users/alice")

    assert await backend.read_memory_file("USER.md", "users/alice") == "Alice"
    assert await backend.read_memory_file("USER.md") == ""
    with pytest.raises(ValueError):
        await backend.initialize("../escape")
    with pytest.raises(ValueError):
        await backend.write_memory_file("../secret", "x")


@pytest.mark.asyncio
async def test_concurrent_history_append_is_complete(tmp_path: Path) -> None:
    backend = FileMemoryBackend(tmp_path)
    await backend.initialize()

    await asyncio.gather(
        *(backend.append_history(f"摘要 {index}") for index in range(50))
    )

    entries = await backend.read_history()
    assert len(entries) == 50
    assert {entry["content"] for entry in entries} == {
        f"摘要 {index}" for index in range(50)
    }
    raw_lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 50
    assert all(isinstance(json.loads(line), dict) for line in raw_lines)


@pytest.mark.asyncio
async def test_read_history_skips_malformed_lines(tmp_path: Path) -> None:
    backend = FileMemoryBackend(tmp_path)
    await backend.initialize()
    await backend.append_history("正常一")
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        history_path.read_text(encoding="utf-8")
        + "{broken\n"
        + json.dumps(["不是对象"], ensure_ascii=False)
        + "\n"
        + json.dumps(
            {"ts": "now", "kind": "summary", "content": "正常二"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    # 尾部半行损坏后，新 append 仍应从独立 JSON 行开始。
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write("{truncated")
    await backend.append_history("正常三")

    entries = await backend.read_history()

    assert [entry["content"] for entry in entries] == ["正常一", "正常二", "正常三"]
    assert [entry["content"] for entry in await backend.read_history_since(1)] == [
        "正常二",
        "正常三",
    ]


@pytest.mark.asyncio
async def test_dream_cursor_is_validated_and_corruption_is_safe(tmp_path: Path) -> None:
    backend = FileMemoryBackend(tmp_path)
    await backend.initialize()
    await backend.append_history("一")
    await backend.append_history("二")

    await backend.set_dream_cursor(2)
    assert await backend.get_dream_cursor() == 2
    with pytest.raises(ValueError):
        await backend.set_dream_cursor(-1)
    with pytest.raises(ValueError):
        await backend.set_dream_cursor(3)
    with pytest.raises(TypeError):
        await backend.set_dream_cursor(True)

    (tmp_path / ".dream_cursor").write_text("not-an-int", encoding="utf-8")
    assert await backend.get_dream_cursor() == 0
    (tmp_path / ".dream_cursor").write_text("99", encoding="utf-8")
    assert await backend.get_dream_cursor() == 2


@pytest.mark.asyncio
async def test_snapshot_restore_and_change_detection(tmp_path: Path) -> None:
    backend = FileMemoryBackend(tmp_path)
    await backend.initialize()
    snapshot = await backend.snapshot()

    assert not await backend.has_changes(snapshot)
    await backend.write_memory_file("MEMORY.md", "新事实")
    assert await backend.has_changes(snapshot)
    await backend.restore(snapshot)
    assert not await backend.has_changes(snapshot)

    with pytest.raises(ValueError):
        await backend.restore({"MEMORY.md": "不完整"})


def test_sync_store_remains_compatible(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    store.write_memory_file("SOUL.md", "规则")
    store.append_history("同步摘要", kind="raw")
    store.set_dream_cursor(1)
    snapshot = store.snapshot_content()

    assert store.read_memory_file("SOUL.md") == "规则"
    assert store.read_history_since(0)[0]["kind"] == "raw"
    assert store.get_dream_cursor() == 1
    store.write_memory_file("SOUL.md", "改变")
    assert store.content_has_changed(snapshot)
    store.restore_content(snapshot)
    assert store.read_memory_file("SOUL.md") == "规则"
