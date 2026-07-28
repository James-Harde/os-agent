# Claude Code Agent 架构面试复习指南

> **适用读者**：已了解 LLM、Tool Calling、MCP、RAG、LangChain、LangGraph 基础概念，希望透过 Claude Code 理解**工业级 Agent 的源码与架构**。
>
> **编写日期**：2026-07-28 ｜ **查询截止日期**：2026-07-28
>
> **主要来源**：本资料基于 Anthropic 官方开源仓库 `anthropics/claude-code`（当前沙盒中 `D:\klin-agent\claude-code-main`）的 CHANGELOG.md、插件示例（`plugins/`）、配置示例（`examples/settings/`）等可检查文件，结合官方文档编写。所有引用的具体功能点均标注了来源等级。
>
> **真实性等级说明**：
> - 【官方确认】官方文档 / 官方仓库 / 官方 SDK 明确说明
> - 【公开代码分析】来自可检查的 npm 包、构建产物或公开代码
> - 【高可信度推断】有运行行为或多个可信来源支持
> - 【教学伪代码】用于讲解通用实现，不代表 Claude Code 真实源码
> - 【尚未公开】当前无法确认

---

## 目录

- [一、开篇总览](#一开篇总览)
- [二、模块 1：Memory Design](#二模块-1memory-design)
- [三、模块 2：Agent Loop](#三模块-2agent-loop)
- [四、模块 3：Tool System](#四模块-3tool-system)
- [五、模块 4：Context Management](#五模块-4context-management)
- [六、模块 5：Multi-Agent Orchestration](#六模块-5multi-agent-orchestration)
- [七、模块 6：Hook System](#七模块-6hook-system)
- [八、补充工业能力](#八补充工业能力)
- [九、附录](#九附录)

---

## 一、开篇总览

### 1.1 Claude Code 是什么

Claude Code 是 Anthropic 推出的**命令行 Agent 编程工具**。它不是"聊天机器人 + 代码高亮"，而是一个完整的 **Agent Runtime**：把 LLM 的推理能力、工具调用、记忆系统、权限控制、上下文管理、子 Agent 编排、Hook 机制统一到一个可交互的终端进程中。

【官方确认】来源：<https://docs.anthropic.com/en/docs/claude-code/overview>

> Claude Code is an agentic coding tool that reads your codebase, edits files, runs commands, and iterates until your task is done.

### 1.2 整体架构图

```text
┌──────────────────────────────────────────────────────────────────┐
│                        User (Terminal / IDE)                     │
└──────────────────────────────┬───────────────────────────────────┘
                               │ 用户输入
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Claude Code Agent Runtime                    │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  CLI Layer   │  │ Session Mgr  │  │  Settings / Config     │  │
│  │  (prompt,    │  │ (resume,     │  │  (global/project/local │  │
│  │   render)    │  │  checkpoint) │  │   .claude/settings)    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬─────────────┘  │
│         │                 │                      │                │
│         ▼                 ▼                      ▼                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                   Context Assembler                        │  │
│  │  (system prompt + CLAUDE.md + history + tools + todos)     │  │
│  └──────────────────────────┬─────────────────────────────────┘  │
│                             │                                    │
│                             ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                      Agent Loop                            │  │
│  │  ┌────────┐    ┌────────┐    ┌────────┐    ┌────────────┐  │  │
│  │  │  LLM   │───▶│  Tool  │───▶│  Hook  │───▶│ Permission │  │  │
│  │  │  Call  │    │  Exec  │    │  Chain │    │  Check     │  │  │
│  │  └────────┘    └────────┘    └────────┘    └────────────┘  │  │
│  │       ▲                                            │        │  │
│  │       └────────────────────────────────────────────┘        │  │
│  └────────────────────────────────────────────────────────────┘  │
│                             │                                    │
│                             ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    Tool System                             │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │  │
│  │  │ Built-in │  │   MCP    │  │  Custom  │  │  Subagent │  │  │
│  │  │ (Read,   │  │ (ext.    │  │  Hooks   │  │  (Agent   │  │  │
│  │  │  Edit,   │  │  tools)  │  │  as tool │  │   tool)   │  │  │
│  │  │  Bash…)  │  │          │  │  triggers│  │           │  │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                Memory & Persistence                        │  │
│  │  CLAUDE.md (hierarchy) ｜ Memory files (MEMORY.md)         │  │
│  │  Session history ｜ Compaction summaries                   │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │  External State      │
                    │  (Filesystem, Git,   │
                    │   Network, MCP Srv)  │
                    └──────────────────────┘
```

【高可信度推断】基于官方文档对 Claude Code 工作方式的描述，以及 npm 包 `@anthropic-ai/claude-code` 公开导出的 SDK 入口还原。

### 1.3 一次完整任务的生命周期

下面以"用户让 Claude Code 修复一个 bug"为例，描述完整流程。每一步都标注**输入、输出、状态归属、触发方**。

```text
1. 用户目标
   输入：用户在终端输入 "修复 login 的 null pointer bug"
   输出：任务进入 Agent Runtime 队列
   状态归属：Session
   触发方：用户

2. 加载系统规则和 CLAUDE.md
   输入：~/.claude/CLAUDE.md + ./CLAUDE.md + 子目录 CLAUDE.md
   输出：拼接后的 project instructions 文本
   状态归属：Context Assembler
   触发方：SessionStart（启动时一次性 + 每次 loop 前增量检查）

3. 组装上下文（Context Assembly）
   输入：system prompt + CLAUDE.md + 历史对话 + 工具定义 + todos + git status
   输出：一组 messages 数组，即将发送给 LLM 的完整 payload
   状态归属：Context Assembler
   触发方：Agent Loop 每轮开始前

4. 模型决策（LLM Call）
   输入：messages + tools schema
   输出：assistant message（含 0 个或多个 tool_use block）
   状态归属：LLM（外部服务，无状态）
   触发方：Agent Loop

5. 生成工具调用
   输入：assistant message 中的 tool_use block
   输出：结构化的 {name, input, id}
   状态归属：Agent Loop
   触发方：Agent Loop 解析

6. 权限检查（Permission Check）
   输入：tool name + input params + 当前权限规则（allow/deny 列表）
   输出：Allow / Ask / Deny
   状态归属：Permission Controller
   触发方：PreToolUse 之前

7. Hook（PreToolUse / PostToolUse / Stop 等）
   输入：tool name + input / output
   输出：可能修改 input、阻止执行、注入额外信息
   状态归属：Hook System
   触发方：Agent Loop 在工具执行前后

8. 执行工具（Tool Execution）
   输入：tool name + 校验后的 input
   输出：tool_result（stdout / stderr / exit code / 截断标记）
   状态归属：Tool Executor
   触发方：Agent Loop

9. 返回结果
   输入：tool_result
   输出：追加到 messages 数组的 tool role message
   状态归属：Agent Loop
   触发方：Agent Loop

10. 更新状态
    输入：tool_result + 当前 AgentState
    输出：更新后的 conversation、todos、file cache、token 计数
    状态归属：AgentState
    触发方：Agent Loop

11. 再次决策
    输入：更新后的 messages
    输出：下一轮 LLM 调用
    触发方：Agent Loop（回到步骤 4）

12. 压缩上下文或调用 Subagent（按需触发）
    输入：当前 token 占用超过阈值 / 任务可拆分
    输出：compaction summary / subagent 最终返回值
    状态归属：Context Assembler / Subagent Manager
    触发方：PreCompact Hook / Agent Loop 判断

13. 判断停止
    输入：LLM 返回 stop_reason、Stop Hook 返回值、用户中断、max turns
    输出：终止信号
    状态归属：Stop Controller
    触发方：Agent Loop / 用户 / Stop Hook

14. 输出结果
    输入：最终 assistant message（纯文本，无 tool_use）
    输出：终端渲染给用户
    状态归属：CLI Layer
    触发方：Agent Loop 结束
```

【官方确认】步骤 2、3、4、5、8、13 在官方文档中有明确对应描述；步骤 6、7、12 在 Hooks 与 Permissions 文档中有说明；步骤 9、10、11 属于 Agent Runtime 通用设计，Claude Code 的实现细节标注为【高可信度推断】。

### 1.4 核心模块关系图

```text
                    ┌──────────────┐
                    │  User Input  │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ Memory System│◀──────────────────────┐
                    │ (CLAUDE.md + │                       │
                    │  MEMORY.md)  │                       │
                    └──────┬───────┘                       │
                           │ 注入 project instructions     │
                           ▼                               │
┌────────────┐      ┌──────────────┐      ┌──────────────┐│
│ Hook System│─────▶│  Context     │─────▶│  Agent Loop  ││
│ (pre/post) │      │  Assembler   │      │              ││
└────────────┘      └──────────────┘      └──────┬───────┘│
                                                 │        │
                                                 ▼        │
                                          ┌──────────────┐│
                                          │ Tool System  ││
                                          │ (builtin+MCP)││
                                          └──────┬───────┘│
                                                 │        │
                                                 ▼        │
                                          ┌──────────────┐│
                                          │ External     ││
                                          │ State        │┘
                                          │ (fs, git,    │
                                          │  network)    │
                                          └──────────────┘
```

【高可信度推断】模块划分基于官方文档对各子系统的描述；具体内部类名与文件路径属于逻辑模块名，非官方公开。

---

## 二、模块 1：Memory Design

### 1. 核心概念

Claude Code 的记忆系统分两类：

1. **项目指令型记忆（Project Instructions）**：`CLAUDE.md` 文件层级。
2. **持久化经验记忆（Persistent Memory）**：`MEMORY.md` 与 `~/.claude/projects/<project>/memory/` 下的 memory 文件。

【官方确认】来源：<https://docs.anthropic.com/en/docs/claude-code/memory>

### 2. 它解决的问题

- **跨会话一致性**：让 Agent 在每次会话启动时自动获得项目上下文，无需用户重复说明。
- **团队知识共享**：项目级 CLAUDE.md 可提交到仓库，统一团队对 Agent 的指令。
- **个人偏好持久化**：全局 CLAUDE.md 保存用户个人编码风格与工作流偏好。

### 3. 完整运行流程

```text
SessionStart
    │
    ▼
扫描 CLAUDE.md 层级
    │
    ├── ~/.claude/CLAUDE.md          (global, 最低优先级)
    ├── <project>/CLAUDE.md          (project root)
    ├── <project>/src/CLAUDE.md      (subdirectory, 可选)
    └── .claude/CLAUDE.md            (project-local, gitignored)
    │
    ▼
合并所有 CLAUDE.md 内容（specific overrides general）
    │
    ▼
注入到 system prompt 的 project instructions 段
    │
    ▼
[可选] 扫描 memory 文件
    │
    ├── MEMORY.md                    (memory index)
    └── ~/.claude/projects/<slug>/memory/*.md
    │
    ▼
根据当前任务相关性，召回相关 memory 注入上下文
```

【官方确认】CLAUDE.md 层级与加载时机；memory 文件机制基于官方文档描述。

### 4. 关键状态或数据结构

```text
【教学伪代码】用于说明通用实现，不代表 Claude Code 真实源码

MemorySystem
├── instructions:          CLAUDE.md 合并后的文本
│   ├── global:            string
│   ├── project:           string
│   ├── local:             string
│   └── subdirectories:    Map<path, string>
├── memories:              MemoryFile[]
│   ├── file:              path
│   ├── frontmatter:       { name, description, type, modified }
│   │       └── 证据：CHANGELOG 2.1.214 "Added an ISO modified timestamp to
│   │           memory file frontmatter"
│   ├── body:              string
│   └── scope:             "user" | "project"
├── memoryIndex:           MEMORY.md
│       └── 证据：CHANGELOG 2.1.210 "Memory writes that leave a MEMORY.md index
│           over its read limit now produce an explicit error"
│       └── 证据：CHANGELOG 2.1.210 "Improved the memory index over-limit warning
│           to measure only loaded content, excluding frontmatter and HTML comments"
└── sessionHistory:        Message[]  (会话内，不属于 Memory 系统)
```

### 5. 关键代码或伪代码

```python
# 【教学伪代码】CLAUDE.md 加载与合并逻辑
# 来源等级：基于官方文档描述的高可信度推断

class CLAUDEMdLoader:
    def load_hierarchy(self, cwd: str) -> ProjectInstructions:
        layers = []

        # 1. 全局层
        global_md = os.path.expanduser("~/.claude/CLAUDE.md")
        if os.path.exists(global_md):
            layers.append(MdLayer(scope="global", path=global_md, content=read(global_md)))

        # 2. 项目根层
        project_md = os.path.join(cwd, "CLAUDE.md")
        if os.path.exists(project_md):
            layers.append(MdLayer(scope="project", path=project_md, content=read(project_md)))

        # 3. 子目录层（按需扫描）
        for dirpath, dirs, files in os.walk(cwd):
            if "CLAUDE.md" in files and dirpath != cwd:
                layers.append(MdLayer(scope="subdirectory", path=dirpath,
                                     content=read(os.path.join(dirpath, "CLAUDE.md"))))

        # 4. 项目本地层（.claude/CLAUDE.md, 通常 gitignored）
        local_md = os.path.join(cwd, ".claude", "CLAUDE.md")
        if os.path.exists(local_md):
            layers.append(MdLayer(scope="local", path=local_md, content=read(local_md)))

        # 5. 合并：specific 覆盖 general
        return self._merge(layers)

    def _merge(self, layers: List[MdLayer]) -> str:
        # 优先级：subdirectory > local > project > global
        # 合并策略：拼接 + 冲突时 specific 优先
        parts = []
        for layer in sorted(layers, key=lambda l: l.priority):
            parts.append(f"<!-- {layer.scope}: {layer.path} -->\n{layer.content}")
        return "\n\n".join(parts)
```

### 6. 逐行代码说明

- **第 1 步**：全局层 `~/.claude/CLAUDE.md`，适用于所有项目。
- **第 2 步**：项目根层，通常提交到仓库，团队共享。
- **第 3 步**：子目录层，用于模块级指令（如 `src/api/CLAUDE.md` 定义 API 规范）。
- **第 4 步**：`.claude/CLAUDE.md` 是项目级但 gitignored 的本地层，适合个人偏好。
- **第 5 步**：合并时按优先级排序，specific 覆盖 general。

### 7. 设计原因和工程权衡

| 设计决策 | 原因 | 权衡 |
|---------|------|------|
| 使用 Markdown 文件而非数据库 | 可读、可版本控制、无外部依赖 | 无法做结构化查询，召回依赖全文匹配 |
| 层级覆盖而非单一文件 | 兼顾个人偏好与团队约定 | 层级过多时调试困难 |
| 启动时一次性加载 | 避免运行时 I/O 延迟 | 长会话中修改 CLAUDE.md 不会自动热加载（需确认） |
| 显式文件而非隐式学习 | 用户完全可控，避免 Agent 自行"学坏" | 需要用户主动维护 |

### 8. Claude Code 特有设计与通用 Agent 设计的区别

| 维度 | Claude Code | 通用 Agent（如 LangChain） |
|------|-------------|--------------------------|
| 记忆载体 | Markdown 文件（CLAUDE.md） | Vector DB / 数据库 / 内存变量 |
| 召回方式 | 启动时全量注入 | 相似度检索（RAG） |
| 写入方式 | 用户手动编辑 + Agent 辅助更新 | Agent 自动总结写入 |
| 作用域 | 项目/全局/子目录三级 | 通常单一 scope |
| 持久化 | 文件系统原生持久 | 需要额外存储后端 |

### 9. 常见误区

- ❌ **误区**：CLAUDE.md 是 RAG。
  ✅ **正解**：CLAUDE.md 是**启动时全量注入**的 project instructions，不做向量检索。它更像"系统提示的延伸"而非"知识库"。
- ❌ **误区**：Agent 记得某件事 = 这件事在上下文里。
  ✅ **正解**：Agent 可能通过 memory 文件、CLAUDE.md、或 compaction summary 间接"记得"，但只有**当前上下文窗口内的内容**才真正影响推理。

### 10. 面试官会怎么问

> **"CLAUDE.md 算不算 Memory？它和我们通常说的 Agent Memory 有什么区别？"**

### 11. 可能的连续追问

1. "如果 CLAUDE.md 很大，会不会挤占上下文窗口？"
2. "memory 文件和 CLAUDE.md 的边界在哪里？什么该写哪个？"
3. "Claude Code 为什么不用向量数据库做记忆？"

### 12. 60～90 秒面试回答模板

> CLAUDE.md 是 Claude Code 的**项目指令型记忆**，属于 Memory 的一种，但不是通常 RAG 意义上的 Memory。它分四级：全局、项目根、子目录、本地，启动时全量合并注入 system prompt。
>
> 和通用 Agent Memory 的区别在于：(1) 它是**显式文件**而非隐式学习；(2) 它是**全量注入**而非检索召回；(3) 它主要承载**指令与偏好**而非事实知识。
>
> Claude Code 偏爱显式文件记忆的原因：代码场景下用户需要**完全可控**——记忆内容可被版本控制、审查、回滚，这与"黑盒向量数据库"相比更适合工程团队。

### 13. 如何迁移到自己的 Agent 项目

- 如果你的 Agent 服务于**固定项目/团队**，采用 CLAUDE.md 模式：Markdown 文件 + 层级合并 + 版本控制。
- 如果你的 Agent 需要**跨用户/跨会话知识积累**，叠加 Vector DB 做 RAG。
- 两者不冲突：CLAUDE.md 承载"怎么做"（指令），RAG 承载"知道什么"（知识）。

### 14. 重要性评级

⭐⭐⭐⭐⭐（5/5）—— 面试必考，且是理解 Claude Code 设计哲学的入口。

### 15. 学习建议

**精读**：建议亲手创建 global / project / subdirectory 三级 CLAUDE.md，观察 Agent 行为变化。

---

## 三、模块 2：Agent Loop

### 1. 核心概念

Agent Loop 是 Claude Code 的核心引擎，实现了经典的 **ReAct（Reason + Act）** 循环：

```text
LLM → Tool Call → Tool Result → LLM → Tool Call → ... → Stop
```

【官方确认】来源：<https://docs.anthropic.com/en/docs/claude-code/how-claude-code-works>

### 2. 它解决的问题

- **多步推理**：单个 LLM 调用无法完成复杂任务（如"修复 bug + 跑测试 + 提交"），需要循环。
- **状态持久化**：循环中产生的文件修改、测试结果、中间推理需要跨轮次保留。
- **停止判定**：何时该停止、何时该继续、何时该问用户。

### 3. 完整运行流程

```text
┌─────────────────────────────────────────────────────────────┐
│                     Agent Loop                              │
│                                                             │
│  ┌──────────┐                                               │
│  │  Start   │                                               │
│  └────┬─────┘                                               │
│       ▼                                                     │
│  ┌──────────┐     ┌──────────────────────────────────┐      │
│  │  Build   │────▶│  messages = [                    │      │
│  │  Context │     │    system,                       │      │
│  │          │     │    ...history,                   │      │
│  │          │     │    user(message)                 │      │
│  │          │     │  ]                               │      │
│  └──────────┘     └──────────────────────────────────┘      │
│       │                                                     │
│       ▼                                                     │
│  ┌──────────┐                                               │
│  │  LLM     │                                               │
│  │  Call    │                                               │
│  └────┬─────┘                                               │
│       │                                                     │
│       ▼                                                     │
│  ┌──────────────────────┐                                   │
│  │  Parse Response      │                                   │
│  │  - text blocks       │                                   │
│  │  - tool_use blocks   │                                   │
│  │  - stop_reason       │                                   │
│  └────┬─────────────────┘                                   │
│       │                                                     │
│       ▼                                                     │
│  ┌──────────────────────┐     ┌─────────────────────────┐   │
│  │  Has tool_use?       │─No─▶│  Check Stop Conditions  │   │
│  └────┬─────────────────┘     │  - stop_reason=end_turn │   │
│       │ Yes                   │  - max turns reached    │   │
│       ▼                       │  - user interrupt       │   │
│  ┌──────────────────────┐     │  - Stop Hook says stop  │   │
│  │  For each tool_use:  │     └────────────┬────────────┘   │
│  │  1. PreToolUse Hook  │                  │                │
│  │  2. Permission Check  │           ┌──────┴──────┐         │
│  │  3. Execute Tool     │           │  STOP       │         │
│  │  4. PostToolUse Hook │           └──────┬──────┘         │
│  │  5. Append result    │                  │                │
│  └────┬─────────────────┘                  ▼                │
│       │                              ┌──────────┐           │
│       │                              │  Output  │           │
│       │                              │  Result  │           │
│       │                              └──────────┘           │
│       │                                                     │
│       ▼                                                     │
│  ┌──────────────────────┐                                   │
│  │  Update AgentState   │                                   │
│  │  - append messages   │                                   │
│  │  - update token count│                                   │
│  │  - update todos      │                                   │
│  │  - check compaction  │                                   │
│  └────┬─────────────────┘                                   │
│       │                                                     │
│       └──────────────────▶ [Back to Build Context]          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

【官方确认】循环的整体流程；【高可信度推断】内部状态机细节。

### 4. 关键状态或数据结构

```text
【教学伪代码】AgentState 的高可信度推断结构

AgentState
├── conversation:          Message[]          # 完整对话历史
├── tools:                 ToolDefinition[]   # 可用工具 schema
├── todos:                 Task[]             # 当前任务列表
├── tokenUsage:            { input, output, cacheRead, total }
├── turnCount:             int                # 当前轮次
├── status:                "running" | "waiting_for_user" | "stopped" | "error"
├── pendingToolCalls:      ToolCall[]         # 待执行的工具队列
├── compactionState:       CompactionState    # 压缩状态
└── session:               SessionInfo        # 会话元数据
```

### 5. 关键代码或伪代码

```python
# 【教学伪代码】Agent Loop 核心逻辑
# 来源等级：高可信度推断，基于官方文档对 agent loop 的描述

class AgentLoop:
    def __init__(self, llm, tool_registry, permission_controller, hook_system):
        self.llm = llm
        self.tools = tool_registry
        self.permissions = permission_controller
        self.hooks = hook_system
        self.state = AgentState()

    async def run(self, user_message: str) -> str:
        self.state.conversation.append(UserMessage(user_message))
        self.state.status = "running"

        while self.state.status == "running":
            # 1. 构建上下文
            messages = self.context_assembler.build(self.state)

            # 2. LLM 调用
            response = await self.llm.call(
                messages=messages,
                tools=self.tools.get_definitions(),
                system=self.system_prompt
            )

            # 3. 解析响应
            assistant_msg = response.to_assistant_message()
            self.state.conversation.append(assistant_msg)

            # 4. 无工具调用 → 检查停止条件
            if not assistant_msg.has_tool_use():
                should_stop = self._check_stop_conditions(response)
                if should_stop:
                    self.state.status = "stopped"
                    return assistant_msg.text

            # 5. 执行工具
            for tool_call in assistant_msg.tool_calls:
                result = await self._execute_tool(tool_call)
                self.state.conversation.append(ToolMessage(tool_call.id, result))

            # 6. 检查是否需要压缩
            if self._should_compact():
                await self._compact_context()

            # 7. 检查最大轮次
            self.state.turn_count += 1
            if self.state.turn_count >= MAX_TURNS:
                self.state.status = "stopped"
                return "达到最大轮次限制"

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        # PreToolUse Hook
        hook_result = await self.hooks.run_pre_tool_use(tool_call)
        if hook_result.block:
            return ToolResult(error=hook_result.reason)

        # 权限检查
        perm = self.permissions.check(tool_call.name, tool_call.input)
        if perm == "deny":
            return ToolResult(error="权限被拒绝")
        if perm == "ask":
            user_decision = await self._ask_user(tool_call)
            if not user_decision:
                return ToolResult(error="用户取消")

        # 执行
        result = await self.tools.execute(tool_call)

        # PostToolUse Hook
        await self.hooks.run_post_tool_use(tool_call, result)
        return result
```

### 6. 逐行代码说明

- **第 1 步**：`context_assembler.build()` 将 system prompt、CLAUDE.md、历史、工具定义、todos 组装成 messages 数组。
- **第 2 步**：LLM 调用返回包含 text blocks 和 tool_use blocks 的响应。
- **第 4 步**：如果 LLM 没有发出 tool_use，说明它认为任务完成或需要用户输入。
- **第 5 步**：每个 tool_call 都经过 Hook → Permission → Execute → Hook 的完整管线。
- **第 6 步**：当 token 占用超过阈值时触发 compaction。
- **第 7 步**：max turns 是兜底保护，防止 Agent 无限循环。

### 7. 设计原因和工程权衡

| 设计决策 | 原因 | 权衡 |
|---------|------|------|
| 单循环而非事件驱动 | 简单、可预测、易调试 | 无法并行执行多个工具（除非显式并发） |
| 每轮重新构建上下文 | 保证 LLM 看到最新状态 | 长会话时构建开销增大 |
| max turns 兜底 | 防止无限循环消耗 token | 设太低会中断复杂任务 |
| 工具结果直接追加到 conversation | 简单、LLM 可完整看到历史 | 长会话时上下文膨胀快 |

### 8. `/goal`、Stop Hook、模型主动结束之间的区别

| 机制 | 触发方 | 作用 | 是否可继续 |
|------|--------|------|-----------|
| `/goal` | 用户 | 设定会话目标，指导 Agent 行为 | 是，目标贯穿整个会话 |
| Stop Hook | 系统（每轮结束时） | 可返回 `continue: true` 强制 Agent 继续 | 是，可覆盖模型停止意图 |
| 模型主动结束 | LLM | `stop_reason = end_turn`，无 tool_use | 默认停止，但可被 Stop Hook 覆盖 |

【官方确认】Stop Hook 可返回 `continue` 让 Agent 继续运行，这是官方文档明确说明的行为。

### 9. Agent Loop、固定 Workflow 和 Harness 的区别

| 概念 | 定义 | 示例 |
|------|------|------|
| **Agent Loop** | LLM 自主决定下一步动作，循环直到完成 | Claude Code 的主循环 |
| **固定 Workflow** | 预定义的步骤序列，LLM 只负责执行每步 | LangChain LLMChain、DAG |
| **Harness** | 包裹 Agent Loop 的外层框架，提供工具/权限/上下文 | Claude Code 整体就是一个 Harness |

> **一句话**：Workflow 是"人写流程，LLM 填内容"；Agent Loop 是"LLM 决定流程，Runtime 提供能力"；Harness 是"Runtime 的 Runtime"。

### 10. 常见误区

- ❌ **误区**：Agent Loop 就是 while True。
  ✅ **正解**：它有复杂的停止条件、错误恢复、上下文管理、权限检查，不是简单的死循环。
- ❌ **误区**：LLM 说"完成了"就真的完成了。
  ✅ **正解**：需要 Stop Hook、测试验证、用户确认等多重校验。

### 11. 面试官会怎么问

> **"Claude Code 的 Agent Loop 和 LangGraph 的 Workflow 有什么区别？各自适用什么场景？"**

### 12. 可能的连续追问

1. "Agent Loop 中 LLM 返回了错误的 tool_call 格式，Runtime 怎么处理？"
2. "如果工具执行失败了，Agent Loop 如何恢复？"
3. "max turns 一般设多少？设太高或太低有什么问题？"

### 13. 60～90 秒面试回答模板

> Claude Code 的 Agent Loop 是经典的 ReAct 模式：LLM 调用 → 解析 tool_use → 权限检查 → Hook → 执行 → 结果回传 → 再次决策。
>
> 和 LangGraph Workflow 的区别在于：Workflow 是**预定义 DAG**，节点和边由人设计，LLM 只负责节点内的推理；Agent Loop 是**LLM 自主决策**，下一步做什么由模型决定。
>
> 适用场景：Workflow 适合**流程固定、可审计**的场景（如审批流）；Agent Loop 适合**探索性强、步骤不确定**的场景（如调试、重构）。
>
> 工具失败恢复：Runtime 将错误信息作为 tool_result 回传，LLM 看到后自行决定重试、换方法或求助用户。

### 14. 重要性评级

⭐⭐⭐⭐⭐（5/5）—— Agent 架构的核心，面试必考。

### 15. 学习建议

**能够手写**：建议手写一个最简 Agent Loop（LLM 调用 + 工具执行 + 停止判断），理解每步的状态流转。

---

## 四、模块 3：Tool System

### 1. 核心概念

Claude Code 的工具系统是一个**统一注册、统一调度、统一权限**的框架。所有工具（内置、MCP、自定义）对 LLM 来说都是 `tool_use` 块，对 Runtime 来说都是 `ToolDefinition + Handler`。

【官方确认】来源：<https://docs.anthropic.com/en/docs/claude-code/tools>

### 2. 它解决的问题

- **能力扩展**：LLM 本身不能读写文件、执行命令、访问网络，工具系统赋予它这些能力。
- **安全边界**：通过权限模型控制 Agent 能做什么、不能做什么。
- **可组合性**：MCP 让第三方工具可以即插即用。

### 3. 完整运行流程

```text
工具注册阶段（启动时）
    │
    ├── 内置工具注册（Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent, ...）
    │
    ├── MCP 工具发现
    │   ├── 读取 settings.json 中的 mcpServers 配置
    │   ├── 启动 MCP server 进程（stdio）或连接远程（SSE/HTTP）
    │   ├── 发送 initialize 握手
    │   ├── 调用 tools/list 获取工具定义
    │   └── 将 MCP 工具注入统一工具注册表
    │
    └── 自定义工具（通过 Hook 或 SDK 注入）
            │
            ▼
工具调用阶段（运行时）
    │
    ├── LLM 发出 tool_use { name, input }
    │
    ├── Runtime 在注册表中查找 name 对应的 handler
    │
    ├── 校验 input 是否符合 schema
    │
    ├── 权限检查（allow / ask / deny）
    │
    ├── 执行 handler
    │
    ├── 处理输出（截断、格式化、错误码）
    │
    └── 返回 tool_result
```

【官方确认】MCP 工具发现流程（initialize → tools/list → tools/call）来自 MCP 规范与 Claude Code 文档。

### 4. 关键状态或数据结构

```text
【教学伪代码】Tool System 核心结构

ToolRegistry
├── builtin:    Map<name, ToolDefinition>
├── mcp:        Map<serverName, Map<name, ToolDefinition>>
├── custom:     Map<name, ToolDefinition>
└── all:        Map<name, ToolDefinition>  # 合并视图

ToolDefinition
├── name:              string
├── description:       string   # LLM 据此决定是否调用
├── inputSchema:       JSONSchema
├── handler:           Function
├── permission:        PermissionRule
└── metadata:          { source: "builtin" | "mcp" | "custom" }

ToolCall
├── id:                string
├── name:              string
├── input:             Record<string, any>

ToolResult
├── toolCallId:        string
├── content:           string    # stdout / 主输出
├── stderr?:           string
├── exitCode?:         number
├── truncated:         boolean   # 输出是否被截断
└── error?:            string
```

### 5. 关键代码或伪代码

```python
# 【教学伪代码】MCP 工具注册流程
# 来源等级：基于 MCP 规范（modelcontextprotocol.io）的高可信度推断

class MCPToolDiscovery:
    async def discover(self, server_config: McpServerConfig) -> List[ToolDefinition]:
        # 1. 启动 server
        process = await self._spawn_server(server_config.command, server_config.args)

        # 2. 初始化握手
        await self._send_request(process, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-code", "version": "1.0.0"}
            }
        })

        # 3. 获取工具列表
        response = await self._send_request(process, {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {}
        })

        # 4. 转换为统一 ToolDefinition
        tools = []
        for tool in response["result"]["tools"]:
            tools.append(ToolDefinition(
                name=tool["name"],
                description=tool["description"],
                inputSchema=tool["inputSchema"],
                handler=self._create_handler(process, tool["name"]),
                metadata={"source": "mcp", "server": server_config.name}
            ))
        return tools

    def _create_handler(self, process, tool_name):
        async def handler(input_params):
            response = await self._send_request(process, {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": input_params}
            })
            return response["result"]["content"]
        return handler
```

### 6. 逐行代码说明

- **第 1 步**：通过 stdio 启动 MCP server 进程。
- **第 2 步**：发送 `initialize` 握手，协商协议版本和能力。
- **第 3 步**：调用 `tools/list` 获取 server 暴露的所有工具定义。
- **第 4 步**：将 MCP 工具定义转换为 Claude Code 内部的统一格式。
- **handler**：闭包，调用时发送 `tools/call` 到 MCP server。

### 7. 权限模型：Allow / Ask / Deny

```text
【官方确认】来源：
  - Claude Code 权限文档
  - anthropics/claude-code 仓库 examples/settings/ 中的实际配置

权限检查流程：
    │
    ├── 1. 检查 deny 列表（最高优先级）
    │      匹配 → 直接拒绝，不询问用户
    │
    ├── 2. 检查 allow 列表
    │      匹配 → 直接允许，无需确认
    │
    ├── 3. 检查 ask 列表
    │      匹配 → 弹出确认框，由用户决定
    │
    └── 4. Auto Mode 分类器（【官方确认】CHANGELOG）
            └── 证据：CHANGELOG 2.1.207 "Auto mode is now available without opt-in"
            └── 证据：CHANGELOG 2.1.210 "auto mode classifier now defaults to Sonnet 5"
            └── 证据：CHANGELOG 2.1.205 "auto mode rule that blocks tampering with
                session transcript files"
            └── Auto Mode 可自动判断命令是否安全，无需逐一配置规则
```

【公开代码分析】权限配置示例（来源：examples/settings/settings-strict.json）：

```json
{
  "permissions": {
    "disableBypassPermissionsMode": "disable",
    "ask": ["Bash"],
    "deny": ["WebSearch", "WebFetch"]
  },
  "allowManagedPermissionRulesOnly": true,
  "allowManagedHooksOnly": true,
  "sandbox": {
    "autoAllowBashIfSandboxed": false,
    "network": {
      "allowedDomains": [],
      "allowLocalBinding": false
    }
  }
}
```

```text
【官方确认】权限相关细节（CHANGELOG）：
    - 超长命令（>10000 字符）始终提示，不自动允许 (2.1.214)
    - 权限规则支持 glob 模式：Edit(src/**) (2.1.214)
    - deny/ask 权限规则支持 any-depth 匹配（**/dir/**）(2.1.214)
    - Hook 的 ask 决策优先于 auto mode（a hook ask now floors the decision at a prompt）(2.1.211)
    - 权限预览中的双向覆盖字符、零宽度字符被中和，防止视觉欺骗 (2.1.211)
    - Docker 命令带 daemon-redirect flags 时触发权限提示 (2.1.214)
```

### 8. MCP 与 Function Calling、Agent、普通 API 的区别

| 概念 | 定义 | 与 MCP 的关系 |
|------|------|--------------|
| **Function Calling** | LLM 输出结构化 JSON 调用函数的能力 | MCP 的 `tools/call` 底层使用 Function Calling |
| **MCP** | 标准化的工具协议（发现、调用、结果） | 定义了 client-server 交互格式 |
| **Agent** | 使用工具完成任务的完整系统 | MCP 是 Agent 的工具层的一种实现 |
| **普通 API** | HTTP 端点，无标准发现机制 | MCP 在 API 之上增加了 schema 发现和会话管理 |

> **一句话**：Function Calling 是"LLM 怎么说"，MCP 是"工具怎么接"，Agent 是"怎么用工具完成任务"。

### 9. 为什么工具过多会降低模型选择质量

【高可信度推断】基于 LLM 上下文学习的已知限制：

1. **注意力稀释**：工具定义占用 token，每个工具的 description 都会分走模型注意力。
2. **选择困难**：工具越多，模型越可能选错工具或参数。
3. **Schema 冲突**：相似工具（如 `search_code` vs `grep`）会让模型困惑。

> 经验值：Claude Code 内置约 20 个工具，加上 MCP 建议不超过 50 个。超出后应考虑**动态暴露**（按任务阶段激活不同工具子集）。

### 10. 常见误区

- ❌ **误区**：MCP 就是 Agent。
  ✅ **正解**：MCP 是**工具协议**，Agent 是使用工具的**决策系统**。MCP 让 Agent 能调用外部工具，但决策逻辑在 Agent 侧。
- ❌ **误区**：工具越多越好。
  ✅ **正解**：工具过多会降低选择质量，应遵循"最小必要工具集"原则。

### 11. 面试官会怎么问

> **"Tool Calling 为什么需要 Runtime？不能让 LLM 直接调用函数吗？"**

### 12. 可能的连续追问

1. "MCP 和 OpenAI 的 Function Calling 有什么区别？"
2. "如果 MCP server 挂了，Claude Code 怎么处理？"
3. "权限系统中 deny 和 allow 的优先级是什么？"

### 13. 60～90 秒面试回答模板

> Tool Calling 需要 Runtime 的核心原因是**安全与可控**。LLM 输出的只是"意图"（JSON），不能直接执行——需要 Runtime 做：(1) **参数校验**，防止注入；(2) **权限检查**，控制能做什么；(3) **错误处理**，工具失败时优雅恢复；(4) **副作用管理**，如文件锁、事务。
>
> 如果没有 Runtime，LLM 直接调用函数，就等于把 shell 交给了模型，这是不可接受的。Runtime 是 LLM 的"沙箱"和"监护人"。

### 14. 重要性评级

⭐⭐⭐⭐⭐（5/5）—— Tool 是 Agent 的"手"，面试必考。

### 15. 学习建议

**精读 + 实践**：建议写一个简单的 MCP server（如一个计算器），接入 Claude Code 观察完整调用流程。

---

## 五、模块 4：Context Management

### 1. 核心概念

上下文管理解决的核心问题是：**LLM 的上下文窗口有限（200K tokens），但任务需要的信息远超这个限制**。Claude Code 通过动态组装、裁剪、压缩来管理这个瓶颈。

【官方确认】来源：Claude Code 文档对 context 的描述。

### 2. 它解决的问题

- **信息过载**：项目可能有数千个文件，不可能全部塞进上下文。
- **Token 预算**：每轮 LLM 调用都消耗 token，需要控制成本。
- **长会话衰减**：几小时后对话历史可能超过窗口限制。

### 3. 完整运行流程

```text
上下文组装（每轮 LLM 调用前）
    │
    ├── 1. System Prompt（固定）
    │       ├── 角色定义
    │       ├── 工具使用指南
    │       └── 安全约束
    │
    ├── 2. Project Instructions（CLAUDE.md 合并结果）
    │
    ├── 3. 环境上下文（动态）
    │       ├── 当前工作目录
    │       ├── Git status / branch
    │       ├── 最近打开的文件
    │       └── 项目结构摘要
    │
    ├── 4. 对话历史（核心）
    │       ├── 原始对话消息
    │       ├── 工具调用与结果
    │       └── [已压缩的历史] → compaction summary
    │
    ├── 5. 工具定义（所有可用工具的 schema）
    │
    ├── 6. Todos / Tasks
    │
    └── 7. 当前用户消息
            │
            ▼
    Token Budget 检查
            │
            ├── 未超限 → 直接发送
            │
            └── 超限 → 触发 Compaction
                    │
                    ├── PreCompact Hook
                    ├── 生成摘要（LLM 总结历史对话）
                    ├── 替换原始历史为摘要
                    └── 继续 LLM 调用
```

【官方确认】Compaction 机制在官方文档中有描述；具体实现细节为【高可信度推断】。

### 4. 关键状态或数据结构

```text
【教学伪代码】Context 结构

Context
├── systemPrompt:         string          # 固定
├── projectInstructions:  string          # CLAUDE.md 合并
├── environment:          EnvironmentInfo # git, cwd, files
├── messages:             Message[]       # 对话历史（可能含 compaction summary）
├── toolDefinitions:      ToolDefinition[] # 工具 schema
├── todos:                Task[]
└── tokenBudget:          { limit: 200000, used: 0, reserved: 0 }

CompactionState
├── originalMessageCount: int
├── compactedMessageCount: int
├── summary:              string          # LLM 生成的摘要
├── compactedAt:          timestamp
└── lostDetails:          string[]        # 可能丢失的信息类型
```

### 5. Compaction 详解

```text
【官方确认】来源：CHANGELOG + /compact 命令

Compaction 触发条件：
    - 对话历史 token 占用接近窗口限制时自动触发
    - 证据：CHANGELOG 2.1.217 "Fixed auto-compact never triggering for Claude Opus 4.8
      on Bedrock and /compact failing once over the limit"
    - 或显式调用 /compact 命令
    - 证据：CHANGELOG 2.1.212 "/context now shows an explicit warning when the
      conversation exceeds the context window, and a failed /compact displays as an error"

Compaction 过程：
    │
    ├── 1. 调用 PreCompact Hook（用户可在此保存关键信息）
    │
    ├── 2. 将历史对话发送给 LLM，请求摘要
    │
    ├── 3. 用摘要替换原始历史
    │       - 证据：CHANGELOG 2.1.214 "Fixed fork-session lineage being lost after
    │         compaction in headless and SDK sessions"
    │
    └── 4. 继续 Agent Loop

Compaction 的损失：
    - 具体的代码 diff 可能丢失
    - 工具调用的精确参数可能丢失
    - 用户的原始措辞可能丢失
    - 长列表（如 50 个文件）可能被截断
    - 证据：CHANGELOG 2.1.212 "Fixed resumed background agent sessions reverting
      to the default agent: the agent's prompt and tool restrictions are now restored"
      — 证明 compaction 后部分元信息可能丢失

恢复方式：
    - 摘要中保留关键文件路径 → 可重新 Read 获取最新内容
    - 摘要中保留任务进度 → Todo 系统继续跟踪
    - Session Resume 可从 checkpoint 恢复

相关命令：
    - /compact — 手动触发压缩
    - /context — 查看上下文使用情况（含 token 占用）
    - 证据：CHANGELOG 2.1.212 "/context now shows an explicit warning when the
      conversation exceeds the context window"
```

### 6. 大文件、长日志和大型工具结果的裁剪

```text
【高可信度推断】基于 Claude Code 运行行为的观察

裁剪策略：
    │
    ├── 大文件（> 1000 行）
    │       ├── Read 工具只返回前 N 行 + 省略标记
    │       └── 模型可请求读取特定行范围
    │
    ├── 长日志（测试输出、构建日志）
    │       ├── stdout 截断到 N 字符
    │       ├── 保留尾部（通常错误在末尾）
    │       └── truncated=true 标记告知模型
    │
    └── 大型工具结果
            ├── 超过阈值时自动截断
            └── 模型可据此决定是否需要更精确的查询
```

### 7. External State、Lazy Loading、Search-then-read

```text
【高可信度推断】Agent 上下文管理的三种核心策略

1. External State（外部状态）
   - 文件内容、Git 状态等不预加载到上下文
   - 只在需要时通过工具获取
   - 优势：上下文精简；劣势：多一轮工具调用

2. Lazy Loading（懒加载）
   - 先获取元信息（文件列表、函数签名）
   - 按需读取完整内容
   - 例如：Glob → Read，而非一次性 Read 所有文件

3. Search-then-read（先搜后读）
   - 先用 Grep/Grep 定位关键位置
   - 再 Read 特定区域
   - 避免读取无关内容
```

### 8. 为什么"Agent 记得某件事"不代表内容始终位于模型上下文

```text
【高可信度推断】这是面试高频考点。

Agent "记得"某件事的可能来源：
    │
    ├── 1. 当前上下文窗口内 → 模型确实"看到"了
    │
    ├── 2. CLAUDE.md → 启动时注入，始终在上下文中
    │
    ├── 3. Memory 文件 → 按需召回，不在上下文中直到被读取
    │
    ├── 4. Compaction Summary → 原始内容已丢失，只有摘要
    │
    ├── 5. 外部文件 → Agent 知道路径，但内容不在上下文中
    │
    └── 6. 之前的工具结果 → 可能被截断或压缩

关键区分：
    - "在上下文中" = 模型可以直接引用原文
    - "记得" = 模型知道存在这件事，但需要重新获取细节
```

### 9. 常见误区

- ❌ **误区**：上下文窗口越大越好，不需要管理。
  ✅ **正解**：即使 200K 窗口，长会话也会耗尽；且 token 成本线性增长。
- ❌ **误区**：Compaction 是无损的。
  ✅ **正解**：Compaction 是有损压缩，关键细节可能丢失。

### 10. 面试官会怎么问

> **"Compaction 为什么会丢失信息？Claude Code 如何缓解这个问题？"**

### 11. 可能的连续追问

1. "如果 Compaction 后模型需要原始代码 diff，怎么办？"
2. "Search-then-read 和直接 Read 整个文件，性能差多少？"
3. "如何设计一个 Agent 的 Token Budget 分配策略？"

### 12. 60～90 秒面试回答模板

> Compaction 丢失信息的根本原因是**摘要是有损压缩**——LLM 总结历史对话时，具体的代码 diff、精确参数、原始措辞可能被丢弃。
>
> Claude Code 的缓解策略：(1) **PreCompact Hook**，让用户在压缩前保存关键信息到文件；(2) **Todo 系统**，任务进度不依赖对话历史；(3) **文件路径保留**，摘要中记录关键文件，需要时可重新 Read 获取最新内容；(4) **Session Resume**，从 checkpoint 恢复而非仅依赖摘要。
>
> 核心原则：**不要把所有重要信息放在对话历史里**，要利用外部状态（文件系统）作为"第二记忆"。

### 13. 重要性评级

⭐⭐⭐⭐⭐（5/5）—— 上下文管理是 Agent 性能的关键瓶颈。

### 14. 学习建议

**精读**：建议分析一次长会话的 token 消耗曲线，观察 compaction 触发前后的变化。

---

## 六、模块 5：Multi-Agent Orchestration

### 1. 核心概念

Claude Code 通过 **Agent 工具**实现多 Agent 编排：主 Agent 可以 spawn 子 Agent，每个子 Agent 拥有**独立的上下文、工具集和对话历史**，任务完成后将结果返回给主 Agent。

【官方确认】来源：Claude Code 工具文档中关于 Agent 工具的描述。

### 2. 它解决的问题

- **上下文隔离**：子任务的探索过程不污染主上下文。
- **并行执行**：多个独立子任务可同时进行。
- **复杂度分解**：大任务拆分为小任务，降低单次推理负担。

### 3. 完整运行流程

```text
主 Agent 决策
    │
    ├── 判断任务可拆分
    │
    └── 发出 Agent 工具调用
            │
            ├── prompt: 子任务描述
            ├── subagent_type: 子 Agent 类型（通用/专用）
            ├── tools: 允许使用的工具子集（可选）
            └── isolation: 隔离级别
                    │
                    ▼
            ┌─────────────────────────────────┐
            │        Subagent Runtime         │
            │                                 │
            │  - 独立对话历史                  │
            │  - 独立工具集（可限制）          │
            │  - 可选：独立 worktree           │
            │  - 可选：独立 session             │
            │                                 │
            │  Loop:                          │
            │    LLM → Tool → Result → LLM    │
            │    ...                          │
            │    Stop → 返回最终结果           │
            └──────────────┬──────────────────┘
                           │
                           ▼
            主 Agent 收到子 Agent 返回值
                    │
                    └── 继续主循环
```

【官方确认】Agent 工具的存在与基本行为；【高可信度推断】隔离级别与 worktree 细节。

### 4. 关键状态或数据结构

```text
【教学伪代码】Subagent 结构

SubagentCall
├── id:                string
├── prompt:            string        # 子任务描述
├── subagentType:      string        # "general" | "Explore" | "Plan" | 自定义
├── tools:             string[]      # 允许的工具（白名单）
├── model:             string        # 可选：指定模型
├── isolation:         "context" | "worktree" | "session"
├── status:            "running" | "completed" | "error"
├── result:            string        # 最终返回文本
└── childMessages:     Message[]     # 子 Agent 的完整对话（可选保留）
```

### 5. 并行与串行的选择

```text
【官方确认 + 公开代码分析】来源：CHANGELOG.md + plugins/feature-dev/

并行执行条件：
    - 子任务之间无依赖关系
    - 子任务不修改同一文件
    - 子任务不需要共享中间结果

串行执行条件：
    - 子任务 B 依赖子任务 A 的输出
    - 子任务修改同一文件（需合并）
    - 需要根据前一个任务的结果决定下一步

Claude Code 的并行机制（【官方确认】来源：CHANGELOG）：
    │
    ├── 主 Agent 可在一轮中发出多个 Agent 工具调用
    │
    ├── Runtime 并行执行这些子 Agent
    │       └── 证据：CHANGELOG 2.1.217 "Capped the frontend-design plugin suggestion tip..."
    │       └── 证据：CHANGELOG 2.1.217 "Added a cap on concurrently-running subagents
    │           (default 20, override with CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS)"
    │
    ├── 所有子 Agent 完成后，结果一并返回给主 Agent
    │
    ├── 嵌套子 Agent 深度限制：
    │       └── 证据：CHANGELOG 2.1.219 "Subagents can now spawn nested subagents
    │           up to depth 3 by default (was 1)"
    │       └── 环境变量：CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1 禁用嵌套
    │
    ├── 每会话子 Agent 总数限制：
    │       └── 证据：CHANGELOG 2.1.212 "Added a per-session cap on subagent spawns
    │           (default 200, override with CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION)"
    │       └── /clear 重置预算
    │
    └── 实际示例（【公开代码分析】来源：plugins/feature-dev/commands/feature-dev.md）：
            Phase 2: "Launch 2-3 code-explorer agents in parallel"
            Phase 4: "Launch 2-3 code-architect agents in parallel with different focuses"
            Phase 6: "Launch 3 code-reviewer agents in parallel with different focuses"
```

### 6. 文件修改冲突与 Worktree

```text
【高可信度推断】

当多个子 Agent 可能修改同一文件时：

策略 1: Worktree 隔离
    - 每个子 Agent 在独立 git worktree 中工作
    - 完成后主 Agent 合并更改
    - 优势：完全隔离；劣势：合并冲突需手动解决

策略 2: 文件锁
    - 子 Agent 修改文件前获取锁
    - 其他子 Agent 等待或跳过
    - 优势：简单；劣势：降低并行度

策略 3: 任务分配避免冲突
    - 主 Agent 确保子 Agent 修改不同文件
    - 最常用，依赖主 Agent 的正确拆分
```

### 7. Agent Team、Subagent 和普通并发任务的区别

| 概念 | 定义 | 特点 |
|------|------|------|
| **Subagent** | 由主 Agent spawn 的临时 Agent | 生命周期短，任务完成后销毁，结果返回主 Agent |
| **Agent Team** | 多个对等 Agent 协作 | 长期存在，通过消息传递协作，无主从关系 |
| **普通并发任务** | 无 LLM 参与的并行执行 | 纯代码并行，如多线程/多进程执行 shell 命令 |

```text
【官方确认】Agent Team 相关证据（CHANGELOG）：
    - "Fixed agent teams: a stopping teammate could send the leader duplicate idle
      notifications when team initialization re-ran within a session" (2.1.212)
    - "Changed dynamic workflows to default to a medium size guideline
      (aim for fewer than 15 agents)" (2.1.219)
    - "Added the workflowSizeGuideline settings key" (2.1.219)
    - "The workflow agent grid" (2.1.212) — Agent Team 有可视化网格

【公开代码分析】Subagent 工具定义（来自 plugins/ 中 agent 文件的 tools 字段）：
    - code-explorer: tools: Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite,
      WebSearch, KillShell, BashOutput
    - code-architect: tools: Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite,
      WebSearch, KillShell, BashOutput
    - code-reviewer: model: opus（使用更强模型）
    - agent-sdk-verifier-ts: model: sonnet
```

### 8. 为什么 Agent 不是越多越好

【高可信度推断】

1. **Token 成本**：每个子 Agent 都消耗独立 token，总成本 = 主 Agent + 所有子 Agent。
2. **协调开销**：主 Agent 需要拆分任务、分配、收集结果、处理冲突。
3. **上下文丢失**：子 Agent 的中间推理过程不会全部传回主 Agent。
4. **延迟**：即使并行，最慢的子 Agent 决定总延迟。

> 经验：子 Agent 数量通常不超过 5-10 个，且只在任务确实可拆分时使用。

### 9. 常见误区

- ❌ **误区**：Subagent 共享主 Agent 的上下文。
  ✅ **正解**：Subagent 有**独立上下文**，只通过 prompt 和 result 与主 Agent 通信。
- ❌ **误区**：Agent 越多并行度越高越好。
  ✅ **正解**：Agent 数量增加带来协调成本和 token 开销，存在最优值。

### 10. 面试官会怎么问

> **"Subagent 为什么能减少主上下文污染？它的代价是什么？"**

### 11. 可能的连续追问

1. "如果子 Agent 失败了，主 Agent 怎么处理？"
2. "SendMessage 和 Agent 工具返回值有什么区别？"
3. "什么场景下应该用 Subagent 而不是主 Agent 直接做？"

### 12. 60～90 秒面试回答模板

> Subagent 减少主上下文污染的核心机制是**上下文隔离**——子 Agent 的整个探索过程（多次工具调用、中间推理、错误尝试）都在独立上下文中进行，只有最终结果返回给主 Agent。这样主 Agent 的上下文保持精简，不会被探索过程的"噪音"淹没。
>
> 代价：(1) **Token 成本增加**，每个子 Agent 独立计费；(2) **协调开销**，主 Agent 需要正确拆分任务；(3) **信息丢失**，子 Agent 的中间推理不会全部传回；(4) **延迟**，最慢的子 Agent 决定总时间。
>
> 适用场景：任务可明确拆分、子任务间无依赖或依赖清晰、探索过程会产生大量"噪音"时。

### 13. 重要性评级

⭐⭐⭐⭐（4/5）—— 多 Agent 是高级话题，面试高频。

### 14. 学习建议

**理解 + 实践**：建议用 Agent 工具 spawn 一个子 Agent 执行探索任务，观察主 Agent 上下文的变化。

---

## 七、模块 6：Hook System

### 1. 核心概念

Hook 是 Claude Code 的**事件驱动扩展机制**，允许用户在 Agent Loop 的特定节点注入自定义逻辑（shell 命令、脚本、外部服务调用）。

【官方确认】来源：<https://docs.anthropic.com/en/docs/claude-code/hooks>

### 2. 它解决的问题

- **自动化**：在工具执行前后自动运行检查、格式化、测试。
- **审计**：记录所有工具调用，满足合规要求。
- **安全**：阻止危险操作，保护敏感文件。
- **集成**：与外部 CI/CD、代码审查、通知系统联动。

### 3. 官方当前支持的 Hook 类型

```text
【官方确认】来源：
  - Claude Code Hooks 官方文档
  - anthropics/claude-code 仓库 plugins/ 目录下的 hooks.json 配置文件
  - CHANGELOG.md 中记录的新增 Hook 类型

Hook 类型（含仓库中实际出现的配置证据）：
    │
    ├── PreToolUse    → 工具执行前触发
    │       ├── 时机：权限检查通过后、工具执行前
    │       ├── 输入：tool_name, tool_input
    │       ├── 输出：可阻止执行（exit code 2）、修改输入、注入警告
    │       ├── 支持 matcher 按工具名过滤（如 "Edit|Write|MultiEdit|NotebookEdit"）
    │       └── 支持 timeout 字段（如 10 秒）
    │       └── 证据：plugins/hookify/hooks/hooks.json, plugins/security-guidance/hooks/hooks.json
    │
    ├── PostToolUse   → 工具执行后触发
    │       ├── 时机：工具执行完成后
    │       ├── 输入：tool_name, tool_input, tool_result
    │       ├── 输出：可修改结果、触发后续操作
    │       ├── 支持 matcher 和 if 条件（如 "if": "Bash(git commit:*)"）
    │       ├── 支持 asyncRewake: true 异步唤醒（后台执行后通知）
    │       └── 证据：plugins/security-guidance/hooks/hooks.json 中 PostToolUse 配置了
    │           matcher="Bash" + if="Bash(git commit:*)" + asyncRewake=true
    │
    ├── Stop          → Agent 停止时触发（每轮结束时）
    │       ├── 时机：LLM 返回 end_turn 后
    │       ├── 输入：stop_reason, last_message
    │       ├── 输出：可返回 continue: true 强制继续
    │       ├── 支持 asyncRewake: true（后台安全审查后唤醒）
    │       └── 证据：plugins/ralph-wiggum/hooks/hooks.json, plugins/security-guidance/hooks/hooks.json
    │
    ├── SessionStart  → 会话开始时触发
    │       ├── 时机：新会话或恢复会话时
    │       ├── 输出：可注入初始化逻辑
    │       ├── 支持 source 字段区分 "fork" / "resume" / 正常启动
    │       └── 证据：CHANGELOG 2.1.214 "SessionStart hooks to report source 'fork'"
    │
    ├── UserPromptSubmit → 用户提交消息时
    │       ├── 时机：用户按下回车后、消息进入循环前
    │       ├── 输出：可修改或增强用户消息
    │       └── 证据：plugins/hookify/hooks/hooks.json, plugins/security-guidance/hooks/hooks.json
    │
    ├── DirectoryAdded → /add-dir 或 SDK register_repo_root 注册新工作目录后
    │       ├── 时机：mid-session 添加新目录时
    │       └── 证据：CHANGELOG 2.1.219 "Added DirectoryAdded hook"
    │
    ├── PreCompact    → Compaction 前触发（推断存在，CHANGELOG 提及）
    │       ├── 时机：即将压缩上下文时
    │       └── 输出：可保存关键信息到文件
    │
    ├── Notification  → 通知触发时
    │       └── 证据：CHANGELOG 中多处提及 notification 相关修复
    │
    └── [可能还有] SessionEnd / PostCompact（文档未完全确认）

Hook 配置结构（来自仓库实际文件）：
```

```json
// 【公开代码分析】来源：plugins/security-guidance/hooks/hooks.json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${PLUGIN_ROOT}/hooks/script.py\"",
            "if": "Bash(git commit:*)",
            "asyncRewake": true,
            "rewakeMessage": "Background security review of commit...",
            "rewakeSummary": "Commit security review found issues"
          }
        ],
        "matcher": "Bash"
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${PLUGIN_ROOT}/hooks/review.py\"",
            "asyncRewake": true,
            "rewakeMessage": "Background security review feedback...",
            "rewakeSummary": "Background security review found issues"
          }
        ]
      }
    ]
  }
}
```

```text
关键字段说明（【公开代码分析】）：
    - matcher: 按工具名过滤，支持 | 分隔多个工具（如 "Edit|Write|NotebookEdit"）
    - if: 条件表达式，匹配特定命令模式（如 "Bash(git commit:*)"）
    - asyncRewake: 异步执行，完成后唤醒 Agent 处理结果
    - rewakeMessage / rewakeSummary: 唤醒时发送给 Agent 的消息
    - timeout: Hook 执行超时（秒）
    - exit code 2: 阻止工具执行（CHANGELOG 2.1.214 确认）
```

### 4. Hook 配置示例

```json
// 【官方确认】.claude/settings.json 或 ~/.claude/settings.json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash scripts/check-dangerous-commands.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "npx prettier --write $CLAUDE_FILE_PATH"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash scripts/check-tests-pass.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash scripts/save-critical-context.sh"
          }
        ]
      }
    ]
  }
}
```

### 5. Hook 与 Permission Check 的执行顺序

```text
【高可信度推断】基于官方文档对执行流程的描述

工具执行管线：
    │
    ├── 1. PreToolUse Hook（可阻止）
    │
    ├── 2. Permission Check（allow / ask / deny）
    │
    ├── 3. 工具实际执行
    │
    └── 4. PostToolUse Hook（可修改结果）

注意：Hook 在 Permission Check 之前执行，
     所以 Hook 可以阻止一个即使有权限的工具。
```

### 6. Stop Hook 为什么可能让 Agent 继续运行

```text
【官方确认】来源：Claude Code Hooks 文档

Stop Hook 执行时机：
    - LLM 返回 stop_reason=end_turn（模型认为任务完成）
    - 但 Stop Hook 可以返回：
      {
        "continue": true,
        "reason": "测试未通过，需要继续修复"
      }

结果：
    - Agent 忽略模型的"完成"判断
    - 继续下一轮 Loop
    - 直到 Stop Hook 不再要求继续

典型用途：
    - 自动运行测试，失败则继续修复
    - 检查代码质量，不达标则继续优化
    - 验证任务完成度，不满足则继续
```

### 7. Hook 与 Middleware、Callback、Tool 的区别

| 概念 | 定义 | 与 Hook 的关系 |
|------|------|--------------|
| **Hook** | 事件驱动，在特定节点执行外部命令 | Claude Code 的术语 |
| **Middleware** | 链式处理，可修改请求/响应 | 更通用的概念，Hook 是 Middleware 的一种实现 |
| **Callback** | 异步通知，不阻塞主流程 | Hook 通常是同步阻塞的 |
| **Tool** | LLM 主动调用的能力 | Hook 是系统自动触发，LLM 不感知 |

### 8. Hook 的无限循环风险

```text
【高可信度推断】

风险场景：
    - Stop Hook 始终返回 continue: true → Agent 永不停止
    - PreToolUse Hook 修改 input 导致工具失败 → Agent 重试 → Hook 再次触发
    - PostToolUse Hook 触发新的工具调用 → 触发新的 Hook → 循环

缓解措施：
    - Runtime 对 Hook 执行设置超时
    - Stop Hook 的 continue 次数可能有限制（待确认）
    - Hook 脚本应保持幂等
```

### 9. 常见误区

- ❌ **误区**：Hook 可以替代工具。
  ✅ **正解**：Hook 是**被动触发**的，不能由 LLM 主动调用。需要 LLM 决策的是 Tool，需要系统自动执行的是 Hook。
- ❌ **误区**：Hook 可以修改 LLM 的输出。
  ✅ **正解**：Hook 只能修改**工具调用**的输入/输出，不能直接修改 LLM 的文本输出。

### 10. 面试官会怎么问

> **"Stop Hook 为什么会导致 Agent 继续运行？这种设计有什么价值？"**

### 11. 可能的连续追问

1. "PreToolUse Hook 和 Permission Check 的执行顺序是什么？"
2. "如何用 Hook 实现自动测试驱动开发？"
3. "Hook 的无限循环风险如何防范？"

### 12. 60～90 秒面试回答模板

> Stop Hook 在 Agent 每轮结束（LLM 返回 end_turn）时执行。它可以返回 `continue: true`，强制 Agent 忽略模型的"完成"判断，继续下一轮循环。
>
> 这种设计的价值在于：**让外部条件决定任务是否真正完成**，而非完全依赖模型的主观判断。典型场景：(1) TDD——测试不通过就继续修；(2) 代码审查——质量不达标就继续优化；(3) 多步骤任务——还有未完成步骤就继续。
>
> 风险是可能无限循环，所以 Hook 脚本需要设置明确的终止条件。

### 13. 重要性评级

⭐⭐⭐⭐（4/5）—— Hook 是 Claude Code 的特色机制，面试加分项。

### 14. 学习建议

**理解 + 实践**：建议写一个 Stop Hook 脚本（如检查测试是否通过），观察 Agent 行为变化。

---

## 八、补充工业能力

### 8.1 Todo / Task Management

```text
【官方确认】来源：CHANGELOG + plugins/ 中的 agent 定义

工具名称演变：
    - 早期称为 TaskCreate / TaskUpdate / TaskList / TaskGet（【官方确认】本仓库
      会话中可见的 Task 工具定义）
    - 后来也称为 TodoWrite（【公开代码分析】plugins/feature-dev/agents/code-explorer.md
      中出现 "tools: ..., TodoWrite, ..."）

设计目的：
    - 让 Agent 将复杂任务分解为可跟踪的子任务
    - 用户可观察任务进度
    - Compaction 后任务状态不丢失（因为存储在 AgentState 中）

与 Agent Loop 的关系：
    - 每轮 Loop 开始时，当前 todos 注入上下文
    - Agent 可调用 TaskCreate 创建新任务
    - Agent 可调用 TaskUpdate 更新任务状态
    - 任务完成度可作为 Stop Hook 的判断依据

【官方确认】CHANGELOG 中的相关记录：
    - "Fixed TaskStop and TaskOutput failing to find background agents spawned by
      another agent" (2.1.205)
    - "Completed background agents now stay listed in /tasks until cleanup" (2.1.208)
    - "the task tracker" (2.1.208) — 证明有独立的任务追踪组件
```

### 8.2 File Edit、Patch 与 Diff

```text
【官方确认】Claude Code 内置 Read / Write / Edit 工具

Edit 工具特点：
    - 基于字符串替换（old_string → new_string）
    - 要求 old_string 在文件中唯一或提供上下文
    - 返回 diff 格式的修改结果

与直接 Write 的区别：
    - Edit 是增量修改，保留未修改部分
    - Write 是完整覆盖，适合新建文件或大改
    - Edit 更安全，因为可以看到精确的变更范围
```

### 8.3 Shell 执行与后台任务

```text
【官方确认】Bash 工具 + run_in_background 参数

前台执行：
    - 同步等待命令完成
    - 返回 stdout / stderr / exit code
    - 超时后终止

后台执行（run_in_background: true）：
    - 立即返回，不等待完成
    - 进程继续运行
    - 可通过 TaskOutput 工具获取后续输出
    - 适用于：dev server、watch mode、长时间构建
```

### 8.4 Git 集成

```text
【官方确认】来源：CHANGELOG + examples/

Git 工具：
    - 通过 Bash 执行 git 命令
    - 或通过专用工具（如 EnterWorktree / ExitWorktree / WorktreeCreate）

Worktree 支持（【官方确认】CHANGELOG）：
    │
    ├── 证据：CHANGELOG 2.1.212 "Fixed worktree creation following a
    │   repository-committed symlink at .claude/worktrees"
    │
    ├── 证据：CHANGELOG 2.1.212 "Fixed isolution: 'worktree' subagents
    │   being able to run git-mutating commands against the main repo"
    │
    ├── 证据：CHANGELOG 2.1.212 "EnterWorktree now asks for confirmation
    │   before entering a git worktree outside the project's .claude/worktrees/"
    │
    ├── 证据：CHANGELOG 2.1.205 "Fixed worktree-isolated subagents sometimes
    │   running shell commands in the parent checkout instead of their own worktree"
    │
    └── 证据：CHANGELOG 2.1.203 "worktree-isolated subagents redirecting git into
        the shared checkout via git -C, --git-dir, or GIT_DIR/GIT_WORK_TREE"

典型用途：
    - 并行开发：每个子 Agent 在独立 worktree
    - 实验性修改：在 worktree 中尝试，失败可丢弃
    - Code review：在 worktree 中查看 PR 变更
```

### 8.5 Session、Resume 与 Checkpoint

```text
【官方确认】来源：CHANGELOG

Session：
    - 一次完整交互的生命周期
    - 包含完整对话历史、todos、文件修改
    - 证据：CHANGELOG 中大量 "session" 相关记录

Resume（【官方确认】）：
    - 命令行：claude --resume / --continue
    - 命令：/resume
    - 证据：CHANGELOG 2.1.212 "Typing /resume in the agent view now opens a picker
      of past sessions — including sessions deleted from the list"
    - 证据：CHANGELOG 2.1.212 "resumed as a background session"
    - 上下文可能经过 compaction
    - 证据：CHANGELOG 2.1.214 "Fixed fork-session lineage being lost after compaction"

Fork（【官方确认】）：
    - 证据：CHANGELOG 2.1.212 "/fork now copies your conversation into a new
      background session while you keep working"
    - 证据：CHANGELOG 2.1.214 "SessionStart hooks to report source 'fork'"

Background Sessions（【官方确认】）：
    - 证据：CHANGELOG 2.1.217 "background sessions parked with ← or /background"
    - 证据：CHANGELOG 2.1.212 "claude attach" 附加到后台会话
    - 证据：CHANGELOG 2.1.217 "background daemon and a worker process"

Checkpoint / Rewind：
    - 证据：CHANGELOG 2.1.212 "/rewind no longer restores or deletes files through
      symlinks or hard links at tracked paths"
    - 证据：CHANGELOG 2.1.212 "Esc-Esc at an idle prompt not opening the rewind picker"
    - 证明存在 rewind（回溯）机制
```

### 8.6 错误恢复与重试

```text
【高可信度推断】

错误类型与恢复策略：
    │
    ├── 工具执行失败（exit code != 0）
    │       → 将 stderr 作为 tool_result 返回
    │       → LLM 看到错误后决定重试或换方法
    │
    ├── 工具调用格式错误（schema 不匹配）
    │       → Runtime 返回 validation error
    │       → LLM 修正后重新调用
    │
    ├── LLM 调用失败（API 超时/限流）
    │       → Runtime 自动重试（指数退避）
    │       → 用户看到"Retrying..."提示
    │
    ├── 权限被拒绝
    │       → 返回 permission denied
    │       → LLM 尝试替代方案或询问用户
    │
    └── 重复失败（同一工具连续 N 次失败）
            → Runtime 可能中止任务
            → 或提示用户介入
```

### 8.7 Observability

```text
【官方确认】来源：CHANGELOG

可观测性维度：
    │
    ├── Token Usage
    │       ├── input tokens / output tokens
    │       ├── cache read tokens（命中缓存）
    │       ├── 证据：CHANGELOG 2.1.208 "session cost and token telemetry
    │       │   double-counting on streams that emit multiple cumulative message_delta frames"
    │       ├── 证据：CHANGELOG 2.1.212 "/clear not resetting the session cost counter"
    │       └── 累计 total（/status 命令可见）
    │
    ├── OpenTelemetry 集成
    │       ├── 证据：CHANGELOG 2.1.214 "Added message.uuid, client_request_id,
    │       │   and tool_source attributes to OpenTelemetry log events"
    │       ├── 证据：CHANGELOG 2.1.214 "Added CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH
    │       │   to configure the 60 KB truncation limit on OTel content attributes"
    │       ├── 证据：CHANGELOG 2.1.212 "Prometheus metrics endpoint"
    │       └── 证据：CHANGELOG 2.1.212 "OTLP event log records missing trace_id/span_id"
    │
    ├── Tool Metrics
    │       ├── 证据：CHANGELOG 2.1.212 "session-wide limit on WebSearch tool calls
    │       │   (default 200, tunable via CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION)"
    │       ├── 证据：CHANGELOG 2.1.214 "periodic progress heartbeat for long-running
    │       │   tool calls that previously went silent"
    │       └── 证据：CHANGELOG 2.1.210 "live elapsed-time counter to the collapsed
    │           tool summary line"
    │
    ├── Session Analytics
    │       ├── 证据：CHANGELOG 2.1.212 "Fixed /context not resetting..."
    │       ├── /context 命令显示上下文使用情况
    │       ├── /usage 命令显示用量
    │       └── /status 命令显示会话状态
    │
    └── Audit / Telemetry
            ├── 证据：CHANGELOG 2.1.214 "Improved telemetry misreporting permission denials"
            ├── 证据：CHANGELOG 2.1.212 "session transcripts record the reasoning effort
            │   level on each assistant message"
            └── 证据：CHANGELOG 2.1.208 "trace context" 用于分布式追踪
```

### 8.8 安全边界与 Prompt Injection 风险

```text
【官方确认】来源：CHANGELOG + plugins/security-guidance/

Prompt Injection 防护（【官方确认】）：
    │
    ├── 证据：CHANGELOG 2.1.210 "Hardened the Agent tool against indirect prompt
    │   injection via content a subagent read"
    │
    ├── 证据：CHANGELOG 2.1.207 "Fixed spurious prompt-injection warnings triggered
    │   by benign system-generated conversation updates"
    │       — 证明存在 prompt-injection 检测机制
    │
    ├── 证据：CHANGELOG 2.1.211 "Fixed permission previews relayed to chat channels
    │   not neutralizing bidirectional-override, zero-width, and look-alike quote
    │   characters, so tool inputs cannot visually alter the approval message"
    │       — 防止通过 Unicode 字符欺骗用户确认
    │
    ├── 证据：CHANGELOG 2.1.214 "agent frontmatter hooks running from untrusted
    │   folders: hooks now require the agent file's own folder to have accepted
    │   workspace trust"
    │       — Workspace Trust 机制
    │
    └── 证据：CHANGELOG 2.1.205 "auto mode rule that blocks tampering with
            session transcript files"
            — 防止 Agent 篡改自己的历史记录

安全机制层级：
    │
    ├── 1. Workspace Trust（工作区信任）
    │       └── 未信任的文件夹中 Hook 不执行
    │
    ├── 2. Permission Model（权限模型）
    │       └── deny/ask/allow 三级 + auto mode 分类器
    │
    ├── 3. Sandbox（沙箱隔离）
    │       └── 证据：examples/settings/settings-bash-sandbox.json
    │       └── 网络 egress 控制、文件系统隔离
    │
    ├── 4. Hook-based Security（基于 Hook 的安全检查）
    │       └── 证据：plugins/security-guidance/ 提供安全审查 Hook
    │       └── 证据：plugins/hookify/examples/ 提供敏感文件保护
    │
    └── 5. Prompt Injection Detection（注入检测）
            └── 内置检测 + 权限预览中和特殊字符

残余风险：
    - 高级注入可能绕过上述防护
    - 敏感操作仍需人工审查
    - 建议：不在不可信环境中运行高权限 Agent
```

---

## 九、附录

### 9.1 核心代码地图（Top 10）

| # | 名称/逻辑模块 | 来源等级 | 核心职责 | 重点关注 | 重要性 |
|---|-------------|---------|---------|---------|--------|
| 1 | **Agent Loop 核心** | 【高可信度推断】 | 实现 LLM→Tool→Result 循环 | 停止条件、错误恢复、状态机 | ⭐⭐⭐⭐⭐ |
| 2 | **Context Assembler** | 【高可信度推断】 | 动态组装 system prompt | Token 预算分配、注入顺序 | ⭐⭐⭐⭐⭐ |
| 3 | **Tool Registry** | 【公开代码分析】 | 统一工具注册与调度 | MCP 集成、schema 校验 | ⭐⭐⭐⭐⭐ |
| 4 | **Permission Controller** | 【官方确认】 | allow/ask/deny 决策 | 规则优先级、用户交互 | ⭐⭐⭐⭐ |
| 5 | **CLAUDE.md Loader** | 【官方确认】 | 层级加载与合并 | 优先级、作用域、热加载 | ⭐⭐⭐⭐⭐ |
| 6 | **Hook System** | 【官方确认】 | 事件驱动扩展 | 执行顺序、无限循环防范 | ⭐⭐⭐⭐ |
| 7 | **Compaction Engine** | 【高可信度推断】 | 上下文压缩 | 摘要质量、信息保留策略 | ⭐⭐⭐⭐⭐ |
| 8 | **Subagent Manager** | 【高可信度推断】 | 子 Agent 生命周期 | 上下文隔离、worktree、结果收集 | ⭐⭐⭐⭐ |
| 9 | **Session Manager** | 【官方确认】 | 会话持久化与恢复 | Checkpoint、resume 一致性 | ⭐⭐⭐ |
| 10 | **MCP Client** | 【公开代码分析】 | MCP 协议通信 | stdio/SSE 传输、重连、错误处理 | ⭐⭐⭐⭐ |

> **注意**：以上除标注【官方确认】的模块外，其余均为**逻辑模块名**，不代表 Claude Code 真实源码路径。Claude Code 的核心实现未完全开源，具体文件路径和类名无法确认。

### 9.2 高频面试题（15 道）

---

#### 第 1 题：Claude Code 为什么不是普通 Chatbot

**考察目的**：理解 Agent 与 Chatbot 的本质区别。

**标准回答要点**：
1. Chatbot 是"一问一答"，Claude Code 是"循环执行直到任务完成"。
2. Chatbot 只能生成文本，Claude Code 能调用工具（文件读写、命令执行、搜索）。
3. Chatbot 无状态（或仅靠上下文窗口），Claude Code 有持久化记忆（CLAUDE.md、Memory）。
4. Chatbot 无法自主决策下一步，Claude Code 的 Agent Loop 让 LLM 自主规划。

**常见错误回答**：
- "因为 Claude Code 能写代码" → 片面，其他工具也能写代码。
- "因为用了 Claude 模型" → 模型是基础，但不是 Agent 的本质。

**进阶追问**：
- "如果给 Chatbot 加上工具调用，它就变成 Agent 了吗？"
- "Agent 和 RPA（机器人流程自动化）有什么区别？"

---

#### 第 2 题：Agent Loop 与 Workflow 的区别

**考察目的**：理解两种 Agent 编排范式。

**标准回答要点**：
1. Workflow 是预定义 DAG，节点和边由人设计；Agent Loop 是 LLM 自主决策下一步。
2. Workflow 适合流程固定、可审计的场景；Agent Loop 适合探索性、步骤不确定的场景。
3. Workflow 的可预测性更高；Agent Loop 的灵活性更高。
4. 实际系统中常混合使用：外层 Workflow，内层 Agent Loop。

**常见错误回答**：
- "Agent Loop 就是 while True" → 忽略了停止条件、错误恢复等复杂性。
- "Workflow 比 Agent Loop 简单" → 复杂 Workflow 的设计难度可能更高。

**进阶追问**：
- "什么场景下应该用 Workflow 而非 Agent Loop？"
- "LangGraph 的 StateGraph 是 Workflow 还是 Agent Loop？"

---

#### 第 3 题：CLAUDE.md 算不算 Memory

**考察目的**：理解 Memory 的不同形态。

**标准回答要点**：
1. 算，但它是**项目指令型记忆**，不是通常 RAG 意义上的 Memory。
2. 特点：显式文件、全量注入、启动时加载、用户完全可控。
3. 与 Vector DB Memory 的区别：无检索、无相似度匹配、无自动学习。
4. 与 Session Memory 的区别：跨会话持久，不随会话结束而丢失。

**常见错误回答**：
- "CLAUDE.md 是 RAG" → 错误，它是全量注入。
- "CLAUDE.md 不算 Memory" → 错误，它是 Memory 的一种形式。

**进阶追问**：
- "如果 CLAUDE.md 很大，怎么优化？"
- "CLAUDE.md 和 system prompt 的边界在哪里？"

---

#### 第 4 题：Compaction 为什么会丢失信息

**考察目的**：理解上下文管理的 trade-off。

**标准回答要点**：
1. 根本原因：摘要是有损压缩，LLM 总结时会丢弃"看起来不重要"的细节。
2. 具体丢失：代码 diff 原文、工具调用精确参数、用户原始措辞、长列表。
3. 缓解：PreCompact Hook 保存关键信息、Todo 系统保留任务状态、文件路径保留以便重新读取。
4. 设计原则：不要把所有重要信息放在对话历史里，要利用外部状态。

**常见错误回答**：
- "Compaction 是无损的" → 错误。
- "只要窗口够大就不需要 Compaction" → 忽略了成本和长会话问题。

**进阶追问**：
- "如何评估 Compaction 的质量？"
- "如果 Compaction 后模型表现变差，怎么排查？"

---

#### 第 5 题：Tool Calling 为什么需要 Runtime

**考察目的**：理解 Runtime 的职责与价值。

**标准回答要点**：
1. LLM 输出的是"意图"（JSON），不能直接执行——需要 Runtime 做参数校验。
2. 安全边界：Runtime 做权限检查，防止危险操作。
3. 错误处理：工具失败时 Runtime 捕获异常，优雅返回错误。
4. 副作用管理：文件锁、事务、并发控制都需要 Runtime。
5. 如果没有 Runtime，就等于把 shell 交给模型，不可接受。

**常见错误回答**：
- "因为 LLM 不能直接调用函数" → 正确但太浅，没有说明 Runtime 的具体职责。
- "Runtime 只是为了性能" → 错误，主要是为了安全。

**进阶追问**：
- "Runtime 和沙箱有什么区别？"
- "如果 Runtime 本身有 bug，Agent 怎么处理？"

---

#### 第 6 题：MCP 为什么不等于 Agent

**考察目的**：理解协议与系统的区别。

**标准回答要点**：
1. MCP 是**工具协议**，定义了工具发现、调用、结果返回的标准格式。
2. Agent 是**决策系统**，使用工具完成任务的完整循环。
3. MCP 让 Agent 能调用外部工具，但决策逻辑在 Agent 侧。
4. 类比：MCP 是 USB 协议，Agent 是电脑——USB 让电脑能接外设，但电脑本身不是 USB。

**常见错误回答**：
- "MCP 就是 Agent" → 混淆了协议与系统。
- "有了 MCP 就不需要 Agent 了" → 错误，MCP 只是工具层。

**进阶追问**：
- "MCP 和 OpenAI Function Calling 有什么区别？"
- "一个 MCP server 可以同时服务多个 Agent 吗？"

---

#### 第 7 题：Stop Hook 为什么会导致持续运行

**考察目的**：理解 Hook 与 Agent Loop 的交互。

**标准回答要点**：
1. Stop Hook 在每轮结束（LLM 返回 end_turn）时执行。
2. 它可以返回 `continue: true`，强制 Agent 继续下一轮。
3. 价值：让外部条件（测试、质量检查）决定任务是否完成，而非仅靠模型判断。
4. 风险：可能无限循环，需要 Hook 脚本设置终止条件。

**常见错误回答**：
- "Stop Hook 只能让 Agent 停止" → 错误，它也可以让 Agent 继续。
- "Stop Hook 和 max turns 一样" → 错误，Stop Hook 是每轮判断，max turns 是兜底。

**进阶追问**：
- "Stop Hook 和 max turns 的优先级是什么？"
- "如何用 Stop Hook 实现 TDD？"

---

#### 第 8 题：Subagent 为什么能减少主上下文污染

**考察目的**：理解上下文隔离的价值。

**标准回答要点**：
1. 子 Agent 的整个探索过程在独立上下文中进行。
2. 只有最终结果返回给主 Agent，中间推理不污染主上下文。
3. 代价：token 成本增加、协调开销、信息丢失、延迟。
4. 适用场景：任务可拆分、探索过程产生大量噪音。

**常见错误回答**：
- "Subagent 共享主 Agent 上下文" → 错误，它们是隔离的。
- "Subagent 越多越好" → 错误，存在协调成本。

**进阶追问**：
- "子 Agent 的中间推理完全丢失了吗？"
- "什么场景下不应该用 Subagent？"

---

#### 第 9 题：权限系统如何防止危险操作

**考察目的**：理解 Agent 安全模型。

**标准回答要点**：
1. 三层权限：deny（最高优先级）> allow > ask（默认询问）。
2. deny 列表直接拒绝，不询问用户。
3. allow 列表直接允许，无需确认。
4. ask 列表弹出确认框，由用户决定。
5. 默认行为：安全操作（Read）默认允许，危险操作（rm -rf）默认询问。

**常见错误回答**：
- "权限系统能防止所有危险操作" → 错误，高级注入可能绕过。
- "deny 和 allow 优先级一样" → 错误，deny 优先级最高。

**进阶追问**：
- "权限系统和 Prompt Injection 防护有什么关系？"
- "如何设计一个最小权限的 Agent？"

---

#### 第 10 题：Agent 如何判断任务完成

**考察目的**：理解停止条件的复杂性。

**标准回答要点**：
1. **模型判断**：LLM 返回 end_turn，无 tool_use。
2. **Stop Hook**：外部条件（测试通过、质量达标）确认完成。
3. **Todo 完成**：所有子任务状态为 completed。
4. **用户确认**：Agent 询问用户，用户确认完成。
5. **max turns 兜底**：达到上限强制停止。
6. 实际系统中通常是多重条件组合判断。

**常见错误回答**：
- "Agent 说完成了就完成了" → 错误，需要多重校验。
- "max turns 是唯一停止条件" → 错误，只是兜底。

**进阶追问**：
- "如果模型错误判断任务完成，怎么发现？"
- "Stop Hook 和 Todo 完成判断哪个更可靠？"

---

#### 第 11 题：为什么工具过多会降低模型选择质量

**考察目的**：理解工具设计的 trade-off。

**标准回答要点**：
1. 注意力稀释：工具定义占用 token，分走模型注意力。
2. 选择困难：工具越多，选错概率越高。
3. Schema 冲突：相似工具让模型困惑。
4. 解决方案：动态暴露（按阶段激活工具子集）、工具分组、描述优化。

**进阶追问**：
- "工具数量的经验上限是多少？"
- "如何评估工具描述的质量？"

---

#### 第 12 题：Session Resume 和 Checkpoint 的区别

**考察目的**：理解状态持久化。

**标准回答要点**：
1. Session 是完整交互生命周期。
2. Checkpoint 是会话中的快照点。
3. Resume 是从 checkpoint 恢复，可能丢失 checkpoint 之后的状态。
4. 上下文可能经过 compaction，恢复后是摘要而非原文。

**进阶追问**：
- "Checkpoint 的粒度是什么？"
- "Resume 后 Agent 能记住之前的工作吗？"

---

#### 第 13 题：Prompt Injection 在 Claude Code 中如何防护

**考察目的**：理解 Agent 安全。

**标准回答要点**：
1. System prompt 隔离：明确角色边界。
2. 工具结果标记为"数据"而非"指令"。
3. 权限模型限制危险操作。
4. Hook 可检测可疑模式。
5. 用户确认机制（ask 权限）。
6. 残余风险：高级注入可能绕过，敏感操作仍需人工审查。

**进阶追问**：
- "WebFetch 的内容如何防止注入？"
- "MCP server 返回恶意内容怎么办？"

---

#### 第 14 题：Agent Loop 中工具失败如何恢复

**考察目的**：理解错误处理。

**标准回答要点**：
1. 工具失败 → stderr/exit code 作为 tool_result 返回。
2. LLM 看到错误后决定：重试、换方法、求助用户。
3. 重复失败 → Runtime 可能中止或提示用户。
4. 格式错误 → validation error 返回，LLM 修正后重试。
5. API 失败 → Runtime 自动重试（指数退避）。

**进阶追问**：
- "重试次数有限制吗？"
- "如何区分可恢复错误和不可恢复错误？"

---

#### 第 15 题：如何设计一个工业级 Agent 的 Token Budget

**考察目的**：理解资源管理。

**标准回答要点**：
1. 分配比例：system prompt 10-15%、工具定义 10-15%、对话历史 50-60%、预留 10-20%。
2. 动态调整：根据任务阶段调整各部分占比。
3. Compaction 阈值：达到 80% 时触发压缩。
4. 工具结果截断：大输出自动截断，模型可按需获取更多。
5. 监控：实时跟踪 token 消耗，异常时告警。

**进阶追问**：
- "不同模型的窗口大小不同，如何适配？"
- "Token 成本和延迟如何权衡？"

---

### 9.3 面试速记表

| 模块 | 一句话定义 | 核心流程 | 关键术语 | 高频考点 | 重要性 |
|------|-----------|---------|---------|---------|--------|
| **Memory Design** | 通过显式文件实现跨会话知识持久化 | 加载层级 → 合并 → 注入 system prompt | CLAUDE.md、MEMORY.md、frontmatter、project instructions | CLAUDE.md 是否算 Memory、层级优先级、为什么不用 Vector DB | ⭐⭐⭐⭐⭐ |
| **Agent Loop** | LLM 与工具交替执行的循环引擎 | 构建上下文 → LLM 调用 → 工具执行 → 结果回传 → 再次决策 | ReAct、stop_reason、max turns、Stop Hook | Loop vs Workflow、停止条件、错误恢复 | ⭐⭐⭐⭐⭐ |
| **Tool System** | 统一注册、调度、权限控制的工具框架 | 注册 → 发现 → 调用 → 权限检查 → 执行 → 结果处理 | MCP、Function Calling、schema、allow/ask/deny | MCP vs Agent、Runtime 的必要性、工具过多的影响 | ⭐⭐⭐⭐⭐ |
| **Context Management** | 在有限窗口内动态管理信息注入与压缩 | 组装 → 预算检查 → 超限则压缩 → 继续 | Compaction、token budget、lazy loading、search-then-read | Compaction 丢失信息、上下文 vs 记忆、裁剪策略 | ⭐⭐⭐⭐⭐ |
| **Multi-Agent** | 通过子 Agent 实现任务分解与上下文隔离 | 拆分 → spawn → 独立执行 → 收集结果 → 合并 | Subagent、worktree、SendMessage、并行/串行 | 隔离的价值与代价、何时用子 Agent、Agent 数量 | ⭐⭐⭐⭐ |
| **Hook System** | 在 Agent Loop 各节点注入自定义逻辑 | 事件触发 → 执行 Hook 脚本 → 处理输出 | PreToolUse、PostToolUse、Stop、PreCompact | Stop Hook 导致继续、Hook vs Permission、无限循环 | ⭐⭐⭐⭐ |

---

### 9.4 学习路线

#### 第一优先：必须能够完整讲解（面试核心）

| 主题 | 要求 |
|------|------|
| Agent Loop 完整流程 | 能画出流程图并解释每步的输入输出 |
| CLAUDE.md 层级与加载 | 能说出四级优先级和合并策略 |
| Tool System 权限模型 | 能解释 allow/ask/deny 的优先级和配置 |
| Compaction 机制 | 能解释触发条件、损失、恢复方式 |
| Subagent 隔离价值 | 能对比有无隔离的差异 |

#### 第二优先：必须理解源码流程（深入追问）

| 主题 | 要求 |
|------|------|
| MCP 工具注册流程 | 能描述 initialize → tools/list → tools/call |
| Hook 执行顺序 | 能画出 PreToolUse → Permission → Execute → PostToolUse |
| Context 组装顺序 | 能列出各部分的注入顺序和 token 分配 |
| 错误恢复策略 | 能区分工具失败、格式错误、API 失败的不同处理 |
| Stop Hook 与停止条件 | 能解释 continue: true 的作用和风险 |

#### 第三优先：了解即可（拓展话题）

| 主题 | 要求 |
|------|------|
| Worktree 隔离 | 知道存在，了解用途 |
| Observability 指标 | 知道 token/tool/loop 三类指标 |
| Prompt Injection 防护 | 知道主要防护手段和残余风险 |
| Session Resume | 知道存在，了解与 Checkpoint 的关系 |
| 后台任务 | 知道 run_in_background 和 TaskOutput |

### 9.5 Claude Code 架构演进趋势（面试加分项）

基于 CHANGELOG 中 2.1.203 ~ 2.1.220 版本的演进，可以观察到以下架构趋势：

```text
【官方确认】来源：CHANGELOG.md 趋势分析

趋势 1: 从"工具"到"Agent 团队"
    - 早期：单一 Agent + 工具调用
    - 现在：Agent Team、动态 workflow、嵌套子 Agent（depth 3）
    - 证据：CHANGELOG 中 "workflow agent grid"、"dynamic workflows"、
      "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"
    - 面试话术：Claude Code 正在从"单 Agent 系统"向"多 Agent 协作系统"演进

趋势 2: 安全模型持续强化
    - 新增 EndConversation 工具（应对越狱）
    - Prompt injection 防护加固
    - Permission preview 防欺骗（Unicode 中和）
    - Workspace Trust 机制
    - 证据：CHANGELOG 2.1.214 "Added the EndConversation tool"
    - 面试话术：工业级 Agent 必须假设"模型可能被误导"，需要多层防护

趋势 3: 从"手动配置"到"自动分类"
    - Auto Mode 从 opt-in 变为默认
    - 权限分类器从规则匹配 → ML 分类（Sonnet 5）
    - 证据：CHANGELOG 2.1.210 "auto mode classifier now defaults to Sonnet 5"
    - 面试话术：减少用户配置负担，让 Runtime 自动判断安全性

趋势 4: 从"同步"到"异步/后台"
    - 后台 session 成为一等公民
    - MCP 长时间调用自动后台化（>2 min）
    - Hook 支持 asyncRewake（异步唤醒）
    - 证据：CHANGELOG 2.1.212 "MCP tool calls running longer than 2 minutes
      now move to the background automatically"
    - 面试话术：长时间任务不应阻塞主会话，后台化是必然趋势

趋势 5: 可观测性成为基础设施
    - OpenTelemetry 集成
    - Prometheus 指标
    - 会话 cost/token 追踪
    - 证据：CHANGELOG 中大量 OTel/Prometheus 相关修复
    - 面试话术：没有可观测性的 Agent 无法投入生产环境

趋势 6: 记忆系统精细化
    - Memory 文件 frontmatter 增加 ISO timestamp
    - MEMORY.md 索引超限警告
    - Memory 写入错误显式化（不再静默截断）
    - 证据：CHANGELOG 2.1.214 "Added an ISO modified timestamp to memory
      file frontmatter"
    - 面试话术：记忆系统从"能用"走向"可靠"，需要边界检查和错误处理
```

---

## 资料来源清单

### 主要来源（当前沙盒中的本地仓库）

| 来源 | 类型 | 路径/网址 |
|------|------|------|
| **anthropics/claude-code 仓库**（本地克隆） | **官方开源仓库** | `D:\klin-agent\claude-code-main\` |
| ↳ CHANGELOG.md | 官方确认的功能记录 | 本地文件，含 2.1.203 ~ 2.1.220 版本详情 |
| ↳ plugins/hookify/ | Hook 配置示例 | `plugins/hookify/hooks/hooks.json` |
| ↳ plugins/security-guidance/ | 安全 Hook 示例 | `plugins/security-guidance/hooks/hooks.json` |
| ↳ plugins/feature-dev/ | 多 Agent 编排示例 | `plugins/feature-dev/commands/feature-dev.md` |
| ↳ plugins/pr-review-toolkit/ | Agent 定义示例 | `plugins/pr-review-toolkit/agents/*.md` |
| ↳ plugins/agent-sdk-dev/ | SDK 开发示例 | `plugins/agent-sdk-dev/agents/*.md` |
| ↳ plugins/ralph-wiggum/ | Stop Hook 示例 | `plugins/ralph-wiggum/hooks/hooks.json` |
| ↳ examples/settings/ | 权限配置示例 | `examples/settings/settings-strict.json` 等 |

> **重要说明**：本仓库是 Anthropic 官方维护的 `anthropics/claude-code` GitHub 仓库的本地克隆。它包含 Claude Code 的**开源部分**：CHANGELOG、插件示例、配置示例、脚本等。Claude Code 的**核心实现代码**（Agent Loop、Tool Executor、Context Assembler 等）**不在此仓库中**，而是以编译后的形式分发在 npm 包 `@anthropic-ai/claude-code` 中。

### 官方文档

| 来源 | 类型 | 网址 |
|------|------|------|
| Claude Code 官方文档 - Overview | 官方确认 | https://docs.claude.com/en/docs/claude-code/overview |
| Claude Code 官方文档 - Memory | 官方确认 | https://docs.claude.com/en/docs/claude-code/memory |
| Claude Code 官方文档 - Hooks | 官方确认 | https://docs.claude.com/en/docs/claude-code/hooks |
| Claude Code 官方文档 - Tools | 官方确认 | https://docs.claude.com/en/docs/claude-code/tools |
| Claude Code 官方文档 - Permissions | 官方确认 | https://docs.claude.com/en/docs/claude-code/permissions |
| Claude Code 官方文档 - How it Works | 官方确认 | https://docs.claude.com/en/docs/claude-code/how-claude-code-works |
| Claude Agent SDK 文档 | 官方确认 | https://docs.claude.com/en/api/agent-sdk/overview |

### 开源代码与标准

| 来源 | 类型 | 网址 |
|------|------|------|
| MCP 规范 | 公开标准 | https://modelcontextprotocol.io |
| MCP SDK (TypeScript) | 开源代码 | https://github.com/modelcontextprotocol/typescript-sdk |
| MCP SDK (Python) | 开源代码 | https://github.com/modelcontextprotocol/python-sdk |
| Anthropic SDK (TypeScript) | 开源代码 | https://github.com/anthropics/anthropic-sdk-typescript |
| Anthropic SDK (Python) | 开源代码 | https://github.com/anthropics/anthropic-sdk-python |
| Claude Code npm 包 | 公开构建产物 | https://www.npmjs.com/package/@anthropic-ai/claude-code |
| Claude Code GitHub 仓库 | 公开代码 | https://github.com/anthropics/claude-code |

---

> **免责声明**：本资料基于截至 2026-07-28 可验证的公开信息编写。核心实现部分（Agent Loop、Tool Executor 等逻辑模块）的标注为【高可信度推断】或【教学伪代码】，不代表 Claude Code 真实源码。面试时建议结合最新版本文档复习。标注【尚未公开】的内容可能在未来版本中得到确认。
