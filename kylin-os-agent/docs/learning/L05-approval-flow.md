# L05 — 审批流（Approval Flow）

## Concept

**Approval Flow（审批流）** 是 Agent 系统里处理"高风险、不可逆操作"的标准设计模式。当用户要求执行这类操作时，Agent 不是直接拒绝，而是**挂起**它，生成一张"审批卡片"，等待真人管理员来"批准"或"拒绝"后继续执行。

我们项目里把工具分成 3 类（`execution_mode` 字段）：

| 模式 | 含义 | Agent 会做什么 |
|------|------|----------------|
| `auto` | 只读、低风险 | 直接在后台执行，不需要用户参与 |
| `confirm` | 中风险、可能影响服务 | 不执行，创建审批卡片，等管理员批准 |
| `deny` | 高风险、直接拒绝 | 不执行，直接告诉用户"这个操作被禁止" |

approve/disapprove 操作的权限属于更高一级的人（安全审计人员），在 PRD 第 4 节被明确定义。

## 状态机

```
CREATE → pending → approve → approved
                      └→ reject  → rejected
```

关键约束：只有 `pending` 状态的申请才能被转成 `approved/rejected`。防止某个审批决定被执行后被第二次再次批准（double-spend 类比）。

## 在 Our Code Code

### `app/approval/service.py` (ApprovalService)

**数据库表 `approval_requests`**：
```sql
id                  -- 每条审批的 uuid
request_id          -- 关联到 audit.request_id
conversation_id     -- 关联到 memory.conversations
tool_name           -- 要执行的工具名
arguments_json      -- 工具参数（序列化 JSON）
status              -- pending | approved | rejected
requested_by        -- 谁发起（agent）
requested_at        -- 何时发起
decided_by          -- 谁决定（审计人员）
decided_at          -- 何时决定
justification       -- 批准时留的理由
denial_reason       -- 拒绝时留的理由
```

**核心方法**：

| 方法 | 场景 | 行为 |
|------|------|------|
| `create(...)` | Agent 规划到 confirm 类工具时 | 插入一条 pending 记录，返回 uuid |
| `decide(id, by, approve, reason)` | 调用 approve/reject | 更新 status 和 decided_*，返回整条记录 |
| `list_all(limit)` | 前端展示全部审批历史 | 返回最近 N 条 |
| `list_pending(limit)` | 前端展示待处理列表 | 只返回 status=pending 的 |

`decide()` 方法是**原子且安全的**：
```sql
update approval_requests
set status = ?, decided_at = ?, decided_by = ?, {column} = ?
where id = ? and status = 'pending'    ← 关键！只有 pending 才能改
```
如果 `cursor.rowcount == 0`，说明这条申请不存在或已经被处理，返回 None。

### `app/agent/orchestrator.py`（改动后的 plan 循环）

之前：
```python
for step in plan:
    tool_call = self.tool_registry.call(...)  # 所有工具都直接执行
```

现在：
```python
for step in plan:
    execution_mode = spec.get("execution_mode")
    if execution_mode != "auto" and self.approval_service is not None:
        # 不执行，创建审批卡片
        approval_id = self.approval_service.create(...)
        blocker_result = {"status": "blocked_pending_approval", "approval_id": ...}
        tool_calls.append(blocker_result)
        continue   # ← 跳过执行

    tool_call = self.tool_registry.call(...)  # auto 类才真执行
```

所有 plan 步骤都会生成一个 `tool_calls[i]`（让审计和前端知道发生了什么），但 confirm/deny 类工具的 tool_call 状态是 `blocked_pending_approval`。

### `app/main.py` HTTP 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/approvals` | 列出全部审批（默认最近 30 条） |
| `POST` | `/api/approvals/{id}/approve` body: `{decided_by, reason}` | 批准 → status 变 approved |
| `POST` | `/api/approvals/{id}/reject` body: `{decided_by, reason}` | 拒绝 → status 变 rejected |

## Why It Matters

面试官问"confirm 类工具你们怎么实现？"时：

- ❌ 差的回答："我们就 deny 掉了"——不符合真实企业系统中"高风险动作进入人工确认或审批"的要求
- ✅ 好的回答："Agent 不执行，创建 approve_requests 行，前端显示卡片，管理员批准后才会触发"

**另外的好处**：
1. 审计轨迹完整：什么时间、谁发起、谁决定、什么理由都有记录
2. UX 提升：用户的请求没有被粗暴拒绝，而是进入了"等待处理"队列
3. 合规：符合企业安全规范——高权限操作必须有审批

## Common Pitfalls

| 坑 | 踩没踩 | 说明 |
|----|--------|------|
| 审批后工具直接执行（可能参数已被篡改） | ⚠️ 预留 | Phase 3 只做卡片 + 决定；工具执行作为 Phase 4 扩展 |
| 审批状态不安全并发（pending 可被同时改） | ❌ 没踩 | `update ... where id=? and status='pending'` 这个 SQL pattern 是原子的 |
| 审批后没有审计记录 | ❌ 没踩 | 审批本身要入 audit（或单独的 approval 表 record）|
| confirm/deny 工具不区分 | ✅ 已分 | confirm → 创建申请, deny → 直接生产 blocked_result（tool_registry.call 的现有逻辑已经处理） |

## Further Reading

- **Four-eyes principle (四眼原则)**：两个人确认高风险操作，是企业安全常用做法
- **SOX (萨班斯法案)**：为什么上市公司 IT 需要完整的审批审计日志
- **ACID 事务的隔离级别**：理解 update where status='pending' 为什么在多用户场景下安全
