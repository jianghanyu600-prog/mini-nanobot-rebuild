from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..bus import InboundMessage, MessageBus


class BaseChannel(ABC):
    name: str = "base"
    supports_streaming: bool = False

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self._running = False

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(
        self,
        session_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def send_delta(
        self,
        session_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return

    async def send_delta_end(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return

    async def _handle_message(
        self,
        session_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = dict(metadata or {})
        meta.setdefault("supports_stream", self.supports_streaming)
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                session_id=session_id,
                content=content,
                metadata=meta,
            )
        )

    @property
    def is_running(self) -> bool:
        return self._running
