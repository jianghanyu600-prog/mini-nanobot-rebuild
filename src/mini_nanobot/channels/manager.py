from __future__ import annotations

import asyncio

from ..bus import MessageBus
from .base import BaseChannel


class ChannelManager:
    def __init__(self, bus: MessageBus, channels: list[BaseChannel]) -> None:
        self.bus = bus
        self.channels = {c.name: c for c in channels}
        self._tasks: list[asyncio.Task[None]] = []
        self._channel_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        for channel in self.channels.values():
            task = asyncio.create_task(
                channel.start(),
                name=f"channel:{channel.name}",
            )
            self._channel_tasks.append(task)
            self._tasks.append(task)
        self._tasks.append(asyncio.create_task(self._dispatch_outbound()))

    async def stop(self) -> None:
        for channel in self.channels.values():
            await channel.stop()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _dispatch_outbound(self) -> None:
        while True:
            msg = await self.bus.consume_outbound()
            channel = self.channels.get(msg.channel)
            if channel is None:
                continue
            if msg.event == "delta":
                await channel.send_delta(msg.session_id, msg.content, msg.metadata)
            elif msg.event == "stream_end":
                await channel.send_delta_end(msg.session_id, msg.metadata)
            else:
                await channel.send(msg.session_id, msg.content, msg.metadata)

    async def wait_until_all_stopped(self) -> None:
        if self._channel_tasks:
            await asyncio.gather(*self._channel_tasks)