from __future__ import annotations

from pathlib import Path

import pytest

from mini_nanobot.config import AppConfig, ProviderConfig
from mini_nanobot.graph import create_app
from mini_nanobot.memory import FileMemoryBackend
from mini_nanobot.session import SessionManager
from mini_nanobot.subagents import SubagentManager


@pytest.mark.asyncio
async def test_runtime_can_compile_without_calling_provider(tmp_path: Path) -> None:
    """真实组装 create_agent、外层图、SQLite 和后台管理器，但不访问网络。"""
    cfg = AppConfig(
        provider=ProviderConfig(api_key="test-key", model="test-model"),
        workspace_dir=tmp_path,
        mcp_config_path="",
    )

    async with create_app(cfg) as runtime:
        assert runtime.graph is not None
        assert isinstance(runtime.memory, FileMemoryBackend)
        assert isinstance(runtime.sessions, SessionManager)
        assert isinstance(runtime.subagents, SubagentManager)
        assert runtime.run_timeout_seconds == cfg.run_timeout_seconds

    assert runtime.subagents.list() == []
