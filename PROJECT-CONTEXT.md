# Kylin Secure OS Agent — 项目上下文书

> 📌 **用途**：复制以下全文粘贴到新 Claude Code 窗口 / 另一个 Agent，即可让它快速了解项目当前状态、已完成的工作、下一步该干什么。此文档随项目进展持续更新。

---

## 1. 项目概况

**赛题**：中国软件杯 A2 赛题——面向麒麟操作系统的安全智能运维 Agent（出题企业：麒麟软件有限公司）

**产品定位**：B/S 架构智能运维 Agent，运行于 LoongArch + 麒麟高级服务器版 V11。用户输入自然语言 → Agent 理解意图 → 调用只读系统工具 → 返回诊断结论。全程受双重沙盒（应用层 + OS 层）保护。

**最终 PRD 文档**：`D:\klin-agent\os-agent-prd-final.md`（已读取，共 21 节）

**项目目录**：`D:\klin-agent\kylin-os-agent\`

**截止时间**：2026 年 7 月底（约 2 周）

**当前开发阶段**：Phase 1（安全加固）✅、Phase 2（MCP Server）✅、Phase 3（审批流）✅、UI 重写 ✅

---

## 2. 目录结构与文件职责

```
kylin-os-agent/
├── app/
│   ├── main.py                  FastAPI 入口（8 个 HTTP 端点 + 1 个 MCP 端点 + 3 个审批端点）
│   ├── config.py                项目根路径 / .env 加载
│   ├── agent/
│   │   └── orchestrator.py      主流程编排：安全预检 → LLM 计划 → 工具执行/审批 → 审计日志
│   ├── model/
│   │   └── adapter.py           OpenAI-compatible LLM Adapter（意图理解 + 计划生成 + 总结）
│   ├── safety/
│   │   └── guard.py             Safety Guard 多阶段校验（preflight / assess / output scan）
│   ├── tools/
│   │   ├── registry.py          Tool Registry：工具注册 + 权限门控
│   │   ├── system_tools.py      只读系统工具（7 个 auto：disk/directory/port/process/log/service/injection）
│   │   ├── command_runner.py    命令执行器（白名单 + Token 黑名单 + shell=False）
│   │   └── types.py             ToolSpec 数据模型
│   ├── mcp/
│   │   ├── __init__.py          模块说明
│   │   ├── schemas.py          JSON-RPC 信封 + 7 个工具的 inputSchema
│   │   └── server.py           MCP Server class（initialize / tools/list / tools/call）
│   ├── approval/
│   │   ├── __init__.py          模块说明
│   │   └── service.py          ApprovalService：approval_requests 表 + create/list/decide
│   ├── audit/
│   │   └── logger.py            SQLite 审计日志（tool_calls + audit_logs 表）
│   ├── memory/
│   │   └── store.py             SQLite 会话记忆（conversations + messages 表）
│   └── static/
│       ├── index.html           前端控制台 HTML（Tailwind + DaisyUI 暗色主题）
│       ├── styles.css           少量自定义 CSS（滚动条 / timeline 组件）
│       └── app.js               前端逻辑（5 个演示场景 + 消息渲染 + 链路图 + 审计列表）
├── deploy/
│   ├── kylinos-agent.service    systemd 单元（含 OS 层沙盒隔离参数）
│   ├── sudoers.kylinos-agent    sudo 最小化白名单（7 条只读命令）
│   └── install.sh               一键部署脚本（创建低权限用户 + 装依赖 + enable 服务）
├── docs/
│   ├── learning/
│   │   ├── L00-overview.md              项目总览与概念地图
│   │   ├── L01-sandbox-basics.md       沙盒原理深入讲解
│   │   ├── L02-safety-guard.md          SafetyGuard 多阶段校验详解
│   │   └── L04-mcp-protocol.md          MCP 协议原理与本项目的正确实现方式
│   │   └── L05-approval-flow.md         审批流设计
│   │   （注：L03、L06、L07 尚未生成，因为用户尚未完成 Phase 4 测试）
│   └── CHANGELOG-safety.md              安全变更记录（Phase 1-3 全量）
├── scripts/
│   └── mcp_smoke_test.py        MCP 7 场景 smoke test（仅 urllib 调用，无 OS 命令）
├── data/
│   └── audit.db                 运行时 SQLite（自动生成）
├── requirements.txt             fastapi / uvicorn / pydantic / slowapi
├── README.md                    项目说明书（旧版，部分内容已过时）
└── .env / .env.example          LLM API 配置（DeepSeek 示例：base_url=https://api.deepseek.com）
```

---

## 3. HTTP API 端点清单

| 方法 | 路径 | 来源 | 用途 |
|------|------|------|------|
| GET  | /api/health            | 已有 | 健康检查 |
| GET  | /api/runtime           | 已有 | 模型 + 沙盒状态 |
| GET  | /api/tools             | 已有 | 列出所有工具定义 |
| POST | /api/chat              | 已有 | 提交自然语言运维请求（限速 10/min） |
| GET  | /api/audit             | 已有 | 查询审计日志 |
| GET  | /api/conversations     | 已有 | 列出会话 |
| GET  | /api/conversations/{id}/messages | 已有 | 查消息历史 |
| POST | /api/mcp               | **Phase 2** | JSON-RPC 端点（initialize / tools/list / tools/call） |
| GET  | /api/approvals         | **Phase 3** | 列出审批历史 |
| POST | /api/approvals/{id}/approve | **Phase 3** | 批准审批 |
| POST | /api/approvals/{id}/reject  | **Phase 3** | 拒绝审批 |

---

## 4. 安全架构要点（回答评委核心问题）

**核心原则**："LLM 输出只是请求，最终执行由代码硬编码门控决定"

**双层沙盒**：
1. **应用层沙盒**：`ToolRegistry.call()` 硬编码门控（`execution_mode=auto + permission=read + read_only=true`）→ 只有 7 个只读工具能执行
2. **OS 层沙盒**：`kylinos-agent.service`（`NoNewPrivileges=yes` / `ProtectSystem=strict` / `CapabilityBoundingSet=`）+ 低权限用户 `kylinos-agent`

**Safety Guard 4 阶段**：preflight（LLM 前）→ assess_request + assess_plan（LLM 后）→ scan_untrusted_output（工具结果）→ merge 合并最终决策

**三大防线**：
- 白名单命令（df/du/ss/netstat/lsof/ps/journalctl/systemctl/tasklist）
- 黑名单 token（rm/del/kill/chmod/sudo/bash 等 25 个）
- 所有 OS 命令 `subprocess.run(list, shell=False)`，结构上不可能注入

**关键技术决策**：
- MCP `tools/call` **复用现有 `ToolRegistry.call()`**，不建第二执行路径
- `tools/list` **只暴露 auto 模式工具**，confirm/deny 对外不可信
- Safety Guard `_normalize()` 做 Unicode NFKC 归一化 + 零宽字符移除，防绕过

---

## 5. Phase 完成详情

### Phase 1 — 安全加固（P0，完成）

| 任务 | 文件 | 核心改动 |
|------|------|---------|
| 1.1 SafetyGuard 增强 | `app/safety/guard.py` | `_normalize()` NFKC 归一化 + 零宽字符移除；Pattern 新增 `r\s*-\s*rf`、反引号子shell、`$()` |
| 1.2 API 限流 | `app/main.py` + `requirements.txt` | slowapi `@limiter.limit("10/minute")` on `/api/chat`，新增 429 异常处理器 |
| 1.3 路径约束 | `app/tools/system_tools.py` | `_safe_path_for_disk()` 缩窄到 PROJECT_ROOT |
| 1.4 部署安全包 | `deploy/\*` 3 文件 | systemd unit + sudoers 白名单 + install.sh |

### Phase 2 — MCP Server（P0，完成）

| 任务 | 文件 | 核心改动 |
|------|------|---------|
| 2.1 Schema | `app/mcp/schemas.py` | JSONRPCRequest + 7 个 auto 工具的 JSON Schema inputSchema |
| 2.2 Server | `app/mcp/server.py` | MCPServer 类，复用 ToolRegistry.call() |
| 2.3 HTTP 端点 | `app/main.py` | `POST /api/mcp` |
| 2.4 Smoke Test | `scripts/mcp_smoke_test.py` | 7 场景（仅 urllib，无 OS 命令） |

### Phase 3 — 审批流（P1，完成）

| 任务 | 文件 | 核心改动 |
|------|------|---------|
| 3.1 ApprovalService | `app/approval/service.py` | SQLite 表 + create/list_all/list_pending/decide/decide 原子 |
| 3.2 Orchestrator 联动 | `app/agent/orchestrator.py` | plan 循环中 confirm/deny 类工具 → 创建 approval，返回 `blocked_pending_approval` |
| 3.3 API 端点 | `app/main.py` | GET/approvals + POST approve/reject |
| 3.4 | 文档 | L05-approval-flow.md + CHANGELOG |

### UI 重写

- 第 1 次：纯 Tailwind CDN 暗色自定义 CSS（用户的评价：丑）
- 第 2 次：加 DaisyUI 4.12.10 组件库 + Google Fonts + DaisyUI chat bubble + badge + spinner

---

## 6. 待办（未完成）

### Phase 4 — 自动化测试 + 测试报告（未完成）
- [ ] `tests/test_safety.py` — pytest 参数化：每个高危 pattern + 零宽字符变形 → 期望拦截
- [ ] `tests/test_mcp.py` — MCP 协议层测试
- [ ] `tests/smoke_test.py` — 5 个演示场景端到端验证（仅 urllib）
- [ ] `docs/test_report.md` — 测试报告

### Phase 5 — 演示材料（未完成）
- [ ] `docs/slides_outline.md` — PPT 大纲
- [ ] `docs/demo_script.md` — 演示录屏脚本文案
- [ ] `docs/perf_report.md` — 性能报告

### 仍需补全的学习日志
- [ ] `docs/learning/L03-command-runner.md`
- [ ] `docs/learning/L06-systemd-sandbox.md`
- [ ] `docs/learning/L07-testing.md`

### 部署验证（未完成）
- [ ] 在 Kylin V11 + LoongArch 实机跑 `bash install.sh` 验证
- [ ] 确认 `ss`、`journalctl`、`systemctl` 命令路径兼容

### 没有做的（PRD 列为 P2 或后续版本）
- ❌ 长期语义记忆 / RAG（不入初版范围）
- ❌ 前端审批卡片 UI（Phase 4 / 5 时补）
- ❌ 多主机批量运维
- ❌ 自动修复所有系统故障
- ❌ 不经审批的破坏性写操作
- ❌ 默认 root 执行

---

## 7. 对话管理规则（必须延续）

1. **学习日志**：每个 Phase 完成后生成 `docs/learning/L{xx}-*.md`，按 Concept / In Our Code / Why It Matters / Common Pitfalls / Further Reading 五段式写，中英混合深入讲解
2. **安全变更记录**：每次改代码同步追加到 `docs/CHANGELOG-safety.md`，含安全影响 / 测试方式 / 回滚方法
3. **确认门控**：
   - 修改现有文件 < 30 行 → 自己做
   - 修改现有文件 > 30 行 → 先给用户看 diff 预览
   - 新增文件 → 先展示内容，用户确认后再创建
   - Phase 间过渡 → 暂停等用户确认下一 Phase 计划
4. **安全约束**：严禁执行 rm/chmod/chown/kill/systemctl/sudo/mkfs/dd/shutdown/reboot/format/cmd/powershell/bash/sh 等命令
5. **pip install**：禁止 Agent 执行，只改 requirements.txt，由用户手动 pip install

---

## 8. 快速启动命令

```powershell
# 1. 装依赖
pip install -r D:\klin-agent\kylin-os-agent\requirements.txt

# 2. 启动服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. 打开浏览器
# http://127.0.0.1:8000

# 4. MCP smoke test（服务启动后另开终端）
python D:\klin-agent\kylin-os-agent\scripts\mcp_smoke_test.py
```

---

## 9. 总进度

**约 75%**（功能主体剩 Phase 4 测试 + Phase 5 演示材料，UI 已完成）

| 维度 | 进度 |
|------|------|
| PRD 1 项"必须实现" | 9/10 完成（仅"麒麟 V11 实机验证"未做） |
| 初赛 9 项提交材料 | 1/9 完成（PRD） |

---

> 📅 最后更新：2026-07-12
> 🔄 每次新 Phase 完成后，应同步更新本文档
