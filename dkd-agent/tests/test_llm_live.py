"""真实 LLM 连通性测试（任务 0-3 验收手段）。

默认**不执行**（pytest 配置里 `-m "not live"` 已排除）。
需显式开启并具备有效 Key：

    set -a; source .env; set +a            # 或 PowerShell 手动注入
    DKD_AGENT_LIVE_LLM=1 uv run pytest -m live -v

为什么默认关闭：AGENTS §8 明确要求「LLM 调用一律 Fake/打桩，测试不得真实调用外部 API」，
真实调用只允许作为人工触发的冒烟验证。
"""

import os

import pytest

from app.llm import ping_llm

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("DKD_AGENT_LIVE_LLM") != "1",
        reason="需设置 DKD_AGENT_LIVE_LLM=1 才执行真实 LLM 调用",
    ),
]


async def test_live_llm_returns_200_and_content():
    """方案 0-3 完成标准：一句话请求返回 HTTP 200 + 内容。"""
    result = await ping_llm()
    assert result.ok is True, "LLM 返回空内容"
    assert result.content.strip(), "LLM 返回内容为空"
    assert result.input_tokens > 0 and result.output_tokens > 0, "未取得 token 计量数据"
    print(
        f"[live] model={result.model} (configured={result.configured_model}) "
        f"latency={result.latency_ms}ms tokens={result.input_tokens}/{result.output_tokens}"
    )
