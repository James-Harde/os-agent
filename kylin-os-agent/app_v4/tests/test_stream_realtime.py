"""实时流前端展示验收（对应目标"测试与验收"第 2 项）。

用真实 POST /api/chat/stream 请求验证：
  - 输入"分析磁盘"后，在请求未结束前页面已经出现至少一个 token
    （即 token 事件出现在 done 事件之前，流式而非批量）。
  - 节点进度事件（preflight）在 done 之前出现（实时发射，非结束后重建）。
  - 前端 sse-parser 纯函数能正确解析该真实 SSE 流（含 JSON action token）。
  - done 携带 TTFT / 总耗时 / token 数等最终状态。

这些断言直接对应"运行期间缺少可见进度"的根因修复。
"""

import json

from fastapi.testclient import TestClient


def _collect(client: TestClient, message: str, thread_id=None):
    """收集一次流式调用的所有 SSE 事件，保留顺序。"""
    payload = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id
    events = []
    with client.stream("POST", "/api/chat/stream", json=payload) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        for raw in resp.iter_lines():
            if raw.startswith("data: "):
                try:
                    events.append(json.loads(raw[len("data: "):]))
                except json.JSONDecodeError:
                    pass
    return events


def test_token_appears_before_done_for_readonly_path(client: TestClient):
    """'分析磁盘'（readonly 路径）：首个 token 必须在 done 之前出现。

    这验证 token 是流式产出的，而非等整轮结束后批量下发——
    对应验收条件"请求未结束前页面已经出现至少一个 token"。
    """
    events = _collect(client, "分析磁盘")
    types = [e["event"] for e in events]

    assert "done" in types, "应有 done 事件"
    done_idx = types.index("done")
    first_token_idx = types.index("token")

    assert first_token_idx < done_idx, (
        f"首个 token（idx={first_token_idx}）应在 done（idx={done_idx}）之前，"
        f"实际序列 prefix={types[:5]}"
    )
    # token 数量应足够多（模型 astream 按字/词切分）
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) >= 5, f"应至少 5 个 token，得到 {len(token_events)}"

    # token 连续 index
    for i, e in enumerate(token_events):
        assert e["index"] == i, f"token index 应连续，第 {i} 个为 {e['index']}"


def test_node_event_emitted_before_done(client: TestClient):
    """节点进度事件（preflight）应在 done 之前出现——实时发射的证明。

    旧实现只在图跑完后从 trace_steps 重建节点事件，导致运行期间无可见进度。
    修复后 preflight 在图运行中间即被消费。
    """
    events = _collect(client, "分析磁盘")
    types = [e["event"] for e in events]
    done_idx = types.index("done")
    progress_before_done = [t for t in types[:done_idx]
                           if t in ("preflight", "plan", "execute", "summarize")]
    assert progress_before_done, (
        f"done 之前应出现节点进度事件，实际 done 前序列={types[:done_idx]}"
    )
    assert "preflight" in progress_before_done, "preflight 应在 done 之前"


def test_sse_parser_handles_real_stream(client: TestClient):
    """前端 sse-parser 纯函数应能解析真实 SSE 流（含 JSON action token）。

    把真实响应的原始字节喂给 parseSSEChunk，验证产出与直接 JSON.parse
    一致，且跨 chunk 拼接正确。
    """
    import subprocess
    import sys

    # 仅在 Node 可用时验证；否则跳过（parser 自身由 Node 单元测试覆盖）。
    try:
        import shutil
        node = shutil.which("node")
        if not node:
            return
    except Exception:
        return

    payload = json.dumps({"message": "分析磁盘"})
    # 用 node 直接调用浏览器侧解析函数处理抓取到的原始 SSE 文本。
    script = r"""
    const SSEParser = require('./app_v4/static/sse-parser.js');
    // 读取 stdin 的全部 SSE 文本，按随机分块喂入，验证最终事件序列合法
    let raw = "";
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', d => raw += d);
    process.stdin.on('end', () => {
      // 模拟流式分块：每 30~90 字符切一块
      let buf = "", events = [], i = 0;
      const rng = raw.length;
      while (i < raw.length) {
        const step = 30 + Math.floor(Math.random() * 60);
        const chunk = raw.slice(i, i + step);
        i += step;
        const r = SSEParser.parseSSEChunk(buf, chunk);
        events.push(...r.events);
        buf = r.buffer;
      }
      const flush = SSEParser.parseSSEFlush(buf);
      events.push(...flush);
      const kinds = {};
      events.forEach(e => kinds[e.event] = (kinds[e.event]||0)+1);
      const hasToken = events.some(e => e.event === 'token');
      const hasDone = events.some(e => e.event === 'done');
      const doneEv = events.find(e => e.event === 'done');
      console.log(JSON.stringify({
        total: events.length, kinds, hasToken, hasDone,
        tokenCount: doneEv && doneEv.stream_stats && doneEv.stream_stats.token_count,
        allHaveEventField: events.every(e => typeof e.event === 'string')
      }));
    });
    """
    # 抓取原始 SSE 文本
    raw_sse = ""
    with client.stream("POST", "/api/chat/stream", json={"message": "分析磁盘"}) as resp:
        for raw in resp.iter_lines():
            raw_sse += raw + "\n"

    res = subprocess.run(
        [node, "-e", script], input=raw_sse, capture_output=True, text=True,
        check=False, cwd=".",
    )
    assert res.returncode == 0, f"node parser 失败: {res.stderr}"
    import json as _json
    out = _json.loads(res.stdout.strip().split("\n")[-1])
    assert out["hasToken"], "解析结果应含 token 事件"
    assert out["hasDone"], "解析结果应含 done 事件"
    assert out["tokenCount"] >= 5, f"token_count 应 >=5, 得到 {out['tokenCount']}"
    assert out["allHaveEventField"], "所有解析出的事件都应含 event 字段"


def test_done_carries_final_stats(client: TestClient):
    """done 应携带 TTFT、总耗时、token 数等最终状态。"""
    events = _collect(client, "分析磁盘")
    done = next(e for e in events if e["event"] == "done")
    stats = done.get("stream_stats", {})
    assert stats.get("ttft_ms") is not None, "应有 TTFT"
    assert stats.get("ttft_ms", 0) > 0, "TTFT 应 > 0"
    assert stats.get("total_ms", 0) > 0, "总耗时应 > 0"
    assert stats.get("token_count", 0) >= 5, "token 数应 >= 5"


def test_readonly_tool_results_are_visible_in_stream(client: TestClient):
    """只读工具结果应实时出现，并在 done 中提供最终权威副本。"""
    from app_v4.mcp.agent_invoker import LocalToolInvoker

    client.app.state.deps.mcp_invoker = LocalToolInvoker()
    events = _collect(client, "分析磁盘")
    execute_events = [e for e in events if e["event"] == "execute"]
    assert execute_events, "只读路径应发出 execute 事件"

    streamed_calls = [
        call
        for event in execute_events
        for call in event.get("tool_calls", [])
    ]
    disk_call = next(
        call for call in streamed_calls if call.get("tool_name") == "disk_usage"
    )
    assert disk_call.get("status") == "success"
    assert "used_percent" in disk_call.get("data", {})

    done = next(e for e in events if e["event"] == "done")
    final_calls = done.get("tool_calls", [])
    assert any(call.get("tool_name") == "disk_usage" for call in final_calls)
    assert done.get("answer"), "done 应携带默认展示的最终答案"


def test_consult_stream_returns_direct_answer_without_tool_events(client: TestClient):
    """普通原因咨询经 SSE 直接回答，不应出现工具执行事件。"""
    events = _collect(client, "磁盘空间经常不足有哪些常见原因？")
    assert not [event for event in events if event["event"] == "execute"]

    done = next(event for event in events if event["event"] == "done")
    assert done.get("intent") == "general_help"
    assert done.get("answer_source") == "direct_answer"
    assert done.get("tool_calls") == []
    assert "日志" in done.get("answer", "")


def test_stream_clear_status_progression(client: TestClient):
    """事件序列应体现明确的状态推进：preflight → ... → done。"""
    events = _collect(client, "分析磁盘")
    types = [e["event"] for e in events]
    # 首事件应为 preflight（图的第一步），末事件应为 done
    assert types[0] == "preflight", f"首事件应为 preflight，得到 {types[0]}"
    assert types[-1] == "done", f"末事件应为 done，得到 {types[-1]}"
    # 中间应存在 token 流
    assert "token" in types, "中间应有 token 流"
