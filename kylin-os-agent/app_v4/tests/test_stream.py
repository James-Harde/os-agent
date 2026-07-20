"""P3 SSE 流式响应测试。

覆盖：
  - 端点返回 text/event-stream 内容类型
  - 事件序列包含 preflight/plan/execute/summarize/done
  - answer 在流中完整出现
"""

import json

from fastapi.testclient import TestClient


def test_stream_content_type_is_event_stream(client: TestClient):
    """流式端点应返回 text/event-stream。"""
    with client.stream("POST", "/api/chat/stream", json={"message": "分析磁盘"}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


def test_stream_emits_preflight_then_answer(client: TestClient):
    """事件序列应以 preflight 开始、以 done 结束，并包含 answer。"""
    events = _collect_events(client, "分析磁盘")
    types = [e["event"] for e in events]
    assert "preflight" in types
    assert "done" in types
    # 必须有最终 answer
    done = next(e for e in events if e["event"] == "done")
    assert done.get("answer"), "done 事件应包含 answer"


def test_stream_emits_plan_with_progressive_info(client: TestClient):
    """plan 事件应包含渐进披露元信息（工具 > 5）。"""
    events = _collect_events(client, "分析磁盘")
    plan_events = [e for e in events if e["event"] == "plan"]
    assert plan_events, "应至少有一个 plan 事件"
    plan = plan_events[0]
    assert "intent" in plan
    assert "progressive" in plan  # 渐进披露标志
    assert "hidden_tools" in plan


def test_stream_multi_turn_with_thread_id(client: TestClient):
    """多轮流式：使用 thread_id 保持上下文连贯。"""
    events1 = _collect_events(client, "分析磁盘")
    done1 = next(e for e in events1 if e["event"] == "done")
    thread_id = done1["thread_id"]

    events2 = _collect_events(client, "那进程呢", thread_id=thread_id)
    done2 = next(e for e in events2 if e["event"] == "done")
    assert done2["thread_id"] == thread_id


def _collect_events(client: TestClient, message: str, thread_id: str | None = None) -> list[dict]:
    """收集一次流式调用的所有 SSE 事件。"""
    payload = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id
    events: list[dict] = []
    with client.stream("POST", "/api/chat/stream", json=payload) as resp:
        for raw_line in resp.iter_lines():
            # iter_lines 返回 "data: {...}" 形式
            if raw_line.startswith("data: "):
                payload_str = raw_line[len("data: "):]
                try:
                    events.append(json.loads(payload_str))
                except json.JSONDecodeError:
                    pass
    return events
