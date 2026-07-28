"""真实模型只读 ReAct smoke 测试。

验证真实模型 → 真实工具 → Observation → 最终回答 → Trace 完整链路。
不得输出 API key。真实服务不可用时如实记录错误，禁止 Fake 冒充。

运行条件：
  - 项目根 .env 中设置 OPENAI_COMPATIBLE_BASE_URL/API_KEY/MODEL
  - use_fake_model=false（或未设置）
  - 显式开启：pytest -m real_chat -s

skip 判断方式：
  - 通过 Settings 加载项目根 .env 后判断真实模型是否已配置（不依赖裸 os.getenv）。
  - 未配置时明确 skip，绝不冒充成功。
  - 绝不输出 API key。不修改真实 .env，不提交密钥。
"""

from __future__ import annotations

import pytest


def _check_real_model_configured() -> tuple[bool, str]:
    """通过 Settings 加载项目根 .env 后判断真实模型是否已配置。

    返回 (是否就绪, skip 原因)。绝不输出 API key（只报告缺失的变量名）。
    不修改真实 .env。
    """
    try:
        from app_v4.settings import Settings
        settings = Settings()
    except RuntimeError as exc:
        return False, f"Settings 加载失败: {exc}"
    if settings.use_fake_model:
        return False, "use_fake_model=true，真实模型 smoke 需要 use_fake_model=false"
    missing = []
    if not settings.openai_compatible_base_url:
        missing.append("OPENAI_COMPATIBLE_BASE_URL")
    if not settings.openai_compatible_api_key:
        missing.append("OPENAI_COMPATIBLE_API_KEY")
    if not settings.openai_compatible_model:
        missing.append("OPENAI_COMPATIBLE_MODEL")
    if missing:
        return False, f"未配置: {', '.join(missing)}，跳过真实模型 smoke"
    return True, ""


_real_model_ready, _real_model_skip_reason = _check_real_model_configured()

# 真实模型 smoke 标记
pytestmark = [
    pytest.mark.real_chat,
    pytest.mark.skipif(not _real_model_ready, reason=_real_model_skip_reason),
]


def _build_real_client():
    """构建使用真实模型的 TestClient。"""
    from pathlib import Path
    import tempfile

    from fastapi.testclient import TestClient

    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps
    from app_v4.main import create_app

    settings = Settings(
        use_fake_model=False,
        rate_limit_enabled=False,
        db_path=str(Path(tempfile.mkdtemp(prefix="appv4_real_")) / "agent_v4.db"),
    )
    deps = build_dependencies(settings)
    token = set_deps(deps)
    app = create_app(settings=settings, dependencies=deps)
    return TestClient(app), token, deps


def test_real_readonly_react_pipeline():
    """真实模型只读 ReAct 完整链路：模型 → 工具 → Observation → 最终回答 → Trace。"""
    client, token, deps = _build_real_client()
    try:
        from app_v4.container import reset_deps

        resp = client.post("/api/chat", json={"message": "帮我分析磁盘"})
        assert resp.status_code == 200
        data = resp.json()

        # 验证 route
        assert data.get("route") == "readonly_diagnosis", \
            f"应进入 readonly_diagnosis, 得到 route={data.get('route')}"

        # 验证调用了只读工具
        tool_names = [c["tool_name"] for c in data.get("tool_calls", [])]
        assert len(tool_names) > 0, "应至少调用一个只读工具"

        # 验证工具返回真实数据（不是 Fake）。
        # 诚实说明：真实模型可能选择被安全校验器拒绝的参数（如越级路径），
        # 此时 status=error 是安全系统正常工作，不代表链路失败。
        # 因此这里只要求"至少一个工具调用成功返回真实数据"，并校验结构完整性。
        success_calls = [tc for tc in data.get("tool_calls", []) if tc["status"] == "success"]
        assert len(success_calls) > 0, "应至少有一个工具调用成功返回真实数据"
        for tc in data.get("tool_calls", []):
            assert "data" in tc, "工具返回应包含 data"
            assert "duration_ms" in tc, "工具返回应包含 duration_ms"
        # 成功的调用应来自真实数据源（source 不是 fake/空）
        for tc in success_calls:
            assert tc.get("source") not in ("", None, "fake"), \
                f"成功调用应来自真实数据源, 得到 source={tc.get('source')}"

        # 验证有最终回答
        assert len(data.get("answer", "")) > 0, "应有最终回答"

        # 验证 answer_source 反映 ReAct 来源
        # 真实成功路径：模型在 decide 中返回 final → answer_source=model_final_answer
        allowed_sources = (
            "model_final_answer",      # 真实成功路径：模型给出 final answer
            "readonly_react_summary",  # 预算/熔断停止后基于 Observation 二次总结
            "llm_summary",             # 其他模型总结路径
            "output_guard_blocked",    # 最终回答被安全扫描拦截（合理路径）
        )
        assert data.get("answer_source") in allowed_sources, \
            f"answer_source 应反映 ReAct 来源, 得到 {data.get('answer_source')}"

        # 真实成功路径联动断言：answer_source=model_final_answer → stop_reason 必为 final_answer
        if data.get("answer_source") == "model_final_answer":
            assert data.get("stop_reason") == "final_answer", \
                f"answer_source=model_final_answer 时 stop_reason 应为 final_answer, 得到 {data.get('stop_reason')}"

        # 验证 Trace 完整
        trace_steps = data.get("trace_steps", [])
        node_names = [s["node"] for s in trace_steps]
        assert "route" in node_names, "Trace 应包含 route 节点"
        assert "readonly_decide" in node_names, "Trace 应包含 readonly_decide 节点"
        assert "readonly_execute" in node_names, "Trace 应包含 readonly_execute 节点"
        assert "scan_observation" in node_names, "Trace 应包含 scan_observation 节点"

        # 验证至少一次 Observation 后再次调用模型（循环证据）
        decide_count = sum(1 for n in node_names if n == "readonly_decide")
        assert decide_count >= 2, \
            f"应至少 2 次 decide（证明 Observation 后再次调用模型）, 得到 {decide_count}"

        # 验证 readonly_trace 存在
        readonly_trace = data.get("readonly_trace", [])
        assert len(readonly_trace) > 0, "readonly_trace 应非空"

        # 验证不输出 API key（通过 Settings 加载，不依赖裸 os.getenv）
        # 注意：只读取 key 用于断言"响应不含 key"，绝不打印/记录/输出真实 key。
        resp_text = resp.text
        from app_v4.settings import Settings
        settings = Settings()
        api_key = settings.openai_compatible_api_key
        if api_key:
            assert api_key not in resp_text, "响应中不得包含 API key"

    finally:
        reset_deps(token)


def test_real_consult_direct_answer():
    """真实模型普通咨询：直接回答，不调工具。"""
    client, token, deps = _build_real_client()
    try:
        from app_v4.container import reset_deps

        resp = client.post("/api/chat", json={"message": "你好"})
        assert resp.status_code == 200
        data = resp.json()

        # 验证 route
        assert data.get("route") == "consult", f"应进入 consult, 得到 route={data.get('route')}"

        # 验证有回答
        assert len(data.get("answer", "")) > 0, "应有回答"

        # 验证不调工具
        assert len(data.get("tool_calls", [])) == 0, "consult 不应调用工具"

    finally:
        reset_deps(token)
