"""FIX-17（密钥失效降级）+ FIX-11（真 LLM 校准 live）演练脚本。

## 两个相位对应两个不同的缺口，但走的是同一条代码路径

- `--phase invalid-key`（**FIX-17**）：真实调用 DeepSeek，但用**无效密钥** → 拿到真实的 401。
  验证的是「LLM 不可用时补货链路必须退化成可解释的规则增强版，而不是整个定时任务失败」。
  为什么必须真调而不是打桩：打桩测的是「我写的 except 分支对不对」，
  真调测的是「上游真实失败时**异常类型/时机**是不是我以为的那样」——FIX-19 那个
  JDK 401 误判就是这么被发现的（打桩永远不会暴露）。
- `--phase live`（**FIX-11**）：用**有效密钥**跑一次真实校准，验证「系数夹取 + 理由生成 +
  token 计量」在真实模型上成立（此前本机全程在 `DKD_AGENT_RESTOCK_CALIBRATION_ENABLED=0` 下验收，
  单测全打桩 → 真实链路一次都没跑过）。会产生**少量 token 费用**。

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a
python ../docs/scripts/verify-fix17-llm-degrade.py --phase invalid-key
python ../docs/scripts/verify-fix17-llm-degrade.py --phase live    # 会真实计费（很小）
```

⚠️ 脚本只打印**结构化指标**（model / token / 条数 / 异常类型），不打印 prompt 与响应体
（AGENTS §6.1：日志不得输出完整 LLM 请求体，可能含用户数据与密钥）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from app.config import Settings  # noqa: E402
from app.graphs.restock_baseline import BaselineSuggestion  # noqa: E402
from app.graphs.restock_calibration import calibrate_plan  # noqa: E402
from app.graphs.restock_state import RestockItem, RestockPlan  # noqa: E402

_pass = 0
_fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _plan() -> RestockPlan:
    """构造一份最小可校准计划（货道容量 10、现库存 1 → 基线建议 7）。"""
    return RestockPlan(
        plan_date="2026-10-01",
        vm_id=80,
        inner_code="A1000001",
        region_id=3,
        node_id=1,
        items=[
            RestockItem(
                sku_id=1,
                sku_name="可口可乐 330ml",
                channel_id=4732,
                channel_code="1-1",
                current_quantity=1,
                max_capacity=10,
                suggested_quantity=7,
                after_restock_quantity=8,
                estimated_days=2,
                priority=3,
                reason="基线依据：近 30 天加权分位日均 3.5 件/天",
            )
        ],
    )


def _suggestions() -> dict[str, BaselineSuggestion]:
    return {
        "1-1": BaselineSuggestion(
            channel_id=4732,
            channel_code="1-1",
            sku_id=1,
            sku_name="可口可乐 330ml",
            current_quantity=1,
            max_capacity=10,
            suggested_quantity=7,
            after_restock_quantity=8,
            daily_demand=3.5,
            estimated_days=2,
        )
    }


def _settings(*, api_key: str) -> Settings:
    """显式构造（不读 .env 的 key），并把校准开关打开。"""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DKD_AGENT_RESTOCK_CALIBRATION_ENABLED="1",
        DKD_AGENT_RESTOCK_CALIBRATION_MIN_FACTOR="0.7",
        DKD_AGENT_RESTOCK_CALIBRATION_MAX_FACTOR="1.5",
        DKD_DEEPSEEK_API_KEY=api_key,
        # 超时收紧到 20s：本脚本是人工演练，不该因为上游慢把终端挂住
        DKD_AGENT_LLM_TIMEOUT="20",
    )


# ----------------------------------------------------------------------------------
# 相位一：FIX-17 —— 密钥失效必须降级，而不是把整条链路带崩
# ----------------------------------------------------------------------------------


async def phase_invalid_key() -> None:
    print("\n[FIX-17] 用无效密钥真实调用 DeepSeek → 必须降级为统计基线")
    plan = _plan()
    baseline_items = list(plan.items)
    outcome = await calibrate_plan(
        plan,
        suggestions=_suggestions(),
        settings=_settings(api_key="sk-invalid-fix17-probe-never-valid"),
    )

    print(f"  → applied={outcome.applied} error={outcome.error} issues={outcome.issues}")
    print(f"  → tokens_in={outcome.tokens_in} tokens_out={outcome.tokens_out}")

    check("校准未生效（applied=False）——失败不伪装成成功", outcome.applied is False)
    check("记录了失败类型（error 非空，便于运维定位）", bool(outcome.error), str(outcome.error))
    check(
        "issues 里写明了“已降级为统计基线”",
        any("降级" in i for i in outcome.issues),
        f"issues={outcome.issues}",
    )
    check(
        "**统计基线结果被完整保留**（items 与降级前一致，没被清空/清零）",
        [i.suggested_quantity for i in outcome.items] == [i.suggested_quantity for i in baseline_items],
        f"建议量={[i.suggested_quantity for i in outcome.items]}（基线={[i.suggested_quantity for i in baseline_items]}）",
    )
    check(
        "建议量仍是 7（容量 10、现库存 1）——降级后仍是可解释的规则增强版",
        [i.suggested_quantity for i in outcome.items] == [7],
    )
    check("失败时不计 token（不该把失败调用算进成本）", outcome.tokens_in == 0 and outcome.tokens_out == 0)


# ----------------------------------------------------------------------------------
# 相位二：FIX-11 —— 真 LLM 校准 live 验证
# ----------------------------------------------------------------------------------


async def phase_live() -> None:
    key = (os.environ.get("DKD_DEEPSEEK_API_KEY") or "").strip()
    if not key:
        print("\n[FIX-11] ⛔ 缺少 DKD_DEEPSEEK_API_KEY（先 `set -a; source ../.env; set +a`）")
        raise SystemExit(1)

    print("\n[FIX-11] 用有效密钥跑一次真实校准（会产生少量 token 费用）")
    plan = _plan()
    outcome = await calibrate_plan(plan, suggestions=_suggestions(), settings=_settings(api_key=key))

    print(f"  → applied={outcome.applied} error={outcome.error} model={outcome.model}")
    print(f"  → tokens_in={outcome.tokens_in} tokens_out={outcome.tokens_out}")
    print(f"  → entries={[(e.channel_code, e.factor) for e in outcome.entries]}")
    print(f"  → issues={outcome.issues}")
    print(f"  → 校准后建议量={[i.suggested_quantity for i in outcome.items]}")

    check("真实链路未降级（error 为空、applied=True）", outcome.applied is True and not outcome.error)
    check(
        "服务端返回了实际模型名（计量按实际值，不按配置猜）",
        bool(outcome.model),
        f"model={outcome.model}",
    )
    check("token 计量有值（成本可核算）", outcome.tokens_in > 0 or outcome.tokens_out > 0,
          f"in={outcome.tokens_in} out={outcome.tokens_out}")
    check("至少产出一个校准项", bool(outcome.entries), f"entries={len(outcome.entries)}")

    if outcome.entries:
        factors = [e.factor for e in outcome.entries]
        check(
            "系数被夹取在 [0.7, 1.5] 内（LLM 输出是不可信输入，AGENTS §7.6）",
            all(0.7 <= f <= 1.5 for f in factors),
            f"factors={factors}",
        )
        item = outcome.items[0]
        check(
            "建议量仍受容量约束（0 ≤ 建议 ≤ 容量-现库存 = 9）",
            0 <= item.suggested_quantity <= 9,
            f"建议={item.suggested_quantity}（上限 9）",
        )
        check(
            "生成的理由非空且写进 item.reason（前端“依据”字段有内容）",
            bool(item.reason.strip()),
            f"reason 长度={len(item.reason)}",
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="FIX-17 降级 / FIX-11 live 校验")
    parser.add_argument("--phase", required=True, choices=["invalid-key", "live"])
    args = parser.parse_args()

    if args.phase == "invalid-key":
        await phase_invalid_key()
    else:
        await phase_live()

    print(f"\n结果：PASS={_pass} FAIL={_fail}")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
