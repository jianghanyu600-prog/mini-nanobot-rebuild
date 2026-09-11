# PLAN · mini-nanobot（从零复现）— 已完成

参考教程：`PycharmProjects\mini-nanobot\docs\从零复现\01~05`；架构蓝本：HKUDS/nanobot。

## 里程碑（全部完成）
- **M1 能对话（第 1~2 章）**：config/prompts/tools + Bus/Channel/Service + SessionManager
  + SQLite checkpointer（重启恢复会话）
- **M2 能记住（第 3 章）**：AgentState/AgentContext + 动态 prompt + 完整记忆后端
  + 压缩归档 + Dream
- **M3 能持续工作（第 4 章）**：Goal 工具与授权 + 外层 StateGraph 续跑
  + pending 异步注入 + Subagent/线程管理 + 流式输出 + /stop /status
- **收尾（第 5 章）**：工作区文件工具 + Hook 生命周期 + 可选 MCP + 手动 /compact

## 交付物
- 45 个离线单测（并发/超时/取消/注入/流式/组装），ruff 干净
- README 六问 · LICENSE(MIT) · GitHub Actions CI · .env.example / mcp_servers.example.json

## 状态：**已完成 / 封板**

## 与参考仓库的差异（学习版取舍）
- 不做 RESTORE/SAVE 显式状态（交给 checkpointer）
- pending 与 Subagent 任务表为进程内（重启丢队列）
- token 估算为「字符数/3」启发式
