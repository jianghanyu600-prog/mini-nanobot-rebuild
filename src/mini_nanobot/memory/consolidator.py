"""Consolidator：token 预算触发的上下文压缩。

思路：
1. 估算当前消息列表占用的 token；
2. 超过 `context_window * consolidation_ratio` 就触发；
3. 把最老的一批消息（保留最近 keep_recent 条）交给 LLM 摘要；
4. 摘要写入 memory/history.jsonl，消息列表里把原文替换成一条「[历史摘要]」；
5. LLM 摘要失败时降级为原文截断（kind="raw"），保证游标仍能推进。

简化点：token 用「字符数 / 3」粗估，不追求精确计费。
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, RemoveMessage

from .store import MemoryStore


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part) for part in content)
    return str(content)


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """粗略估算 token 数：字符数 / 3。"""
    total_chars = sum(len(_message_text(m)) for m in messages)
    return total_chars // 3


@dataclass
class Consolidator:
    llm: BaseChatModel
    memory: MemoryStore
    context_window: int
    consolidation_ratio: float
    keep_recent: int = 6

    def should_compact(self, messages: list[BaseMessage]) -> bool:
        if len(messages) <= self.keep_recent:
            return False
        return estimate_tokens(messages) > self.context_window * self.consolidation_ratio

    async def compact(
        self, messages: list[BaseMessage], *, force: bool = False
    ) -> list[BaseMessage]:
        """返回要 append 进 state["messages"] 的增量：一批 RemoveMessage + 一条摘要消息。

        不需要压缩时返回空列表；`force=True` 用于手动 `/compact`。
        """
        if not force and not self.should_compact(messages):
            return []
        if len(messages) <= self.keep_recent:
            return []

        old_messages = messages[: -self.keep_recent]
        if not old_messages:
            return []

        transcript = "\n".join(f"[{m.type}] {_message_text(m)}" for m in old_messages)
        prompt = (
            "请把下面这段对话历史压缩成一段简洁的摘要（中文，控制在 200 字以内），"
            "保留关键事实、已做出的决定，以及还没完成的任务：\n\n" + transcript
        )

        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            summary = str(response.content).strip()
            kind = "summary"
        except Exception:
            summary = transcript[:2000]
            kind = "raw"

        self.memory.append_history(summary, kind=kind)

        removals = [RemoveMessage(id=m.id) for m in old_messages if m.id is not None]
        summary_message = HumanMessage(content=f"[历史摘要]\n{summary}")
        return [*removals, summary_message]
