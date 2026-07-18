# L01 — 沙盒是什么？本项目的沙盒如何工作

## Concept

**Sandbox（沙盒）** 是一种安全机制：把一段不可信的代码关进一个有墙壁的"沙箱"里跑，它能看到什么、能做到什么都被墙壁限制住，哪怕代码试图做恶意的操作也影响不到外面。

对于 OS 运维 Agent 来说，沙盒 = 限制 Agent 能执行的命令。比如用户让 Agent"分析一下磁盘"，Agent 只能执行 `df -h`；用户说"帮我删下 /var/log"，这个请求应该被拦截。

## 两层防御体系

本项目采用**两层沙盒**，每层独立生效，一层破了还有另一层挡着：

```
┌─────────────────────────────────────────────────────┐
│ 第一层：Application Sandbox（应用层）                │
│                                                     │
│  - ToolRegistry.call() 硬编码权限门控                │
│  - CommandRunner 命令白名单 + Token 黑名单            │
│  - SafetyGuard 正则匹配高危输入                       │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │ 第二层：OS Layer Sandbox（系统层）           │    │
│  │                                              │    │
│  │  - systemd unit: ProtectSystem, PrivateTmp   │    │
│  │  - 低权限系统用户 (kylinos-agent)             │    │
│  │  - sudoers 最小化白名单                       │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

### 第一层：Application Sandbox（已生效）

**核心文件**：`app/tools/registry.py:38-93` (`ToolRegistry.call` 方法)

**原理**：每次 Agent 要调用任意工具，必须通过这个统一的入口。入口第一行代码就是一个"死命令"：

```python
elif spec.execution_mode != "auto" or not spec.read_only or spec.permission != "read":
    result = {"status": "blocked", ...}
```

翻译成大白话：**四个条件全部满足才放行** (`auto` + `read_only=True` + `permission=read`)。有一个不满足就直接返回 `blocked`，根本不会执行。

每个工具都会被标记 `execution_mode`（auto/confirm/deny）。目前 9 个工具中，只有 7 个是 `auto`，`service_restart` 是 `confirm`（需要审批），`file_delete` 是 `deny`（直接拒绝）。

**第二道封锁**：`app/tools/command_runner.py:57-104`

就算工具权限校验通过了，如果要执行 OS 命令，还要再过 CommandRunner 这一关：
1. **命令白名单**：只有 `df/du/ss/netstat/lsof/ps/journalctl/systemctl/tasklist` 这 9 个命令能过（第 12-21 行 `DEFAULT_ALLOWED_COMMANDS`）
2. **Token 黑名单**：参数里如果出现 `rm, del, kill, chmod, sudo, bash, sh...` 等 25 个敏感词，第 69-75 行直接拦截
3. **不经过 shell**：`subprocess.run(args, shell=False)`，参数是 list 不是字符串 → 在结构上不可能通过管道、分号注入额外命令

### 第二层：OS Layer Sandbox（kylinos-agent.service + install.sh）

**作用**：哪怕第一层被绕过（比如某个程序漏洞），外面的系统还有第二道锁。

`deploy/kylinos-agent.service` 关键参数一览：

| 参数 | 含义 |
|------|------|
| `User=kylinos-agent` | 不跑在 root 下，跑在一个没有家目录的"最低权限用户"下 |
| `NoNewPrivileges=yes` | 子进程永远不能提权回 root |
| `ProtectSystem=strict` | 整个 `/usr`、`/boot`、`/etc` 变成只读 |
| `ProtectHome=read-only` | `/home` 下用户的家目录也是只读 |
| `ReadWritePaths=...` | 唯一一个能写的目录就是 `/opt/kylin-os-agent/data`（存 SQLite 数据库） |
| `CapabilityBoundingSet=` | 清空 Linux capability，Agent 没有任何特权能力 |
| `MemoryDenyWriteExecute=yes` | 代码段不可改、不可动态生成可执行代码 |

`deploy/install.sh` 作用是创建这个低权限用户 + 拷贝源码 + 启动服务。

## Why It Matters

假设你的 Agent 跑在里面一层沙盒里：

- 用户说 `rm -rf /` → **在 SafetyGuard 阶段就被拦截**，永远不会进入命令执行
- 假设某个工具实现有 bug 被注入了一段恶意代码 → 低权限用户只能写 `data/` 目录，`/etc` 都是只读，破坏力被封死
- 假设 Agent 被 Prompt Injection 攻击者远程利用了 → `CapabilityBoundingSet=` 堵死了提权，`PrivateTmp=yes` 让它看不到其他进程的临时文件

**一句话总结**：Defense in Depth（纵深防御）。没有一层是完美的，但多层叠加后，攻击者需要同时突破所有层才能造成损害。

## Common Pitfalls

| 坑 | 踩没踩 | 说明 |
|----|--------|------|
| 沙盒权限设在 LLM prompt 里而不是代码里 | ❌ 没踩 | 权限是代码硬编码，LLM 只是"请求执行" |
| 用 `shell=True` 跑 `os.system(user_input)` | ❌ 没踩 | `subprocess.run(list, shell=False)` 结构上安全 |
| 路径 open 不做边界检查 | ⚠️ 半踩 | `directory_usage` 已限制在 PROJECT_ROOT，但 `disk_usage` 之前可以随便探 → **Phase 1.3 已修复** |
| OS 层隔离缺失 | ⚠️ 已修 | 之前完全没 OS 层 → **Phase 1.4 已补上** |
| 正则不做 Unicode 归一化 | ⚠️ 已修 | 零宽字符可绕过 → **Phase 1.1 已修复** |

## Further Reading

- **subprocess 官方文档**：`subprocess.run` 的 `shell` 参数何时为 True/False
- **systemd.exec(5)**：systemd 可用的所有隔离参数
- **OWASP Command Injection**：https://owasp.org/www-community/attacks/Command_Injection
- **Linux Capabilities**：`man 7 capabilities` ——理解 `CapabilityBoundingSet=` 为什么是空
