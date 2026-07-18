# L00 — 项目总览与关键概念地图

## 一句话总结

> **Kylin Secure OS Agent** 是一个部署在麒麟操作系统上的 B/S 架构 Agent，用户可以打字说话，Agent 自己调用系统命令查磁盘、查端口、查日志，但**绝对不能执行危险的写操作**（删除、修改、重启、格式化 …）。

## 数据流向全图

```
                          浏览器
                      (index.html + app.js)
                            │  用户自然语言
                            ▼
                    POST /api/chat
                            │
                            ▼
            ┌───────────────────────────────┐
            │     AgentOrchestrator          │
            │     (orchestrator.py:30-216)   │
            │                                │
            │  ① SafetyGuard.preflight ──高危?──→ 拒绝
            │  ② ModelAdapter.plan ──────────→ JSON 工具计划
            │  ③ SafetyGuard.assess ──高危?──→ 拒绝
            │  ④ ToolRegistry.call ──────────→ 只读工具 or 拦截
            │  ⑤ SafetyGuard.scan_output ──注入?──→ 告警
            │  ⑥ ModelAdapter.summarize ──→ 中文结论
            └───────────────────────────────┘
                            │
                  ┌─────────┼──────────┐
                  ▼          ▼          ▼
            AuditLogger  MemoryStore  前端 JSON
```

## 文件与模块速查

```
app/
├── main.py                    FastAPI 路由（8 个 HTTP 端点）
├── config.py                  项目路径、.env 加载
├── agent/
│   └── orchestrator.py        ★ 主流程编排：把 LLM/安全/工具/审计串起来
├── model/
│   └── adapter.py             ★ LLM Adapter：意图理解 + 工具计划 + 总结
├── safety/
│   └── guard.py               ★ SafetyGuard：多阶段安全校验
├── tools/
│   ├── registry.py            ★ ToolRegistry：工具注册 + 权限门控
│   ├── system_tools.py            只读系统命令（磁盘/目录/端口/进程/日志）
│   ├── command_runner.py          命令白名单执行器
│   └── types.py                   数据模型
├── audit/
│   └── logger.py              SQLite 审计日志
├── memory/
│   └── store.py               SQLite 会话记忆
└── static/                    Web 控制台 (HTML / CSS / JS)
```

## 关键概念中英对照

| 英文 | 中文 | 本项目对应 |
|------|------|-----------|
| Sandbox | 沙盒 | ToolRegistry + systemd unit |
| Safety Guard / Guardrail | 安全护栏 | `safety/guard.py` |
| Whitelist / Allowlist | 白名单 | `DEFAULT_ALLOWED_COMMANDS` |
| Blacklist / Denylist | 黑名单/阻止名单 | `BLOCKED_TOKENS` |
| Prompt Injection | 提示词注入 | `PROMPT_INJECTION_PATTERNS` |
| Minimum Privilege | 最小权限原则 | `CapabilityBoundingSet=` + 低权限用户 |
| Audit Log | 审计日志 | `SQLite tool_calls + audit_logs` |
| MCP (Model Context Protocol) | 模型上下文协议 | 尚未实现，Phase 2 添加 |
| Root / Non-root | root 用户 / 低权限用户 | `User=kylinos-agent` |
| Defense in Depth | 纵深防御 | 应用层 + OS 层双层沙盒 |

## 关键的 5 条安全防线（答题金句）

1. **Safety Guard 完全独立于 LLM，代码级校验** —— LLM 输出只是"建议"，最终执行由 `ToolRegistry.call()` 的硬编码门控决定
2. **双层沙盒，Defense in Depth** —— 应用层（Tool Registry + 命令白名单）+ OS 层（systemd + sudoers）
3. **工具分级执行** —— `auto` 自动（只读），`confirm` 需审批（不自动），`deny` 直接拒绝
4. **所有工具输出视为不可信数据** —— 连工具结果里的 Prompt Injection 文本都会被扫描
5. **输入 URL 路径统一归一化** —— Unicode NFKC 归一化后移除零宽字符，防止.regex 绕过

## 2 周路线图速览

```
Week 1：
  Phase 1 (P0) ← 当前：安全加固
  Phase 2 (P0) ← 真 MCP Server（PRD 最大缺口）
  Phase 3 (P1) ← 审批流

Week 2：
  Phase 4 (P1) ← 自动化测试 + 测试报告
  Phase 5 (P1) ← PPT / 演示脚本 / 性能报告
```

## Further Reading

- **Defense in Depth**：https://en.wikipedia.org/wiki/Defence_in_depth_(computing)
- **OWASP LLM Top 10**：https://owasp.org/www-project-top-10-for-large-language-model-applications/
- **MCP Protocol Spec**：https://spec.modelcontextprotocol.io/
- **systemd for Linux 安全隔离**：`man systemd.exec`
