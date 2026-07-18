"""工具函数 — 和 v2 完全一样的 @tool 函数。

教学要点：
  @tool 是 LangChain 的抽象，不是 LangGraph 的。
  所以工具层在 v2（LangGraph）和 v3（纯 LangChain）之间可以原样复用。
  这也说明：工具定义是独立于"编排方式"的。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from app_v3.safety.guard import SafetyGuard
from app_v3.model.command_runner import CommandRunner

runner = CommandRunner()


@tool
def disk_usage(path: str = ".") -> dict[str, Any]:
    """获取指定目录所在磁盘的使用率。"""
    resolved = _safe_path(path)
    usage = shutil.disk_usage(resolved)
    percent = round((usage.used / usage.total) * 100, 2) if usage.total else 0
    return {
        "source": "python.shutil",
        "path": str(resolved),
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "used_percent": percent,
    }


@tool
def directory_usage(path: str = ".") -> dict[str, Any]:
    """获取指定目录下各子目录/文件的占用空间排名。"""
    root = _safe_path(path)
    if not root.is_dir():
        root = Path(__file__).resolve().parents[3]
    entries = []
    for child in root.iterdir():
        try:
            size = _dir_size(child, max_files=2000)
        except OSError:
            continue
        entries.append({
            "path": str(child),
            "size": size,
            "type": "dir" if child.is_dir() else "file",
        })
    entries.sort(key=lambda item: item["size"], reverse=True)
    return {"source": "python.os_walk", "root": str(root), "entries": entries[:10]}


@tool
def port_lookup(port: int) -> dict[str, Any]:
    """查询指定端口的占用情况。"""
    if not 1 <= port <= 65535:
        return {"status": "error", "error": "invalid port", "port": port}
    if platform.system().lower() == "windows":
        result = runner.run(["netstat", "-ano"], timeout=5)
    else:
        result = runner.run(["ss", "-ltnp"], timeout=5)
        if result["status"] == "unavailable":
            result = runner.run(["netstat", "-tulpn"], timeout=5)
    matches = [
        {"raw": line.strip()}
        for line in result.get("stdout", "").splitlines()
        if _line_has_port(line, port)
    ]
    return {
        "source": result.get("command", []),
        "status": result.get("status"),
        "port": port,
        "matches": matches[:20],
        "message": "未发现监听进程" if not matches else "发现端口占用记录",
    }


@tool
def process_list(limit: int = 10) -> dict[str, Any]:
    """查询当前运行的进程列表，按 CPU 占用排序。"""
    if platform.system().lower() == "windows":
        result = runner.run(["tasklist", "/FO", "CSV", "/NH"], timeout=5)
    else:
        result = runner.run(
            ["ps", "-eo", "pid,user,comm,%cpu,%mem", "--sort=-%cpu"], timeout=5,
        )
    lines = result.get("stdout", "").splitlines()
    return {
        "source": result.get("command", []),
        "status": result.get("status"),
        "rows": lines[: max(1, min(limit, 50))],
    }


@tool
def system_logs(limit: int = 50) -> dict[str, Any]:
    """读取系统警告和错误级别的日志。"""
    limit = max(1, min(limit, 100))
    result = runner.run(
        ["journalctl", "-p", "warning..alert", "-n", str(limit), "--no-pager"],
        timeout=5,
    )
    if result.get("status") == "ok" and result.get("stdout"):
        rows = result["stdout"].splitlines()
        return {
            "source": result.get("command", []),
            "status": "ok",
            "rows": rows[:limit],
            "summary": _summarize(rows),
        }
    rows = [
        "kernel: disk sda1 reported high usage warning",
        "sshd: failed password for invalid user admin from 10.0.0.23",
        "app: retrying database connection after timeout",
    ]
    return {"source": "mock_logs", "status": "ok", "rows": rows[:limit],
            "summary": _summarize(rows), "note": "使用模拟日志"}


@tool
def service_status(service: str) -> dict[str, Any]:
    """查询 systemd 服务的运行状态。"""
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}", service):
        return {"status": "error", "error": "invalid service name", "service": service}
    result = runner.run(["systemctl", "status", service, "--no-pager"], timeout=5)
    return {
        "source": result.get("command", []),
        "status": result.get("status"),
        "service": service,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


@tool
def prompt_injection_scan(content: str) -> dict[str, Any]:
    """扫描不可信文本中的提示词注入风险。"""
    guard = SafetyGuard()
    event = guard.scan_untrusted_output(
        {"tool_name": "prompt_injection_scan", "result": {"content": content}}
    )
    return {
        "source": "safety_guard",
        "status": "ok",
        "detected": event["detected"],
        "risk_level": event["risk_level"],
        "reasons": event["reasons"],
        "content_preview": content[:240],
    }


# ---- 内部辅助 ----

def _safe_path(path: str) -> Path:
    project_root = Path(__file__).resolve().parents[3]
    candidate = (project_root / path).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return project_root
    return candidate if candidate.exists() else project_root


def _dir_size(path: Path, max_files: int) -> int:
    if path.is_file():
        return path.stat().st_size
    total, count = 0, 0
    for root, _, files in os.walk(path):
        for fname in files:
            count += 1
            if count > max_files:
                return total
            try:
                total += (Path(root) / fname).stat().st_size
            except OSError:
                continue
    return total


def _line_has_port(line: str, port: int) -> bool:
    return bool(re.search(
        rf"(^|[^\d])(?:0\.0\.0\.0|\[::\]|\*|127\.0\.0\.1|::1)?[:.]{port}([^\d]|$)",
        line,
    ))


def _summarize(rows: list[str]) -> dict[str, Any]:
    warning_words = ["warning", "warn", "failed", "error", "timeout"]
    injection_words = ["忽略之前", "ignore previous", "rm -rf", "system prompt"]
    return {
        "total": len(rows),
        "warning_count": sum(1 for r in rows if any(w in r.lower() for w in warning_words)),
        "prompt_injection_suspect_count": sum(
            1 for r in rows if any(w in r.lower() for w in injection_words)
        ),
    }


# 工具注册表（和 v2 一样的 list，但用途不同：这里直接给 AgentExecutor）
SAFE_TOOLS = [
    disk_usage, directory_usage, port_lookup,
    process_list, system_logs, service_status,
    prompt_injection_scan,
]
