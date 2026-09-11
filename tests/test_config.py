from __future__ import annotations

import pytest
from pydantic import ValidationError

from mini_nanobot.config import ProviderConfig


def test_rejects_workspace_placeholder() -> None:
    """api_base 里还带着 [workspace-id] 占位符 → 必须报错。"""
    with pytest.raises(ValidationError, match="占位符"):
        ProviderConfig(
            api_key="sk-test",
            api_base="https://[workspace-id].example.com/v1",
        )


def test_strips_trailing_slash() -> None:
    """尾斜杠应该被去掉，方便和后面的路径拼接。"""
    cfg = ProviderConfig(
        api_key="sk-test",
        api_base="https://api.example.com/v1/",
    )
    assert cfg.api_base == "https://api.example.com/v1"


def test_env_default_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """从环境变量读出来的默认值也必须过校验（validate_default=True）。"""
    monkeypatch.setenv(
        "OPENAI_API_BASE",
        "https://[workspace-id].example.com/v1",
    )
    with pytest.raises(ValidationError, match="占位符"):
        ProviderConfig()
