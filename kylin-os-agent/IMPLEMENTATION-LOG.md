# app_v4 实现日志

## P4（2026-07-20）— RAG 接入 + B/S 前端

### 完成项

1. **rag_search 工具接入 Agent 主链路**
   - `tools/system_tools.py`：新增 `rag_search(query, top_k=3)` @tool，内部用 `build_sample_index()` 懒加载 RAGIndex，返回 `[{score, text, source}]`
   - `tools/registry.py`：注册为 `auto` 权限，`plan_node` 通过 `get_auto_tool_names()` 自动暴露给 LLM
   - `model/chat_model.py`：fake model 增加知识库关键词分支（"知识库/知识/FAQ"），命中时规划调用 rag_search
   - 知识库复用 `rag/eval.py` 的 SAMPLE_DATASET（8 条麒麟 OS 运维 FAQ，覆盖磁盘/进程/端口/日志/安全/服务管理），**未改动 rag/ 包内部**

2. **B/S 前端单页**
   - `app_v4/static/index.html`：纯 HTML + 内联 CSS/JS，Tailwind + DaisyUI via CDN，无构建流程
   - 功能：消息输入、多轮气泡、SSE 流式打字机（fetch + ReadableStream）、intent/guard/tool_calls 展示、thread_id 显示与编辑、Trace 查询、工具列表、审计日志
   - `main.py`：挂载 `StaticFiles`，`GET /` 返回 `index.html`

3. **修复 trace_steps 数据丢失**
   - `graph/runner.py`：`run_agent` 的 result 字典补传 `trace_steps`，审计日志持久化完整节点链（preflight → plan → assess_plan → execute → summarize）

### 新增文件

- `app_v4/tests/test_rag_tool.py`（5 条测试）
- `app_v4/static/index.html`（前端单页）
- `app_v4/MANUAL-TEST.md`（手测清单）
- `IMPLEMENTATION-LOG.md`（本文件）

### 测试结果

- 自动化：**62 passed**（既有 57 + 新增 5）
- 手测：10 项清单见 `MANUAL-TEST.md`

### rag_search 使用示例（curl）

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"查询知识库：磁盘使用率怎么查"}'
```

返回：
```json
{
  "intent": "knowledge_query",
  "tool_calls": [{
    "tool_name": "rag_search",
    "status": "success",
    "data": {
      "results": [
        {"score": 7.8763, "text": "磁盘使用率可以通过 df -h 命令查看...", "source": "如何查看磁盘使用率"}
      ]
    }
  }]
}
```
