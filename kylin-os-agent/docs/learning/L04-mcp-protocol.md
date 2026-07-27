# L04 — MCP 协议：为什么要做、在本项目里怎么实现

## Concept

**MCP (Model Context Protocol)** 是 Anthropic 主导的一个开放协议，目标是标准化大模型应用与外部工具/数据源的连接方式。

打个比方：想象 USB-C 接口。在此之前每种设备有自己的充电口（ Nokia 圆口、Mini-USB、Micro-USB），互不兼容。USB-C 出来后，一个接口搞定所有设备。**MCP 的野心就是成为 AI Agent 的 USB-C——一套标准协议，让 Agent 即插即用地对接各种工具。**

在我们的项目里，"工具"就是 `df -h` 查磁盘、`ss -ltnp` 查端口、`journalctl` 查日志这些系统命令。MCP 定义的是"Agent 怎么告诉外部世界'我要用什么工具'、'我传了什么参数'、'工具返回了什么结果'"。

## 协议分层

```
应用层 ── Agent 自己（本项目是 AgentOrchestrator）
    │
MCP 层 ──── JSON-RPC 2.0    ← 我们这一层实现的
    │
传输层 ──── HTTP / stdio     ← 我们是用 HTTP 传输
    │
工具层 ──── Tool Registry    ← 复用现有，不动
```

MCP 协议本身是 JSON-RPC 2.0 的超集。JSON-RPC 是最简单的远程调用协议：

```json
// 请求
{"jsonrpc":"2.0", "id":1, "method":"tools/list", "params":{}}
// 响应
{"jsonrpc":"2.0", "id":1, "result":{"tools":[...]}}
```

MCP 在 JSON-RPC 之上定义了 3 个关键方法：
- **`initialize`**：握手，客户端和服务器交换版本和能力信息
- **`tools/list`**：服务器告诉客户端"我能提供哪些工具、每个工具需要什么参数"
- **`tools/call`**：客户端说"调用某个工具、传这些参数"

## 在 Our Code Code

### `app/mcp/schemas.py`

定义**两个东西**：
1. **JSON-RPC 信封** (`JSONRPCRequest`) — 描述一个 JSON-RPC 请求长什么样子
2. **每个工具的 inputSchema** (`TOOL_INPUT_SCHEMAS`) — 描述每个工具接受什么参数、参数类型

`inputSchema` 用的是 **JSON Schema** 语法（和 TypeScript 类型类似的东西）。比如 `port_lookup` 的 schema：

```json
{
  "type": "object",
  "properties": {"port": {"type": "integer", "minimum": 1, "maximum": 65535}},
  "required": ["port"]
}
```

意义：调用方必须传一个 `port` 字段，值必须 1~65535 之间的整数。

### `app/mcp/server.py` (MCPServer class)

**设计核心**：`tools/call` 申请到的请求，**不自己执行**，而是转发给现有的 `ToolRegistry.call()`。

```python
# server.py tools_Call 方法里
call_result = self.tool_registry.call(
    name=name,
    arguments=arguments,
    request_id=f"mcp-{uuid.uuid4()}",
    reason="mcp_tools_call",
)
```

**为什么这样做？**
- 好处 1：应用层沙盒 + Safety Guard 仍然生效（和 `/api/chat` 请求走同一个门）
-好处 2：审计日志和 `/api/chat` 请求记录在同一个表里
-好处 3：不需要维护两套校验逻辑（维护两套 = 维护不一致 = safety 容易破）

**还有两个安全设计**：
1. `tools/list` **只暴露 auto 模式**的工具。confirm（需审批）和 deny（禁止）类工具对外部 MCP 调用方不可见，避免他们通过 MCP 路径绕过审批
2. `tools/call` 走之前先调 `safety_guard.preflight_request()`，和安全标准一致

### `app/main.py` 端点

```python
@app.post("/api/mcp")
def mcp_endpoint(body: JSONRPCRequest) -> dict:
    result = mcp_server.handle_request(body)
    return {"jsonrpc": "2.0", "id": body.id, "result": result}
```

最简单的代理：收到 JSON-RPC 请求 → 转给 MCPServer → 包上 `jsonrpc/id/result` 信封返回。`tools/call` 返回的 MCP 包里的 `content[0].text` 是 JSON 字符串（`call_result` 的序列化），MCP 客户端需要再 json.loads 一次才能读结果。

## Why It Matters

面试和工业化 Agent 工程都强调 MCP。原因：

1. **标准化**：MCP 是模型/Agent 与外部工具、资源、服务解耦的协议层。不写 MCP 协议而直接硬编码工具调用，后续很难支持 Any Client 即插即用
2. **解耦**：前端 Agent、MCP Server、工具实现被协议隔开，修改一边不影响另一边
3. **可扩展**：未来想做多主机监控？加一台 MCP Server Agent 通过 JSON-RPC 连进来就行

## Common Pitfalls

| 坑 | 踩没踩 | 说明 |
|----|--------|------|
| 做一个绕过安全校验的"快速 MCP 通道" | ❌ 没踩 | MCP 走和 `/api/chat` 一样的 ToolRegistry.call() |
| 把 confirm/deny 工具也暴露给外部  | 半踩 → 已修 | `_auto_tool_names` 只包含 auto 模式工具 |
| 返回值不是 JSON-RPC 信封格式 | ❌ 没踩 | `mcp_endpoint` 统一包 |
| 收到不存在的 method 直接报错崩溃 | ❌ 没踩 | 返回 `error(-32601)` 文本 |
| 忘记写 inputSchema，客户端不知道传什么 | ❌ 没踩 | 所有工具都有 schema |

## Further Reading

- **MCP 官方规范**：https://spec.modelcontextprotocol.io/specification/
- **JSON-RPC 2.0**：https://www.jsonrpc.org/specification — 比 REST 更轻量的远程调用协议
- **JSON Schema**：https://json-schema.org/understanding-json-schema/ — 理解 inputSchema 的含义
