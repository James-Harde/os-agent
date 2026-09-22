"""知识库文档导入闭环 — 上传 → 解析 → 切分 → 向量化 → Milvus 入库 → 可检索引用。

模块：
  - store   : SQLite 文档元数据 + 任务状态清单（DocumentMetadataStore）
  - security: 上传安全边界（扩展名 / MIME / 大小 / 路径穿越 / 安全存储名）
  - parsing : 官方 LangChain Loader 选择 + 元数据增强
  - service : 导入任务编排（异步解析 / 入库 / 查询 / 删除）
  - router  : FastAPI 路由（/api/knowledge/...）
  - models  : 请求/响应模型

设计要点：
  - 用户上传文件存在受控目录 ``app_v4/data/uploads/``，绝不信任客户端文件名或路径。
  - 解析 / Embedding / Milvus 写入在后台任务中通过线程池执行，不阻塞 FastAPI 事件循环。
  - 任务状态（queued / parsing / embedding / indexed / failed）可查询，失败必须可见。
  - 只有 Milvus 写入成功后才标记为 indexed；"上传成功"不等于"已可检索"。
  - 删除按 document_id 精确过滤，不清空整个集合。
  - 文档二进制 / 完整文本 / 密钥不写入 Trace/Audit。
"""

from app_v4.knowledge.service import KnowledgeService

__all__ = ["KnowledgeService"]
