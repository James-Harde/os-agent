"""Kylin Secure OS Agent v4 — LangGraph 成品版。

基于 app_v2 骨架补齐：
  - Trace 系统（带 run_id + 查询端点）
  - 自动 thread_id 生成（修复 B06）
  - 工具结果结构化（duration_ms / status / source）
  - 审计接入主链路（修复 B08）
  - 工具输出扫描影响路由（修复 B09）
  - 完整自动化测试
"""
