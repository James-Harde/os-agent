# 安全变更日志 (Safety Change Log)

> 每次 Agent 修改代码后，自动追加一条记录。用于：安全审计、回溯分析、答辩时说明。

---

## Phase 1 — 安全加固 (P0)

### [2026-07-12 任务 1.1] SafetyGuard Unicode 归一化增强

- **文件**: `app/safety/guard.py`
- **操作**: 
  1. 新增 `import unicodedata`
  2. 新增 `_ZERO_WIDTH` 常量和 `_normalize()` 静态方法（NFKC 折叠 + 零宽字符移除）
  3. `_detect_high_risk()` 和 `_detect_prompt_injection()` 入口调用 `_normalize(text)`
  4. `HIGH_RISK_PATTERNS` 新增 3 条 Pattern：`r"\br\s*-\s*rf\b"`（空格混淆的反引号）、反引号子shell 检测、`$()` 命令替换检测
- **安全影响**: 输入文本在正则匹配前先做 Unicode 归一化，防御通过零宽字符 (U+200B-U+200D) 或形近字符绕过 Pattern 的攻击
- **测试方式**: 待 Phase 4 写 `tests/test_safety.py` 参数化测试
- **回滚方法**: 删除 `_normalize` 方法 + `_detect_*` 中的首行调用 + 新增的 3 条 HIGH_RISK_PATTERNS 条目

### [2026-07-12 任务 1.2] slowapi 频率限制

- **文件**: `app/main.py`, `requirements.txt`
- **操作**:
  1. `requirements.txt` 新增依赖 `slowapi`
  2. `app/main.py` 导入 `slowapi.Limiter`、`get_remote_address`
  3. `limiter = Limiter(key_func=get_remote_address)`
  4. `chat` 函数新增 `@limiter.limit("10/minute")` 装饰器
  5. 新增 `RateLimitExceeded` 异常处理器 → 返回 429 JSON
- **安全影响**: 限制 `/api/chat` 每分钟 10 次请求，防止恶意用户耗尽 LLM API 配额或利用高频攻防探测
- **测试方式**: 连续调用 `/api/chat` 11 次，期望第 11 次返回 429
- **回滚方法**: 删除装饰器、异常处理器、`requirements.txt` 中的 `slowapi` 行

### [2026-07-12 任务 1.3] disk_usage 路径约束

- **文件**: `app/tools/system_tools.py` (`_safe_path_for_disk` 函数)
- **操作**: 之前实现允许任意存在的路径，现在缩窄为必须位于 `PROJECT_ROOT` 下，否则 fallback 到 `PROJECT_ROOT`
- **安全影响**: 防止只读 API 被利用来探测主机文件系统布局（即使 read-only 也能泄露信息）
- **测试方式**: 传 `path="/etc/shadow"` → 期望返回的是 PROJECT_ROOT 的 disk usage，不是 `/etc`
- **回滚方法**: 恢复成 `return Path(path).resolve() if Path(path).exists() else PROJECT_ROOT`

### [2026-07-12 任务 1.4] 部署安全包

- **文件**: `deploy/kylinos-agent.service`, `deploy/sudoers.kylinos-agent`, `deploy/install.sh`
- **操作**: 全新创建 3 个文件
- **安全影响**:
  - `.service`：OS 层隔离（`NoNewPrivileges`、`ProtectSystem=strict`、`CapabilityBoundingSet=` 等 10+ 参数）
  - `sudoers.kylinos-agent`：限制低权限用户只能执行 7 条 sudo 只读命令
  - `install.sh`：一键创建低权限用户 + 部署 + 启用服务
- **测试方式**: 在 Kylin V11 + LoongArch 真实环境执行 `./install.sh`，检查 `systemctl status kylinos-agent` 和 `journalctl`
- **回滚方法**: `systemctl stop kylinos-agent && systemctl disable kylinos-agent && userdel kylinos-agent`

---

## Phase 3 — 审批流（P1）

### [2026-07-12 任务 3.1] ApprovalService 新建

- **文件**: `app/approval/__init__.py`, `app/approval/service.py`（新建）
- **操作**:
  - SQLite 表 `approval_requests`（id, request_id, conversation_id, tool_name, arguments_json, status, requested_by, requested_at, decided_at, decided_by, justification, denial_reason）
  - 3 个 API：`create(...)` 创建 pending 审批、`decide(id, by, approve, reason)` 原子更新 status、`list_all()/list_pending()` 列出
  - `decide()` 用 `UPDATE ... WHERE status='pending'` 确保并发安全
- **安全影响**：与 ToolRegistry 的 confirm/deny 类型工具配合，不再只返回 blocked 给用户，而是进入可审计的审批队列
- **测试方式**: 调用 confirm 类工具后查 `/api/approvals` 应有一条 pending 记录；approve/reject 后 status 改变
- **回滚方法**: 删除 `app/approval/__init__.py` 和 `app/approval/service.py`，drop approval_requests 表

### [2026-07-12 任务 3.2] Orchestrator 联动审批流

- **文件**: `app/agent/orchestrator.py`
- **操作**:
  - `__init__` 新增可选参数 `approval_service`
  - `handle()` 里 plan 循环改造：每个 step 先查 spec 的 `execution_mode`
  - confirm/deny 类工具不再由 `tool_registry.call()` 执行，改为调 `approval_service.create()` 创建审批记录，返回 `blocked_pending_approval` 状态
- **安全影响**：
  - confirm 类工具现在进入"等待审批"，不再只是被拒绝
  - deny 类工具的路径保持不变（会被 `tool_registry.call()` 的现有门控 block 掉）
  - 所有工具调用仍然在安全审计范围内（tool_calls 列表保留了每一步）
- **测试方式**: 规划含 confirm 类工具的请求 → 返回含 approval_id 的 tool_call
- **回滚方法**: 恢复 `__init__` 签名和 `handle()` 里原来的 for 循环逻辑

### [2026-07-12 任务 3.3] 审批 API 端点

- **文件**: `app/main.py`
- **操作**:
  - 新增 `approval_service = ApprovalService()` 实例并注入 orchestrator
  - 新增路由：`GET /api/approvals`、`POST /api/approvals/{id}/approve`、`POST /api/approvals/{id}/reject`
  - approve/reject 共用 `ApprovalDecision` 请求模型（`{decided_by, reason}`）
- **安全影响**：新增 HTTP 端点。approve/reject 仅变更数据库状态，不执行系统操作（实际工具执行留作未来扩展）
- **测试方式**: 创建审批 → POST approve → 查列表 status=approved
- **回滚方法**: 删除 3 个端点和 `approval_service` 实例

---

## Phase 2 — MCP Server（历史 P0，协议层缺口）

### [2026-07-12 任务 2.1] MCP schema 数据模型

- **文件**: `app/mcp/__init__.py`, `app/mcp/schemas.py`（新建）
- **操作**:
  - `schemas.py`：定义 `JSONRPCRequest`（JSON-RPC 信封）、`JSONRPCResponse`、`MCPToolInfo`、`MCPToolResult`
  - `schemas.py`：定义 `TOOL_INPUT_SCHEMAS` dict，包含 7 个 auto 工具的 JSON Schema（disk_usage/path, port_lookup/port, process_list/limit, system_logs/limit, service_status/service, prompt_injection_scan/content）
- **安全影响**：纯数据模型，无执行逻辑
- **测试方式**：smoke test 验证 `tools/list` 返回的工具数量和 schema 格式
- **回滚方法**: 删除 `app/mcp/__init__.py` 和 `app/mcp/schemas.py`

### [2026-07-12 任务 2.2] MCP Server 核心类

- **文件**: `app/mcp/server.py`（新建）
- **操作**:
  - class `MCPServer(tool_registry, safety_guard, audit_logger)`
  - `handle_request()`: 路由 initialize / tools/list / tools/call
  - `_initialize()`: 返回 serverInfo 和 capabilities
  - `_tools/list()`: 只暴露 auto 模式工具（`_auto_tool_names`），附带 extension 字段 `x-permission`、`x-execution_mode`、`x-sandbox_scope`
  - `_tools/call()`: 先调 `safety_guard.preflight_request()` 拦截高危，再转发到 `tool_registry.call()`，返回 MCP content 信封
- **安全影响**：MCP 调用**复用现有 ToolRegistry.call()**，应用层沙盒 + Safety Guard 完全生效；confirm/deny 工具对外不可见
- **测试方式**: `scripts/mcp_smoke_test.py` 包括 7 个场景验证
- **回滚方法**: 删除 `app/mcp/server.py`

### [2026-07-12 任务 2.3] /api/mcp HTTP 端点

- **文件**: `app/main.py`
- **操作**:
  - 导入 `JSONRPCRequest` 和 `MCPServer`
  - 创建 `mcp_server` 实例（与 orchestrator 共用同一个 tool_registry/safety_guard/audit_logger）
  - 新增 `POST /api/mcp` 路由 → 返回 `{"jsonrpc":"2.0", "id", "result"}`
- **安全影响**：新 HTTP 端口，攻击面增加。但端点是只读代理，所有工具调用仍受应用层沙盒保护
- **测试方式**: smoke test + curl 手动验证
- **回滚方法**: 删除 `/api/mcp` 路由和 `mcp_server` 实例，移除 import

### [2026-07-12 任务 2.4] Smoke Test 脚本

- **文件**: `scripts/mcp_smoke_test.py`（新建）
- **操作**:
  - 7 个测试场景：initialize、tools/list 格式校验、tools/call disk_usage、tools/call port_lookup、unknown tool、confirm 工具被 blocked、unknown method
  - 纯 urllib 调用本地 API，无 OS 命令执行
- **安全影响**：测试只读，安全
- **测试方式**: `python scripts/mcp_smoke_test.py`，需要先启动服务
- **回滚方法**: 删除 `scripts/mcp_smoke_test.py`
