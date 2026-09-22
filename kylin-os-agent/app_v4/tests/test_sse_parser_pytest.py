"""pytest 入口调度 Node 的 SSE parser 单元测试。

前端 ``sse-parser.js`` 是无依赖纯函数，可在 Node 中同源码运行。
本测试用 ``node --test`` 跑 ``test_sse_parser.test.js``，把 Node 测试结果
桥接为 pytest 用例，使 ``pytest app_v4/tests`` 成为统一测试入口。

若环境未安装 Node，测试会跳过而非失败。
"""

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PARSER_TEST_JS = os.path.join(HERE, "test_sse_parser.test.js")
STATIC_DIR = os.path.join(HERE, "..", "static")
PARSER_JS = os.path.join(STATIC_DIR, "sse-parser.js")


def _node_available():
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="Node.js 不可用，跳过前端 parser 测试")
def test_sse_parser_module_loads_in_node():
    """parser 模块应能被 Node 正确 require（UMD 导出）。"""
    res = subprocess.run(
        [shutil.which("node"), "-e",
         f"const m=require('{PARSER_JS.replace(os.sep, '/')}');"
         "if(typeof m.parseSSEChunk!=='function')throw new Error('missing parseSSEChunk');"
         "if(typeof m.parseSSEFlush!=='function')throw new Error('missing parseSSEFlush');"
         "console.log('OK')"],
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
    )
    assert res.returncode == 0, f"Node require 失败: {res.stderr}"
    assert "OK" in res.stdout


@pytest.mark.skipif(not _node_available(), reason="Node.js 不可用，跳过前端 parser 测试")
def test_sse_parser_node_unit_tests_pass():
    """调度 node --test 跑 parser 全部单元测试，任一失败则 pytest 失败。"""
    res = subprocess.run(
        [shutil.which("node"), "--test", PARSER_TEST_JS],
        capture_output=True, text=True, check=False,
        cwd=os.path.join(HERE, ".."),
        encoding="utf-8", errors="replace",
    )
    # node --test 输出到 stdout；把输出透传以便失败时诊断。
    assert res.returncode == 0, (
        f"Node SSE parser 测试失败 (exit={res.returncode}):\n"
        f"--- stdout ---\n{res.stdout}\n--- stderr ---\n{res.stderr}"
    )
