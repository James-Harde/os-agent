"""MCP smoke test — verifies the /api/mcp JSON-RPC endpoint end-to-end.

Safety:
    This script only makes HTTP requests to http://127.0.0.1:8000.
    It issues NO OS-level commands and modifies no files.
    Start the agent first: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Usage: python scripts/mcp_smoke_test.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/mcp"
HEADERS = {"Content-Type": "application/json"}

results: list[tuple[str, bool, str]] = []


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(BASE, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": exc.code, "message": exc.read().decode()}}
    except urllib.error.URLError as exc:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": str(exc)}}


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("== MCP Smoke Test ==\n")

    # 1. initialize
    r = rpc("initialize", req_id=1)
    check(
        "initialize returns serverInfo",
        "result" in r and "serverInfo" in r.get("result", {}),
        f"keys={list(r.get('result', {}).keys())}",
    )

    # 2. tools/list
    r = rpc("tools/list", req_id=2)
    tools = r.get("result", {}).get("tools", [])
    tool_names = [t["name"] for t in tools]
    check("tools/list returns >= 5 tools", len(tools) >= 5, f"got {len(tools)}")
    check(
        "tools/list tools have inputSchema",
        all("inputSchema" in t for t in tools),
    )
    check(
        "confirm/deny tools are hidden",
        "service_restart" not in tool_names and "file_delete" not in tool_names,
        f"visible: {tool_names}",
    )
    check(
        "auto read-only tools are present",
        "disk_usage" in tool_names and "port_lookup" in tool_names,
    )

    # 3. tools/call disk_usage (safe, read-only)
    r = rpc("tools/call", {"name": "disk_usage", "arguments": {}}, req_id=3)
    content_list = r.get("result", {}).get("content", [])
    result_is_error = r.get("result", {}).get("isError", True)
    try:
        inner = json.loads(content_list[0]["text"]) if content_list else {}
    except Exception:
        inner = {}
    check(
        "tools/call disk_usage succeeds",
        not result_is_error and inner.get("status") == "ok",
        f"status={inner.get('status')}",
    )

    # 4. tools/call port_lookup
    r = rpc("tools/call", {"name": "port_lookup", "arguments": {"port": 80}}, req_id=4)
    content_list = r.get("result", {}).get("content", [])
    try:
        inner = json.loads(content_list[0]["text"]) if content_list else {}
    except Exception:
        inner = {}
    check(
        "tools/call port_lookup runs",
        "status" in inner,
        f"status={inner.get('status')}",
    )

    # 5. tools/call unknown tool → isError=true
    r = rpc("tools/call", {"name": "hack_tool", "arguments": {}}, req_id=5)
    check(
        "unknown tool returns isError=true",
        r.get("result", {}).get("isError", False),
    )

    # 6. tools/call confirm-class tool → blocked
    r = rpc("tools/call", {"name": "service_restart", "arguments": {}}, req_id=6)
    is_error = r.get("result", {}).get("isError", False)
    check(
        "confirm-class tool is blocked via MCP",
        is_error,
        f"isError={is_error}",
    )

    # 7. unknown method → error
    r = rpc("fake/method", req_id=7)
    check(
        "unknown JSON-RPC method returns error",
        "error" in r or r.get("result", {}).get("isError"),
    )

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"== Result: {passed}/{total} passed ==")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
