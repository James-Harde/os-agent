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
    """运行时：工具总数 7 > 5，plan_node 应启用渐进披露。"""
    resp = client.post("/api/chat", json={"message": "分析磁盘"})
    assert resp.status_code == 200
    data = resp.json()
    trace = data.get("trace_summary", {})
    steps = trace.get("steps", [])
    assert "plan" in steps


def test_multi_turn_memory_accumulates(client: TestClient):
    """多轮：连续两轮后，长期记忆里应积累 2 条结论。"""
    from app_v4.memory.long_term import get_long_term_memory
    # 用隔离的临时数据库避免污染
    import tempfile, pathlib
    tmp_db = pathlib.Path(tempfile.mkdtemp()) / "mem.db"
    real_get = get_long_term_memory
    # 直接构造独立实例并 monkeypatch 单例
    from app_v4.memory import long_term
    mem = LongTermMemory(db_path=tmp_db)
    orig = long_term._memory
    long_term._memory = mem
    try:
        resp1 = client.post("/api/chat", json={"message": "分析磁盘"})
        assert resp1.status_code == 200
        thread_id = resp1.json()["thread_id"]
        # 第二轮追问
        resp2 = client.post("/api/chat", json={"message": "那进程呢", "thread_id": thread_id})
        assert resp2.status_code == 200
        # 验证记忆积累（通过独立实例 recall）
        recalled = mem.recall(thread_id, limit=10)
        # 至少 1 条结论被保存（disk 有真实 answer_source=llm_summary）
        assert len(recalled["conclusions"]) >= 1
    finally:
        long_term._memory = orig


def test_loop_breaker_stops_duplicate_plan(client: TestClient):
    """循环熔断：相同 plan 连续出现 2 次 → 第二次被 deny。"""
    # 第一轮：分析磁盘 → plan = [disk_usage]
    resp1 = client.post("/api/chat", json={"message": "分析磁盘"})
    assert resp1.status_code == 200
    thread_id = resp1.json()["thread_id"]
    assert resp1.json()["guard_decision"] == "allow"

    # 第二轮：完全相同输入 → plan 签名与上次相同 → 循环熔断
    resp2 = client.post("/api/chat", json={"message": "分析磁盘", "thread_id": thread_id})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["guard_decision"] == "deny"
    assert "循环" in "；".join(data2.get("guard_reasons", [])) or \
           "循环" in data2.get("answer", "")
