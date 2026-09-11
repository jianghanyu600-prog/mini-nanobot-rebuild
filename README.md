# mini-nanobot（从零复现的个人 Agent 运行时）

![CI](https://github.com/jianghanyu600-prog/mini-nanobot/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

> 从空目录实现一个**能对话、能记住、能持续工作**的个人 Agent：终端 Channel → 消息总线 →
> 会话/记忆 → 内层 ReAct + 外层 Goal 图，含长期记忆（Dream）与子代理（Subagent）。
> 架构参考开源项目 HKUDS/nanobot，本项目为学习性从零复现 + 逐层测试。

## 1. Problem（为什么做）

「个人 Agent」这类产品（Claude Code / Codex / Cursor）看起来很神秘，其实核心是几件工程：
**多平台接入要解耦、记忆要分层持久化、长任务要能续跑、子任务不能阻塞主循环**。
本项目用最小代码把这四件事逐一实现并测试，而不是调用一个框架了事。

## 2. Architecture

```
ConsoleChannel ──publish_inbound──▶ [inbound] ──▶ AgentService._dispatch
   ▲                                                │  普通消息 → start_run
   │                                                │  运行中   → pending 入队
   │                                                │  /goal    → 授权后 start_run
   │                                                ▼
   │                          ┌───────── 外层 StateGraph（graph.py）─────────┐
   │                          │ prepare_run → agent（内层 create_agent）      │
   │                          │        ↑↓ 条件边：goal_continue / END /       │
   │                          │               goal_exhausted(超预算→blocked)  │
   │                          └──────────────────────────────────────────────┘
   │                                     │
   │  middleware 边界：动态记忆 prompt · pending 注入 · 重试 · 调用预算 · 摘要归档
   │                                     │
   └──[outbound]◀── AgentService._reply / _run_streamed(delta) ◀──┐
                                                                  │
        SessionManager（会话元数据 + 运行状态 + pending 队列）      │
        SQLite checkpointer（消息正文）                            │
        FileMemoryBackend（SOUL/USER/MEMORY.md + history.jsonl）   │
        SubagentManager（无 checkpointer 的后台子代理）────────────┘ 结果经 pending 注入父会话
```

**三层记忆**：短期 = SQLite checkpoint；中期 = 摘要压缩归档 `history.jsonl`；
长期 = Dream（受限 agent）把摘要巩固成 `SOUL/USER/MEMORY.md`，动态注入 system prompt。

## 3. Algorithm / 关键设计

| 设计 | 说明 |
|---|---|
| Channel–Bus–Service 解耦 | Channel 只认平台，Service 只认总线；新增平台不改服务层 |
| 内层 ReAct + 外层 Goal 图 | 不重写「模型↔工具」循环；外层只回答「目标还 active 吗，要不要再跑一轮」 |
| Goal 授权双保险 | 只有 `/goal` 消息把 `goal_creation_allowed=True`；工具层再校验，`replace` 无旁路 |
| pending 异步注入 | 运行中的用户消息/子任务结果不并发改 checkpoint，而是排队在**模型边界**注入 |
| Subagent 不阻塞 | `spawn` 立即返回 task_id，后台跑完经 `enqueue(event_id 幂等)` 回送 |
| 原子写 + 目录锁 | 记忆文件崩溃不写半文件；并发 append 不坏行 |
| 依赖注入 | judge/complete/runner 可注入 → 全部单测离线，不依赖网络 |

## 4. Experiment（工程验证而非指标）

本项目的"实验"是**逐层可测试性**：**45 个离线单测**覆盖
会话原子写与去重、并发限流、超时/失败/取消注入、Goal 授权与 replace 拒绝、
pending FIFO 去重、流式（有 delta / 无 delta 兜底）、运行时组装（不访问网络）。

## 5. Ablation（学到/放弃的设计）

- **不用自己写 model↔tool 循环**：交给 LangChain `create_agent`，自己只写业务状态机；
- **不使用 `with_structured_output` 做 Dream**：改为受限 `create_agent` + 两个文件工具，半失败可 `restore` 快照；
- **token 估算用「字符数/3」**：够判断压缩时机，不追求精确计费（如实标注）。

## 6. Limitations & 复现

**局限**：pending 在进程内存（重启丢队列）；Subagent 任务表进程内有效；
MCP 为可选，未配置不加载；token 估算不精确。

```bash
uv sync --dev
uv run pytest -q            # 45 个离线单测
uv run ruff check src tests
cp .env.example .env        # 填 OPENAI_API_KEY 等
uv run mini-nanobot         # 启动终端对话
```

常用命令：`/goal <目标>` 长任务 · `/stop` 停止 · `/status` 状态 · `/compact` 压缩 · `/dream` 巩固记忆 · `/new` 新会话 · `/exit`

**目录**
```
src/mini_nanobot/  config · bus · channels/(base,console,manager) · session/manager
                   state · middleware · retry · prompts · tools/(basic,goal,spawn,mcp_tools)
                   memory/(store,consolidator,dream) · subagents · hooks · graph · service · cli
tests/  45 个离线单测
```
