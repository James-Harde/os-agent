"""上传安全边界。

不信任客户端提供的任何内容：文件名、MIME、大小均由服务端二次校验。
职责：
  - 扩展名白名单（.pdf / .docx / .txt / .md）。
  - MIME 与扩展名一致（拒绝 MIME 与扩展名不匹配，防止伪装）。
  - 单文件大小、总存储上限。
  - 拒绝路径穿越、空文件、无法解析的文件。
  - 生成 UUID 作为安全存储名与 document_id，原文件名仅作展示元数据。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# 扩展名 → 期望 MIME（首值为最严格匹配）。
# 注意：MIME 由客户端提供、服务端不可全信；这里要求客户端 MIME 落在该扩展名
# 的常见集合内，作为一致性校验。无法识别的 MIME 不直接拒绝（浏览器/代理可能发
# 通用值），但扩展名必须是白名单。
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
EXT_TO_MIMES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "text/x-markdown", "application/octet-stream"},
}


class UploadRejectedError(ValueError):
    """上传被拒绝（安全问题或无效文件）。"""


@dataclass
class AcceptedUpload:
    """通过校验的上传。"""

    document_id: str          # UUID，主键
    original_filename: str    # 清洗后的原始文件名（仅展示，不参与路径）
    safe_filename: str        # 安全存储名（uuid + 小写扩展名）
    ext: str                  # 小写扩展名（带点）
    size: int                 # 字节
    sha256: str               # 内容摘要


def _sanitize_original_filename(name: str) -> str:
    """清洗原始文件名用于展示：去掉路径分量、控制长度、拒绝空名。

    绝不把客户端文件名用于存储路径。
    """
    # 仅取 basename，丢弃任何路径分量（防御路径穿越）。
    base = Path(name).name if name else ""
    base = base.strip()
    if not base:
        raise UploadRejectedError("文件名为空")
    # 去掉控制字符；限制长度。
    base = "".join(ch for ch in base if ch.isprintable())
    if len(base) > 200:
        base = base[:200]
    if not base or base in (".", ".."):
        raise UploadRejectedError("文件名无效")
    return base


def _ext_of(name: str) -> str:
    """取主扩展名（小写，带点）。"""
    return Path(name).suffix.lower()


def validate_and_accept(
    *,
    filename: str,
    declared_content_type: str | None,
    data: bytes,
    current_total_bytes: int,
    single_max_bytes: int,
    total_max_bytes: int,
) -> AcceptedUpload:
    """校验上传并返回接受的元数据；不通过则抛出 UploadRejectedError。

    校验顺序：
      1. 原始文件名清洗（拒绝路径穿越 / 空名）。
      2. 扩展名白名单。
      3. 非空。
      4. 单文件大小上限。
      5. 总存储上限。
      6. MIME 与扩展名一致性（仅当客户端提供了具体 MIME 时）。
    """
    original = _sanitize_original_filename(filename)
    ext = _ext_of(original)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejectedError(
            f"不支持的文件类型：{ext or '(无)'}。仅支持 {sorted(ALLOWED_EXTENSIONS)}"
        )

    if not data:
        raise UploadRejectedError("文件为空")

    size = len(data)
    if size > single_max_bytes:
        raise UploadRejectedError(
            f"文件过大：{size} 字节，超过单文件上限 {single_max_bytes} 字节"
        )
    if current_total_bytes + size > total_max_bytes:
        raise UploadRejectedError(
            f"存储空间不足：当前 {current_total_bytes} 字节，"
            f"本次 {size} 字节，总上限 {total_max_bytes} 字节"
        )

    # MIME 一致性校验：客户端提供了具体 MIME 时，要求与该扩展名常见集合匹配。
    declared = (declared_content_type or "").strip().lower()
    if declared and ";" in declared:
        declared = declared.split(";", 1)[0].strip()
    if declared and declared not in EXT_TO_MIMES.get(ext, set()):
        # 通用二进制流（application/octet-stream）在白名单扩展名下放行；
        # 其余不匹配视为伪装。
        if declared != "application/octet-stream":
            raise UploadRejectedError(
                f"MIME 与扩展名不一致：扩展名 {ext}，MIME {declared}"
            )

    document_id = str(uuid.uuid4())
    safe_filename = f"{document_id}{ext}"
    sha256 = hashlib.sha256(data).hexdigest()
    return AcceptedUpload(
        document_id=document_id,
        original_filename=original,
        safe_filename=safe_filename,
        ext=ext,
        size=size,
        sha256=sha256,
    )


def save_to_upload_dir(
    upload_dir: Path,
    safe_filename: str,
    data: bytes,
) -> Path:
    """把字节写入受控上传目录，返回最终路径。

    安全约束：
      - 存储名必须是纯文件名（无路径分量），由本模块生成，不信任外部输入。
      - 最终路径必须落在 upload_dir 内（防解析穿越）。
    """
    if "/" in safe_filename or "\\" in safe_filename or safe_filename != Path(safe_filename).name:
        raise UploadRejectedError("安全存储名非法")
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = (upload_dir / safe_filename).resolve()
    try:
        target.relative_to(upload_dir.resolve())
    except ValueError as exc:
        raise UploadRejectedError("存储路径越界") from exc
    target.write_bytes(data)
    return target
