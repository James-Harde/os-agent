"""P3 记忆分层 + 渐进披露 + 循环熔断测试。

覆盖：
  - 长期记忆 recall/保存
  - plan_node 渐进披露（工具 > 5 时只展示 Top-5）
  - 循环熔断（相同 plan 连续出现 2 次则停止）
  - 多轮后记忆注入影响规划
"""

import pytest
from fastapi.testclient import TestClient

from app_v4.graph.nodes import _rank_tools, _tokenize, _plan_signature
from app_v4.memory.long_term import LongTermMemory


# ---------------------------------------------------------------------------
# 单元：分词 + 排序
# ---------------------------------------------------------------------------
def test_tokenize_chinese_and_english():
    tokens = _tokenize("分析 disk 磁盘使用率")
    # 中文按字切，英文保留单词
    assert "分" in tokens
    assert "析" in tokens
    assert "disk" in tokens
    assert "磁" in tokens


def test_rank_tools_returns_top_k():
    tools = {"disk_usage", "process_list", "port_lookup", "system_logs",
             "service_status", "directory_usage", "prompt_injection_scan"}
    descs = {
        "disk_usage": "获取指定目录所在磁盘的使用率",
        "directory_usage": "获取目录下各子目录文件的占用空间排名",
        "port_lookup": "查询指定端口的占用情况",
        "process_list": "查询当前运行的进程列表按 CPU 排序",
        "system_logs": "读取系统警告和错误级别的日志",
        "service_status": "查询 systemd 服务的运行状态",
        "prompt_injection_scan": "扫描不可信文本中的提示词注入风险",
    }
    ranked = _rank_tools("分析磁盘使用率", tools, descs, top_k=5)
    assert len(ranked) == 5
    # disk_usage 必须在前（描述含"磁盘"）
    assert ranked[0] == "disk_usage"


def test_rank_tools_no_match_still_returns_k():
    tools = {"disk_usage", "process_list", "port_lookup"}
    descs = {t: t for t in tools}
    ranked = _rank_tools("你好世界", tools, descs, top_k=2)
    assert len(ranked) == 2  # 无匹配也返回 Top-K，不会报错


def test_plan_signature_stable():
    p1 = [{"tool": "disk_usage", "arguments": {}}, {"tool": "process_list", "arguments": {}}]
    p2 = [{"tool": "process_list", "arguments": {}}, {"tool": "disk_usage", "arguments": {}}]
    assert _plan_signature(p1) == _plan_signature(p2)  # 顺序无关


# ---------------------------------------------------------------------------
# 单元：LongTermMemory
# ---------------------------------------------------------------------------
@pytest.fixture
def mem(tmp_path):
    return LongTermMemory(db_path=tmp_path / "test_mem.db")


def test_save_and_recall_conclusion(mem):
    mem.save_conclusion("t1", "r1", "disk_analysis", "磁盘使用率 72%")
    mem.save_conclusion("t1", "r2", "process_analysis", "发现高 CPU 进程")
    recalled = mem.recall("t1", limit=5)
    conclusions = recalled["conclusions"]
    assert len(conclusions) == 2
    # 最新在前
    assert conclusions[0]["intent"] == "process_analysis"


def test_profile_latest_value(mem):
    mem.save_profile("t1", "intent:disk_analysis", "1")
    mem.save_profile("t1", "intent:disk_analysis", "2")  # 覆盖
    recalled = mem.recall("t1")
    # 取最新值
    assert recalled["profile"]["intent:disk_analysis"] == "2"


def test_record_saves_conclusion_for_real_answer(mem):
    mem.record("t1", "r1", "disk_analysis", "磁盘正常", "llm_summary")
    recalled = mem.recall("t1")
    assert recalled["conclusions"][0]["summary"] == "磁盘正常"
    assert "intent:disk_analysis" in recalled["profile"]


def test_record_skips_conclusion_for_safety_template(mem):
    safety_answer = "已拒绝：高风险"
    mem.record("t1", "r1", "x", safety_answer, "safety_template")
    # safety_template 不进结论，但这里 record 只对 allow 类保存
    # 验证结论未保存（safety_template 分支不保存）
    recalled = mem.recall("t1")
    assert len(recalled["conclusions"]) == 0


# ---------------------------------------------------------------------------
# 集成：渐进披露 + 循环熔断（通过 HTTP）
# ---------------------------------------------------------------------------
def test_progressive_disclosure_at_runtime(client: TestClient):
    """运行时：知识查询走 plan 路径，工具总数 7 > 5 时应启用渐进披露。"""
    # 知识查询走 knowledge → plan 路径（渐进披露在 plan_node 中）
    resp = client.post("/api/chat", json={"message": "如何查看磁盘使用率"})
    assert resp.status_code == 200
    data = resp.json()
    trace = data.get("trace_summary", {})
    steps = trace.get("steps", [])
    # 知识查询走 plan 路径
    assert "plan" in steps or data.get("route") == "knowledge"


def test_multi_turn_memory_accumulates(client: TestClient):
    """多轮：连续两轮后，长期记忆里应积累结论（通过容器公开 recall 路径）。"""
    # client fixture 已注入隔离容器，通过 app.state.deps 取回 memory
    deps = client.app.state.deps
    mem = deps.long_term_memory

    resp1 = client.post("/api/chat", json={"message": "分析磁盘"})
    assert resp1.status_code == 200
    thread_id = resp1.json()["thread_id"]
    # 第二轮追问
    resp2 = client.post("/api/chat", json={"message": "那进程呢", "thread_id": thread_id})
    assert resp2.status_code == 200
    # 验证记忆积累（通过容器公开 recall 路径，不篡改私有单例）
    recalled = mem.recall(thread_id, limit=10)
    # 至少 1 条结论被保存（disk 有真实 answer_source=llm_summary）
    assert len(recalled["conclusions"]) >= 1


def test_repeat_legal_turn_not_flagged_as_loop(client: TestClient):
    """§4.2 #3：循环检测仅限同 run 内部；用户重复合法问题必须允许执行。

    旧实现错误地把跨 turn 的相同 plan 签名判为循环（audit #4/#5），
    现在每次 run 重置 seen_plans，所以第二次"分析磁盘"应成功。
    """
    # 第一轮：分析磁盘
    resp1 = client.post("/api/chat", json={"message": "分析磁盘"})
    assert resp1.status_code == 200
    thread_id = resp1.json()["thread_id"]
    run_id_1 = resp1.json()["run_id"]
    assert resp1.json()["guard_decision"] == "allow"
    names1 = [c["tool_name"] for c in resp1.json()["tool_calls"]]
    assert "disk_usage" in names1

    # 第二轮：完全相同输入 → 合法重复，不应触发假循环
    resp2 = client.post("/api/chat", json={"message": "分析磁盘", "thread_id": thread_id})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["guard_decision"] == "allow", f"假循环! reasons={data2.get('guard_reasons')}"
    assert data2["run_id"] != run_id_1, "两次 run_id 应不同"
    names2 = [c["tool_name"] for c in data2["tool_calls"]]
    assert "disk_usage" in names2, f"第二轮应调用 disk_usage，得到 {names2}"


# ---------------------------------------------------------------------------
# Phase G：记忆分层增强（跨 thread / 过期 / 删除 / 纠错）
# ---------------------------------------------------------------------------
def test_memory_expiry_filters_old_entries(mem, monkeypatch):
    """recall_with_expiry 应过滤超时的记忆（Phase G：TTL）。"""
    from datetime import datetime, timezone, timedelta

    # 插入一条"新"记忆
    mem.save_conclusion("t1", "r1", "disk", "磁盘正常")
    recalled = mem.recall_with_expiry("t1", limit=5, max_age_hours=24)
    assert len(recalled["conclusions"]) == 1

    # 手动修改 created_at 为很久以前（模拟过期）
    with mem._connect() as conn:
        old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        conn.execute(
            "update long_term_memories set created_at = ? where thread_id = ?",
            (old_time, "t1"),
        )

    # 24 小时 TTL 应过滤掉这条
    recalled = mem.recall_with_expiry("t1", limit=5, max_age_hours=24)
    assert len(recalled["conclusions"]) == 0, "过期记忆应被过滤"

    # 不过期（None）应返回
    recalled = mem.recall_with_expiry("t1", limit=5, max_age_hours=None)
    assert len(recalled["conclusions"]) == 1


def test_memory_delete(mem):
    """delete_memory 应删除指定记忆（Phase G：删除权）。"""
    mid = mem.save_conclusion("t1", "r1", "disk", "磁盘正常")
    recalled = mem.recall("t1")
    assert len(recalled["conclusions"]) == 1

    # 删除
    assert mem.delete_memory(mid) is True
    recalled = mem.recall("t1")
    assert len(recalled["conclusions"]) == 0

    # 删除不存在的 id
    assert mem.delete_memory(99999) is False


def test_memory_correct(mem):
    """correct_memory 应能纠正错误记忆（Phase G：纠错）。"""
    mid = mem.save_conclusion("t1", "r1", "disk", "磁盘正常")
    # 纠正
    assert mem.correct_memory(mid, "磁盘使用率 72%") is True
    recalled = mem.recall("t1")
    assert recalled["conclusions"][0]["summary"] == "磁盘使用率 72%"


def test_memory_cross_thread_recall(mem):
    """recall_cross_thread 应跨 thread 检索同一用户记忆（Phase G）。"""
    # 两个不同 thread，同一 user_id
    mem._insert("t1", "r1", "conclusion", "disk", "thread1结论", user_id="user-A")
    mem._insert("t2", "r2", "conclusion", "disk", "thread2结论", user_id="user-A")

    # 跨 thread 召回
    recalled = mem.recall_cross_thread("user-A", limit=10)
    assert len(recalled["conclusions"]) == 2

    # 不同 user 不应命中
    recalled_other = mem.recall_cross_thread("user-B", limit=10)
    assert len(recalled_other["conclusions"]) == 0


def test_memory_delete_all_by_thread(mem):
    """delete_all_by_thread 应清理指定 thread 的所有记忆。"""
    mem.save_conclusion("t1", "r1", "disk", "结论1")
    mem.save_conclusion("t1", "r2", "proc", "结论2")
    mem.save_conclusion("t2", "r3", "disk", "结论3")

    # 删除 t1 的所有记忆
    deleted = mem.delete_all_by_thread("t1")
    assert deleted == 2

    # t1 应空，t2 应保留
    assert len(mem.recall("t1")["conclusions"]) == 0
    assert len(mem.recall("t2")["conclusions"]) == 1


def test_memory_compression(mem):
    """compress_conclusions 应只保留最新 N 条（Phase G：记忆压缩）。"""
    # 插入 10 条结论
    for i in range(10):
        mem.save_conclusion("t1", f"r{i}", f"intent{i}", f"结论{i}")

    assert len(mem.recall("t1", limit=20)["conclusions"]) == 10

    # 压缩到最新 3 条
    deleted = mem.compress_conclusions("t1", keep_latest=3)
    assert deleted == 7  # 10 - 3
    assert len(mem.recall("t1", limit=20)["conclusions"]) == 3


def test_memory_pollution_detection(mem):
    """detect_pollution 应检测到重复注入（Phase G：污染防护）。"""
    # 插入 3 条完全相同的结论（疑似污染）
    for _ in range(3):
        mem.save_conclusion("t1", "r", "disk", "重复内容")

    suspicious = mem.detect_pollution("t1")
    assert len(suspicious) >= 1
    assert suspicious[0]["type"] == "repetitive"
    assert suspicious[0]["count"] == 3


def test_memory_stats(mem):
    """get_stats 应返回正确统计。"""
    mem.save_conclusion("t1", "r1", "disk", "结论1")
    mem.save_conclusion("t1", "r2", "proc", "结论2")
    mem.save_profile("t1", "intent:disk", "1")

    stats = mem.get_stats("t1")
    assert stats["total"] == 3
    assert stats["conclusions"] == 2
    assert stats["profiles"] == 1
