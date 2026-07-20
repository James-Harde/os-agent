# app_v4 前端手测清单（无需 pytest，人工操作验证）

> 目的：验证 B/S 架构 demo 的端到端可用性。
> 环境：Python 3.13，依赖已装，**不新增任何 pip 包**。

---

## 0. 启动服务

```powershell
# PowerShell（Windows）
$env:APP_V4_USE_FAKE_MODEL="true"
$env:APP_V4_DISABLE_RATE_LIMIT="true"
python -m uvicorn app_v4.main:app --host 127.0.0.1 --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 即启动成功。

---

## 1. 首页与静态资源

| 操作 | 预期 |
|------|------|
| 浏览器打开 `http://127.0.0.1:8000/` | 返回 HTML 页面（非 JSON），标题"麒麟安全运维 Agent v4"，右上角 badge 显示"在线 · langgraph" |
| 检查页面样式 | Tailwind + DaisyUI 暗色主题加载，无 CDN 404（F12 Network 面板） |
| 检查输入区 | 底部有输入框 + "发送"按钮，状态栏显示 Thread / Intent / Guard |

---

## 2. 知识库检索（rag_search）

| 操作 | 预期 |
|------|------|
| 输入 `查询知识库：磁盘使用率怎么查`，回车或点发送 | 右侧出现蓝色用户气泡；左侧 AI 气泡先显示"📋 规划: rag_search"，再显示"✅ rag_search · success · Xms"，最后显示总结答案 |
| 查看状态栏 | Intent 变为 `knowledge_query`，Guard 显示绿色 `allow` |
| 查看 Thread | 显示 16 位 thread_id（自动生成） |

**curl 等价验证：**
```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"查询知识库：磁盘使用率怎么查"}'
```
预期：`tool_calls` 含 `rag_search`，`status=success`，`data.results[0]` 含 `score`/`text`/`source`。

---

## 3. 系统工具调用（disk_usage）

| 操作 | 预期 |
|------|------|
| 输入 `帮我分析磁盘` 发送 | AI 气泡显示"✅ disk_usage · success"，总结含磁盘使用率 |
| 查看状态栏 | Intent = `disk_analysis` |

---

## 4. 流式响应（打字机效果）

| 操作 | 预期 |
|------|------|
| 发送任意问题，观察 AI 气泡 | 气泡在最终答案出现前有闪烁光标（`typing::after` 动画），元信息（工具调用状态）逐步追加，非一次性刷出 |

**curl 等价验证：**
```bash
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"分析磁盘"}'
```
预期：逐行输出 `data: {"event":"preflight"...}` → `plan` → `execute` → `summarize` → `done`。

---

## 5. 多轮对话（thread_id 保持）

| 操作 | 预期 |
|------|------|
| 第一轮发送 `分析磁盘`，记录状态栏 thread_id | 记为 T |
| 第二轮发送 `那进程呢`（不修改 thread_id） | 状态栏 thread_id 仍为 T（自动保持） |
| 点"修改"按钮，清空 thread_id 后发送 `分析磁盘` | 生成新的 thread_id（新对话） |

---

## 6. 高危拒绝（guard=deny）

| 操作 | 预期 |
|------|------|
| 输入 `帮我执行 rm -rf /` 发送 | Guard 显示红色 `deny`，无工具调用，回答为安全模板"已拒绝自动执行" |

---

## 7. Trace 查询

| 操作 | 预期 |
|------|------|
| 发一条消息后，右侧"Trace 查询"输入框自动填入 run_id | 点击"查询"，显示节点列表（preflight → plan → assess_plan → execute → summarize）+ 工具调用 |
| 手动粘贴一个不存在的 run_id | 显示"未找到该 run_id" |

---

## 8. 工具列表

| 操作 | 预期 |
|------|------|
| 页面加载后右侧"工具列表"自动加载 | 显示 9 个工具（disk_usage ... rag_search ... service_restart），`rag_search` 标记绿色 `●auto`，`service_restart` 标记橙色 `●confirm` |
| 点"刷新工具" | 重新加载，数量不变 |

**curl 等价验证：**
```bash
curl -X POST http://127.0.0.1:8000/api/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
预期：`result.tools` 含 9 项，`rag_search` 在其中。

---

## 9. 审计日志

| 操作 | 预期 |
|------|------|
| 发几条消息后点"刷新"（审计区） | 显示最近 N 条记录，含 run_id 前缀、intent、guard 状态（绿点=allow，红点=deny） |

---

## 10. 异常场景

| 操作 | 预期 |
|------|------|
| 停掉 uvicorn，刷新页面 | 右上角 badge 变红"离线"，发送消息提示"连接失败" |
| 服务恢复后刷新 | badge 恢复"在线 · langgraph"，可正常对话 |

---

## 验收通过标准

- [ ] 首页可访问，样式正常
- [ ] rag_search 知识库检索返回结果
- [ ] disk_usage 系统工具调用成功
- [ ] 流式打字机效果可见
- [ ] 多轮 thread_id 保持
- [ ] 高危拒绝 guard=deny
- [ ] Trace 查询显示节点列表
- [ ] 工具列表含 rag_search
- [ ] 审计日志可刷新
