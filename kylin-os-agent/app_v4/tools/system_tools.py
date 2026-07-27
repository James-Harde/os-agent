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

runner = CommandRunner()

# ---------------------------------------------------------------------------
# RAG 知识库 — Milvus 混合检索主路径（通过依赖注入容器获取）。
#
# 纵向链路：
#   LangChain Document → RecursiveCharacterTextSplitter → 真实 Embedding
#   → 官方 langchain-milvus Milvus 向量存储（Docker Standalone）
#   → dense retrieval (IP) + BM25 sparse retrieval（BM25BuiltInFunction）
#   → RRF 融合（Function(FunctionType.RERANK)）→ citations。
#
# 错误语义：
#   - 任何环节失败（Milvus 不可达 / Embedding 未配置 / 语料缺失）让异常传播到
#     rag_search 工具边界，由它返回结构化 unavailable，绝不静默回退。
#   - RAG store 通过 ``app_v4.container.get_deps().rag_store`` 注入，保证 app 实例
#     与测试隔离；测试可注入 Fake store。
# ---------------------------------------------------------------------------


@tool
def disk_usage(path: str = ".") -> dict[str, Any]:
    """获取指定目录所在磁盘的使用率。"""
    try:
        resolved = _safe_path(path)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "path": path, "source": "validator"}
    # shutil.disk_usage 需要路径存在；不存在则取最近存在的父目录
    target = resolved
    if not target.exists():
        target = next((p for p in resolved.parents if p.exists()), resolved)
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return {"status": "error", "error": str(exc), "path": str(resolved), "source": "python.shutil"}
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
    try:
        root = _safe_path(path)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "path": path, "source": "validator"}
    if not root.exists():
        return {
            "status": "error",
            "error": f"路径不存在：{path}",
            "path": str(root),
            "source": "validator",
        }
    if not root.is_dir():
        return {
            "status": "error",
            "error": f"不是目录：{path}",
            "path": str(root),
            "source": "validator",
        }
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
    """查询指定端口的占用情况。

    §4.4 #4：优先使用 psutil 获取真实监听端口，避免正则解析 netstat/ss
    输出时把 IPv6 hextet 误判为端口（audit #11）的问题。
    """
    if not 1 <= port <= 65535:
        return {"status": "error", "error": "invalid port", "port": port, "source": "validator"}

    # 主路径：用 psutil 获取真实连接（跨平台、准确）。
    try:
        import psutil
        conns = psutil.net_connections(kind="inet")
        matches = []
        for c in conns:
            # laddr 是 (ip, port) 元组；只对"监听在该端口"的做匹配
            if c.laddr and getattr(c.laddr, "port", None) == port:
                matches.append({
                    "pid": c.pid,
                    "status": c.status,
                    "local": str(c.laddr),
                    "remote": str(c.raddr) if c.raddr else None,
                })
        return {
            "status": "success",
            "source": "psutil.net_connections",
            "port": port,
            "matches": matches[:20],
            "message": "未发现监听进程" if not matches else f"发现 {len(matches)} 个占用",
        }
    except (ImportError, psutil.Error):
        pass

    # 回退：仅在 psutil 不可用时解析命令输出（标注 heuristic）
    if platform.system().lower() == "windows":
        result = runner.run(["netstat", "-ano"], timeout=5)
    else:
        result = runner.run(["ss", "-ltnp"], timeout=5)
        if result["status"] == "unavailable":
            result = runner.run(["netstat", "-tulpn"], timeout=5)

    if result["status"] in ("unavailable", "error", "blocked", "timeout"):
        return {
            "status": result["status"],
            "error": result.get("error", f"command {result['status']}"),
            "port": port,
            "source": "command_runner",
            "message": "当前环境端口查询命令不可用或执行失败",
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
        "message": "未发现监听进程" if not matches else "发现端口占用记录（基于命令解析，可能含 IPv6 误判）",
    }


@tool
def process_list(limit: int = 10) -> dict[str, Any]:
    """查询当前运行的进程列表，按 CPU 占用排序。

    主路径：psutil.process_iter（跨平台，结构化 pid/name/cpu_percent/memory_rss/status/user）。
    回退：tasklist（Windows）/ ps（Linux），标注 source=command_runner，结果仅含原始行。

    返回结构统一：{status, source, processes, total}。
    """
    limit = max(1, min(limit, 50))

    # ---- 主路径：psutil（跨平台，结构化数据）----
    try:
        import psutil

        processes: list[dict[str, Any]] = []
        for proc in psutil.process_iter(
            ["pid", "name", "cpu_percent", "memory_info", "status", "username"]
        ):
            try:
                info = proc.info
                mem = info.get("memory_info")
                processes.append({
                    "pid": info.get("pid"),
                    "name": info.get("name", ""),
                    "cpu_percent": info.get("cpu_percent", 0.0),
                    "memory_rss": mem.rss if mem else None,
                    "status": info.get("status", ""),
                    "username": info.get("username"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # 进程已退出 / 无权限访问（如 SYSTEM 进程）→ 跳过，不中断
                continue
        # 按 CPU 占用降序
        processes.sort(key=lambda p: p.get("cpu_percent") or 0.0, reverse=True)
        return {
            "status": "success",
            "source": "psutil.process_iter",
            "processes": processes[:limit],
            "total": len(processes),
        }
    except ImportError:
        # psutil 未安装 → 回退命令
        pass
    except psutil.Error as exc:
        # psutil 调用异常（如系统不支持）→ 回退命令，记录原因
        return {
            "status": "error",
            "error": f"psutil error: {exc}",
            "source": "psutil.process_iter",
            "processes": [],
            "total": 0,
        }

    # ---- 回退：系统命令（标注 source，结构化程度低于主路径）----
    if platform.system().lower() == "windows":
        result = runner.run(["tasklist", "/FO", "CSV", "/NH"], timeout=5)
    else:
        result = runner.run(
            ["ps", "-eo", "pid,user,comm,%cpu,%mem", "--sort=-%cpu"], timeout=5
        )

    # §4.2 #3：底层命令失败必须返回 error/unavailable，不得包装成 success
    if result["status"] in ("unavailable", "error", "blocked", "timeout"):
        return {
            "status": result["status"],
            "error": result.get("error", f"command {result['status']}"),
            "source": "command_runner",
            "processes": [],
            "total": 0,
            "message": "当前环境进程查询命令不可用或执行失败",
        }

    lines = result.get("stdout", "").splitlines()
    # 回退路径也统一返回 processes 结构（字段对齐，source 标注来源）
    fallback_processes = [
        {"raw": line, "pid": None, "name": None, "cpu_percent": None,
         "memory_rss": None, "status": None, "username": None}
        for line in lines[:limit]
    ]
    return {
        "status": "success",
        "source": "command_runner",
        "processes": fallback_processes,
        "total": len(lines),
        "message": "回退至系统命令解析（结构化程度低于 psutil 主路径）",
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
    if result.get("status") in ("unavailable", "error", "blocked", "timeout"):
        return {
            "status": result["status"],
            "error": result.get("error", f"command {result['status']}"),
            "source": "command_runner",
            "message": "当前环境 journalctl 不可用或执行失败",
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
    if result.get("status") in ("unavailable", "error", "blocked", "timeout"):
        return {
            "status": result["status"],
            "error": result.get("error", f"command {result['status']}"),
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
    """检索麒麟 OS 运维知识库，返回最相关的 FAQ 片段（含可核验引用）。

    用于回答"怎么做""最佳实践"等知识性问题，不执行任何系统命令。
    使用双路融合（BM25 + 稠密 embedding）+ RRF + 重排序，结果带 citation。

    错误语义（fail-fast at tool boundary）：
      - 索引构建失败（语料缺失 / Embedding 未配置 / 网络错误）时返回
        {status: "unavailable", error: ...}，绝不伪装检索成功。
      - 这是为了让 Agent 明确知道"知识库当前不可用"，而不是把空结果
        当成"知识库没有相关内容"。
    """
    try:
        from app_v4.container import get_deps
        store = get_deps().rag_store
        results = store.search(query, top_k=top_k)
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "rag_milvus_hybrid",
            "query": query,
            "results": [],
            "error": f"RAG 检索不可用：{exc}",
        }
    return {
        "status": "success",
        "source": "rag_milvus_hybrid",
        "query": query,
        "results": [
            {
                "score": r["score"],
                "text": r["text"],
                "source": r["source"],
                "citation": r["citation"],       # 可核验引用标签，如 [doc-01]
                "chunk_id": r["chunk_id"],
                "parent_id": r["document_id"],   # document_id 即父文档
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> Path:
    """约束路径到项目根目录防穿越。

    §4.4 #5：路径越界、不存在、类型错误必须返回明确 validation error，
    不得静默回退到更大的目录（修复 audit #10）。
    """
    project_root = Path(__file__).resolve().parents[2]
    candidate = (project_root / path).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        raise ValueError(f"路径越界：{path} 不在项目目录内")
    return candidate


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
    r"""匹配行中是否出现指定端口（修复 audit #11：IPv6 hextet 误判）。

    问题：旧规则 (?<=[:.])8080(?!\d) 会把 IPv6 地址中间的 hextet
    （如 '2001:0db8:8000::1' 中的 8000）误判为监听端口。

    修复：端口必须处于"token 末尾"（后跟空白/行尾），且前面是明确的
    端口分隔符：']'（括号 IPv6）、'*'（通配符）、数字（IPv4 末段）、
    或 '::'（IPv6 未指定地址如 :::8080）。
    这是 psutil 不可用时的回退解析，主路径已用 psutil 避免此问题。
    """
    port_rf = str(port)
    # Case 1: ]:port / *:port / digit:port 处于 token 末尾
    if re.search(r"(?<=[\d\]*]):" + port_rf + r"(?=\s|$)", line):
        return True
    # Case 2: ::port（IPv6 未指定地址，如 :::8080）处于 token 末尾
    if re.search(r"::" + port_rf + r"(?=\s|$)", line):
        return True
    return False


def _summarize_log_lines(rows: list[str]) -> dict[str, Any]:
    warning_words = ["warning", "warn", "failed", "error", "timeout"]
    injection_words = ["忽略之前", "ignore previous", "rm -rf", "system prompt"]
    return {
        "total": len(rows),
        "warning_count": sum(1 for r in rows if any(w in r.lower() for w in warning_words)),
        "prompt_injection_suspect_count": sum(
            1 for r in rows if any(w in r.lower() for w in injection_words)),
    }
