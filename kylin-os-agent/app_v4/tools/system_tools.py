"""工具函数 — LangChain @tool 版。

对比 app_v2 改动：
  - 所有工具结果统一包含 source 字段
  - 不可用时返回结构化 unavailable，不伪造数据（修复 B10）
  - 工具内部不计算 duration_ms（由 execute_node 统一计时）
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from app_v4.safety.guard import SafetyGuard
from app_v4.model.command_runner import CommandRunner
from app_v4.rag.pipeline import RAGIndex

runner = CommandRunner()

# ---------------------------------------------------------------------------
# RAG 知识库（懒加载单例）
# 知识库内容来自 rag/eval.py 的 SAMPLE_DATASET（8 条麒麟 OS 运维 FAQ），
# 覆盖磁盘 / 进程 / 端口 / 日志 / 安全 / 服务管理，满足"至少 5 条"要求。
# ---------------------------------------------------------------------------
_rag_index: RAGIndex | None = None


def _get_rag_index() -> RAGIndex:
    """获取（或懒建）RAG 知识库索引单例。"""
    global _rag_index
    if _rag_index is None:
        from app_v4.rag.eval import build_sample_index
        _rag_index = build_sample_index()
    return _rag_index


@tool
def disk_usage(path: str = ".") -> dict[str, Any]:
    """获取指定目录所在磁盘的使用率。"""
    resolved = _safe_path(path)
    usage = shutil.disk_usage(resolved)
    percent = round((usage.used / usage.total) * 100, 2) if usage.total else 0
    return {
        "status": "success",
        "source": "python.shutil",
        "path": str(resolved),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
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
            "path": str(child), "size_bytes": size,
            "type": "dir" if child.is_dir() else "file",
        })
    entries.sort(key=lambda item: item["size_bytes"], reverse=True)
    return {
        "status": "success",
        "source": "python.os_walk",
        "root": str(root),
        "entries": entries[:10],
    }


@tool
def port_lookup(port: int) -> dict[str, Any]:
    """查询指定端口的占用情况。"""
    if not 1 <= port <= 65535:
        return {"status": "error", "error": "invalid port", "port": port, "source": "validator"}

    if platform.system().lower() == "windows":
        result = runner.run(["netstat", "-ano"], timeout=5)
    else:
        result = runner.run(["ss", "-ltnp"], timeout=5)
        if result["status"] == "unavailable":
            result = runner.run(["netstat", "-tulpn"], timeout=5)

    if result["status"] == "unavailable":
        return {
            "status": "unavailable",
            "error": result.get("error", "command not found"),
            "port": port,
            "source": "command_runner",
            "message": "当前环境不支持端口查询命令",
        }

    matches = [
        {"raw_line": line.strip()}
        for line in result.get("stdout", "").splitlines()
        if _line_has_port(line, port)
    ]
    return {
        "status": "success",
        "source": result.get("command", []),
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
            ["ps", "-eo", "pid,user,comm,%cpu,%mem", "--sort=-%cpu"], timeout=5)

    if result["status"] == "unavailable":
        return {
            "status": "unavailable",
            "error": result.get("error", "command not found"),
            "source": "command_runner",
            "message": "当前环境不支持进程查询命令",
        }

    lines = result.get("stdout", "").splitlines()
    return {
        "status": "success",
        "source": result.get("command", []),
        "rows": lines[: max(1, min(limit, 50))],
    }


@tool
def system_logs(limit: int = 50) -> dict[str, Any]:
    """读取系统警告和错误级别的日志。"""
    limit = max(1, min(limit, 100))
    if platform.system().lower() == "windows":
        # Windows 无 journalctl，返回结构化 unavailable（不伪造数据）
        return {
            "status": "unavailable",
            "error": "Windows environment has no journalctl",
            "source": "command_runner",
            "message": "当前环境不支持 journalctl，请使用事件查看器或 Linux 环境",
        }
    result = runner.run(
        ["journalctl", "-p", "warning..alert", "-n", str(limit), "--no-pager"],
        timeout=5,
    )
    if result.get("status") == "unavailable":
        return {
            "status": "unavailable",
            "error": result.get("error", "command not found"),
            "source": "command_runner",
            "message": "当前环境 journalctl 不可用",
        }
    if result.get("status") == "ok" and result.get("stdout"):
        rows = result["stdout"].splitlines()
        return {
            "status": "success",
            "source": result.get("command", []),
            "rows": rows[:limit],
            "summary": _summarize_log_lines(rows),
        }
    return {
        "status": "error",
        "error": result.get("stderr", "unknown error"),
        "source": result.get("command", []),
    }


@tool
def service_status(service: str) -> dict[str, Any]:
    """查询 systemd 服务的运行状态。"""
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}", service):
        return {"status": "error", "error": "invalid service name", "service": service, "source": "validator"}
    result = runner.run(["systemctl", "status", service, "--no-pager"], timeout=5)
    if result.get("status") == "unavailable":
        return {
            "status": "unavailable",
            "error": result.get("error", "command not found"),
            "source": "command_runner",
            "service": service,
        }
    return {
        "status": "success",
        "source": result.get("command", []),
        "service": service,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


@tool
def prompt_injection_scan(content: str) -> dict[str, Any]:
    """扫描不可信文本中的提示词注入风险。"""
    guard = SafetyGuard()
    event = guard.scan_untrusted_output(
        {"tool_name": "prompt_injection_scan", "result": {"content": content}})
    return {
        "status": "success",
        "source": "safety_guard",
        "detected": event["detected"],
        "risk_level": event["risk_level"],
        "reasons": event["reasons"],
        "content_preview": content[:240],
    }


@tool
def service_restart(service: str) -> dict[str, Any]:
    """重启指定的 systemd 服务（有副作用，需要人工审批）。

    注意：此工具为 confirm 权限，不会自动执行。
    """
    return {
        "status": "pending_approval",
        "source": "approval_gate",
        "service": service,
        "message": "此操作需要人工审批，不会自动执行。",
    }


@tool
def rag_search(query: str, top_k: int = 3) -> dict[str, Any]:
    """检索麒麟 OS 运维知识库，返回最相关的 FAQ 片段。

    用于回答"怎么做""最佳实践"等知识性问题，不执行任何系统命令。
    """
    index = _get_rag_index()
    results = index.query(query, k=top_k)
    return {
        "status": "success",
        "source": "rag_index",
        "query": query,
        "results": [
            {
                "score": r["score"],
                "text": r["chunk"]["text"],
                "source": r["chunk"].get("source", ""),
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> Path:
    """约束路径到项目根目录防穿越。"""
    # parents[2] = D:\klin-agent\kylin-os-agent（项目根）
    # parents[3] 会越级到 D:\klin-agent（错误，审计 #10）
    project_root = Path(__file__).resolve().parents[2]
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
    r"""匹配行中是否出现指定端口。

    匹配规则：
      - 端口前必须是 ':' 或 '.' 分隔符（netstat 的 IP:port 格式），
        使用正向后顾 (?<=[:.]) 定位，兼容 '0.0.0.0:port'、'[::]:port'、
        '192.168.x.x:port'、'*:port' 等所有格式；
      - 端口后不能紧跟数字（(?!\d)，避免 80800 被误判为 8080）。
    """
    return bool(re.search(rf"(?<=[:.]){port}(?!\d)", line))


def _summarize_log_lines(rows: list[str]) -> dict[str, Any]:
    warning_words = ["warning", "warn", "failed", "error", "timeout"]
    injection_words = ["忽略之前", "ignore previous", "rm -rf", "system prompt"]
    return {
        "total": len(rows),
        "warning_count": sum(1 for r in rows if any(w in r.lower() for w in warning_words)),
        "prompt_injection_suspect_count": sum(
            1 for r in rows if any(w in r.lower() for w in injection_words)),
    }
