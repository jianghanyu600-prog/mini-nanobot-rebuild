import asyncio

import pytest

from mini_nanobot.bus import MessageBus
from mini_nanobot.channels.base import BaseChannel
from mini_nanobot.channels.manager import ChannelManager


class BlockingChannel(BaseChannel):
    name = "blocking"

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(bus)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self) -> None:
        self._running = True
        self.started.set()
        await self.release.wait()
        self._running = False

    async def stop(self) -> None:
        self._running = False
        self.release.set()

    async def send(self, session_id: str, content: str, metadata=None) -> None:
        return None


@pytest.mark.asyncio
async def test_manager_does_not_exit_before_channel_starts() -> None:
    bus = MessageBus()
    channel = BlockingChannel(bus)
    manager = ChannelManager(bus, [channel])
    await manager.start()
    waiter = asyncio.create_task(manager.wait_until_all_stopped())
    await channel.started.wait()
    await asyncio.sleep(0)
    assert not waiter.done()
    channel.release.set()
    await asyncio.wait_for(waiter, timeout=1)
    await manager.stop()