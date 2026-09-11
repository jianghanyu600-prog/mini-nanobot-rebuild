import asyncio

from mini_nanobot.bus import InboundMessage, MessageBus, OutboundMessage


def test_roundtrip() -> None:
    async def scenario() -> None:
        bus = MessageBus()
        inbound = InboundMessage("console", "s1", "你好")
        await bus.publish_inbound(inbound)
        got = await bus.consume_inbound()
        assert got.content == "你好"

        await bus.publish_outbound(
            OutboundMessage("console", "s1", "收到", event="final")
        )
        out = await bus.consume_outbound()
        assert out.content == "收到"

    asyncio.run(scenario())