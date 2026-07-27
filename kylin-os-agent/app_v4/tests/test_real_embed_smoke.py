"""真实 Embedding API smoke test — 独立 marker，不纳入默认离线套件。

运行方式：
    pytest -m real_embedding -v -s

默认 `pytest` 运行会跳过本文件所有测试（通过 pytest.ini 的 addopts 排除）。
仅当 EMBEDDING_* 三项全部配置时才真正执行；否则明确 skip，不使用 Fake 或 SVD 冒充成功。

安全：日志只记录 provider / base_url / model、耗时、向量维度、错误类型，绝不输出 api_key。
"""

import time

import pytest

from app_v4.rag.real_embed import OpenAICompatibleEmbedder

# 整个文件标记为 real_embedding，默认离线套件排除
pytestmark = pytest.mark.real_embedding


def _embedding_configured() -> bool:
    """读取根目录 .env 的真实配置（生产路径），判断 Embedding 是否完整配置。"""
    from app_v4.settings import load_settings

    s = load_settings()
    return s.embedding_configured


@pytest.fixture(scope="module")
def real_embedder() -> OpenAICompatibleEmbedder:
    """构造真实 Embedding 客户端（读取根目录 .env）。未配置则 skip。"""
    from app_v4.settings import load_settings

    s = load_settings()
    if not s.embedding_configured:
        pytest.skip(
            "EMBEDDING_* 未完整配置（需要 BASE_URL / API_KEY / MODEL），"
            "跳过真实 embedding smoke"
        )
    return OpenAICompatibleEmbedder(
        base_url=s.embedding_base_url,
        api_key=s.embedding_api_key,
        model=s.embedding_model,
        dimensions=s.embedding_dimensions,
        timeout=s.embedding_timeout,
    )


def test_real_embedding_smoke_basic(real_embedder, capsys):
    """真实 Embedding API 基本调用：返回向量、维度合理、耗时可测。"""
    start = time.perf_counter()
    try:
        vec = real_embedder.encode("磁盘使用率怎么查")
    except Exception as exc:
        elapsed = time.perf_counter() - start
        # 只记录错误类型，绝不输出 api_key
        pytest.fail(
            f"真实 embedding 调用失败: provider=OpenAI-compatible, "
            f"model={real_embedder.model}, elapsed={elapsed:.2f}s, "
            f"error_type={type(exc).__name__}"
        )
    elapsed = time.perf_counter() - start

    dim = len(vec)
    assert dim > 0, "embedding 向量维度应 > 0"
    # 常见维度范围（384~4096），超出则提示检查
    assert 64 <= dim <= 8192, f"embedding 维度异常: {dim}"

    # 记录耗时与维度（不输出 api_key）
    with capsys.disabled():
        print(
            f"\n[real_embedding_smoke] OK model={real_embedder.model} "
            f"dim={dim} elapsed={elapsed:.2f}s"
        )


def test_real_embedding_smoke_batch_and_deterministic(real_embedder):
    """真实 Embedding：批量编码维度一致，相同输入多次编码结果稳定（误差 < 1e-3）。"""
    texts = ["磁盘使用率", "进程 CPU 占用", "端口监听查询"]

    vecs = real_embedder.encode_batch(texts)
    assert vecs.shape[0] == len(texts), "批量编码返回向量数应等于输入数"
    assert vecs.shape[1] == real_embedder.encode(texts[0]).shape[0], "批量与单条维度应一致"

    # 相同输入再次编码，结果应高度一致（API 通常确定性或近似）
    vec_again = real_embedder.encode(texts[0])
    diff = float(abs(vecs[0] - vec_again).max())
    assert diff < 1e-3, f"相同输入两次编码差异过大: {diff}"
