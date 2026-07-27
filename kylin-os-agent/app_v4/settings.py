"""集中配置 — pydantic-settings，支持 .env 加载。

设计：
  - 所有运行时配置集中在此，避免散落在各模块的 os.getenv。
  - 测试通过 Dependencies 注入覆盖，不读取真实 .env。
  - 严禁打印 secret（api_key 等）。
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app_v4/ → kylin-os-agent/），真实 .env 位于此处
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """应用配置。环境变量优先，支持 .env 文件。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "Kylin Secure OS Agent v4"
    app_version: str = "0.4.0"
    log_level: str = "INFO"

    # ---- Chat 模型（OpenAI 兼容） ----
    # 仅用于对话/规划，不得复用为 Embedding 配置。
    openai_compatible_base_url: str = ""
    openai_compatible_api_key: str = Field(default="", repr=False)
    openai_compatible_model: str = ""
    openai_compatible_timeout: float = 20.0

    # 测试/开发：使用确定性假模型（不调用外部 API）
    use_fake_model: bool = False

    # ---- Embedding 模型（独立显式配置） ----
    # 工业界做法：Chat 与 Embedding 通常是不同服务/模型，必须独立配置。
    # 禁止根据 Chat 的 base_url 或 model 猜测 Embedding 模型（曾导致 DeepSeek 404）。
    # 三项均非空才视为"已配置"；缺失时在 RAG 边界 fail-fast 或返回 unavailable。
    embedding_base_url: str = ""
    embedding_api_key: str = Field(default="", repr=False)
    embedding_model: str = ""
    embedding_dimensions: int | None = None   # 可选：仅部分模型支持（如 text-embedding-3-small）
    embedding_timeout: float = 30.0

    # ---- Milvus（向量检索） ----
    # 显式配置完整 Milvus URI（如 http://127.0.0.1:19530 指向 Docker Milvus Standalone）。
    # 空字符串 = 未配置 → 回退到嵌入式 Milvus Lite（仅本地开发/smoke test，数据临时）。
    # 生产必须显式配置，避免把临时 Lite 数据当成可靠存储。
    milvus_uri: str = ""
    milvus_collection: str = "rag_knowledge"
    milvus_timeout: float = 10.0

    # ---- 持久化 ----
    # 默认数据库路径；测试注入临时路径覆盖
    db_path: str = ""

    # ---- 限流 ----
    rate_limit_enabled: bool = True
    rate_limit_capacity: int = 10
    rate_limit_refill_rate: float = 1.0  # tokens/sec

    # ---- 预算 / 熔断 ----
    max_steps: int = 10
    max_tool_calls: int = 8
    max_duration_sec: int = 60
    max_same_plan: int = 2
    kill_switch: bool = False

    # ---- 只读 bounded ReAct ----
    max_readonly_iterations: int = 5   # 最大 ReAct 轮数
    max_readonly_tool_calls: int = 6   # 最大工具调用数
    max_no_progress_streak: int = 2    # 连续无进展轮数上限
    max_error_streak: int = 2          # 连续工具错误轮数上限

    # ---- 可变更工具执行（B6）----
    # 生产默认 False：未开启时 execute_mutation 返回 disabled，不调用 adapter，
    # 避免 recording adapter 冒充真实重启。开启需同时满足服务 allowlist。
    mutation_enabled: bool = False
    mutation_allowed_services: str = ""  # 逗号分隔 allowlist；空=全部允许

    # ---- MCP ----
    # 显式配置完整 MCP Server URL（如 http://127.0.0.1:8001/mcp）。
    # 空字符串 = 未配置 → build_dependencies 保持 LocalToolInvoker（避免测试访问网络）；
    # 非空 → 注入 MCPToolInvoker，生产流量走 streamable_http。
    mcp_server_url: str = ""
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8001

    @property
    def mutation_allowed_services_list(self) -> list[str]:
        """解析服务 allowlist（逗号分隔，去空去空白）。"""
        return [s.strip() for s in self.mutation_allowed_services.split(",") if s.strip()]

    @property
    def embedding_configured(self) -> bool:
        """Embedding 是否已完整配置（三项缺一不可）。

        注意：不作为启动硬性校验——RAG 是可选能力，应用可在无 Embedding
        配置下正常启动；缺失时在 RAG 边界 fail-fast 或返回 unavailable。
        """
        return bool(self.embedding_base_url and self.embedding_api_key and self.embedding_model)

    def resolved_db_path(self) -> Path:
        """返回绝对 DB 路径（未配置则用默认位置）。"""
        if self.db_path:
            return Path(self.db_path)
        return PROJECT_ROOT / "app_v4" / "data" / "agent_v4.db"

    def model_post_init(self, __context: object) -> None:
        """启动校验：非 fake 模型时必须配置 base URL / key / model。

        测试通过 use_fake_model=True 绕过；生产若三项缺失应在此清晰报错，
        避免把空凭据传到深层后随机失败。
        """
        if self.use_fake_model:
            return
        missing = [
            name for name, value in (
                ("OPENAI_COMPATIBLE_BASE_URL", self.openai_compatible_base_url),
                ("OPENAI_COMPATIBLE_API_KEY", self.openai_compatible_api_key),
                ("OPENAI_COMPATIBLE_MODEL", self.openai_compatible_model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "缺少模型配置：{}。请设置环境变量或写入项目根 .env 文件。"
                .format("、".join(missing))
            )


def _read_bool(key: str, default: str = "") -> bool:
    return os.getenv(key, default).lower() in ("1", "true", "yes")


def _read_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def load_settings() -> Settings:
    """从环境变量 / .env 加载配置。"""
    # 兼容旧环境变量名（平滑迁移）
    use_fake = _read_bool("APP_V4_USE_FAKE_MODEL")
    rate_disabled = _read_bool("APP_V4_DISABLE_RATE_LIMIT")
    kill = _read_bool("APP_V4_KILL_SWITCH")

    return Settings(
        use_fake_model=use_fake,
        rate_limit_enabled=not rate_disabled,
        max_steps=_read_int("APP_V4_MAX_STEPS", 10),
        max_tool_calls=_read_int("APP_V4_MAX_TOOL_CALLS", 8),
        max_duration_sec=_read_int("APP_V4_MAX_DURATION_SEC", 60),
        max_same_plan=_read_int("APP_V4_MAX_SAME_PLAN", 2),
        kill_switch=kill,
    )
