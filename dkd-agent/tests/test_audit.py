"""留痕 / 脱敏 / 成本计量测试（任务 0-12）。

用真实 SQLite 跑真 SQL（AGENTS §8：校验类逻辑必须有拒绝路径用例）——
表结构由 SQLAlchemy 模型生成，与 `docs/ddl/agent_tables.sql` 列一一对应。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit import (
    RESULT_FAIL,
    RESULT_OK,
    RESULT_PENDING,
    RESULT_REJECTED,
    Base,
    DecisionLog,
    Message,
    QuotaExceededError,
    enforce_daily_quota,
    mask_sensitive,
    record_decision,
    record_message,
    summarize_usage,
)


@pytest_asyncio.fixture
async def session(tmp_path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# --- 脱敏（AGENTS §7.8）---


def test_mask_phone():
    assert mask_sensitive("联系用户 13812345678 处理") == "联系用户 138****5678 处理"


def test_mask_card_and_order_number():
    assert mask_sensitive("卡号 6225880137788991") == "卡号 6225********8991"
    assert mask_sensitive("订单 1234567890123456789") == "订单 1234***********6789"


def test_mask_keeps_normal_numbers_intact():
    # 货道容量、库存数量等业务数字不能被误脱敏
    assert mask_sensitive("货道 12 容量 6 现库存 2") == "货道 12 容量 6 现库存 2"


def test_mask_preserves_none():
    assert mask_sensitive(None) is None


# --- 留痕 ---


async def test_record_decision_persists_row(session: AsyncSession):
    await record_decision(
        session,
        scene=2,
        action="restock_plan.create",
        request_id="rid-1",
        user_id=7,
        input_context={"phone": "13812345678", "innerCode": "VM-001"},
        llm_output={"suggested": 6},
        result=RESULT_OK,
        cost_tokens=120,
    )
    row = (await session.execute(DecisionLog.__table__.select())).one()
    assert row.action == "restock_plan.create"
    assert row.scene == 2
    assert row.result == RESULT_OK
    assert row.cost_tokens == 120
    # 脱敏后落库：原手机号不得出现
    assert "13812345678" not in str(row.input_context)
    assert "138****5678" in str(row.input_context)


async def test_record_decision_swallows_sqlalchemy_error(session: AsyncSession, monkeypatch):
    """SQLAlchemyError 路径：留痕失败只告警，不把异常抛给用户对话（审计旁路）。"""
    from sqlalchemy.exc import IntegrityError

    async def boom() -> None:
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(session, "commit", boom)
    await record_decision(session, scene=1, action="chat.respond")  # 不得抛出


async def test_record_decision_propagates_programming_errors(session: AsyncSession):
    """非 SQLAlchemyError（如编码错误）应当暴露，不被"静默吞掉"（AGENTS §6.1）。"""

    class _BrokenSession:
        def add(self, _row: object) -> None:
            raise TypeError("coding bug")

    with pytest.raises(TypeError):
        await record_decision(_BrokenSession(), scene=1, action="x")  # type: ignore[arg-type]


async def test_record_message_persists_and_masks(session: AsyncSession):
    await record_message(
        session,
        conversation_id="conv-1",
        user_id=7,
        seq=1,
        msg_role=2,
        content="已为用户 13900001111 创建工单",
        model="deepseek-flash",
        tokens_in=10,
        tokens_out=5,
        request_id="rid-2",
    )
    row = (await session.execute(Message.__table__.select())).one()
    assert row.user_id == 7
    assert row.model == "deepseek-flash"
    assert "13900001111" not in row.content
    assert "139****1111" in row.content


# --- 成本计量与限额 ---


async def _seed(session: AsyncSession, *, user_id: int, tokens: int, age_days: int = 0) -> None:
    conv = f"conv-{user_id}-{age_days}"
    await record_message(
        session,
        conversation_id=conv,
        user_id=user_id,
        seq=1,
        msg_role=2,
        tokens_in=tokens,
        tokens_out=0,
    )
    if age_days:
        # 只改本行时间，别误伤其他用例的数据（update 无 where 会把全表改掉）
        await session.execute(
            update(Message)
            .where(Message.conversation_id == conv)
            .values(create_time=datetime.now() - timedelta(days=age_days))
        )
        await session.commit()


async def test_summarize_usage_aggregates_today(session: AsyncSession):
    await _seed(session, user_id=1, tokens=100)
    await _seed(session, user_id=1, tokens=50)
    await _seed(session, user_id=2, tokens=999, age_days=3)  # 窗口外

    total = await summarize_usage(session, days=1)
    assert total.total_calls == 2
    assert total.tokens_in == 150

    only_user1 = await summarize_usage(session, days=1, user_id=1)
    assert only_user1.tokens_total == 150
    only_user2 = await summarize_usage(session, days=1, user_id=2)
    assert only_user2.tokens_total == 0  # 3 天前的不计入当日窗口


async def test_quota_blocks_when_per_user_limit_exceeded(session: AsyncSession):
    await _seed(session, user_id=9, tokens=120)
    with pytest.raises(QuotaExceededError, match="当日 token 已达上限"):
        await enforce_daily_quota(session, user_id=9, per_user_limit=100, global_limit=0)


async def test_quota_blocks_when_global_limit_exceeded(session: AsyncSession):
    await _seed(session, user_id=1, tokens=80)
    await _seed(session, user_id=2, tokens=80)
    with pytest.raises(QuotaExceededError, match="全局"):
        await enforce_daily_quota(session, user_id=3, per_user_limit=0, global_limit=100)


async def test_quota_passes_under_limit(session: AsyncSession):
    await _seed(session, user_id=1, tokens=10)
    await enforce_daily_quota(session, user_id=1, per_user_limit=100, global_limit=1000)


async def test_quota_disabled_when_limits_zero(session: AsyncSession):
    await _seed(session, user_id=1, tokens=10_000)
    await enforce_daily_quota(session, user_id=1, per_user_limit=0, global_limit=0)


# --- P1-12：全局用量 TTL 缓存与 scene 死参数清理 ---


async def test_global_usage_cached_within_ttl(session: AsyncSession, monkeypatch):
    """TTL 内 global 查询不重复执行；缓存可被强制失效。"""
    import app.audit as audit_mod

    await _seed(session, user_id=1, tokens=30)

    calls = {"n": 0}
    real = audit_mod.summarize_usage

    async def counting(session_: AsyncSession, **kw: object) -> object:
        calls["n"] += 1
        return await real(session_, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(audit_mod, "summarize_usage", counting)

    # per_user 关闭、global 打开：global 走缓存
    await enforce_daily_quota(session, user_id=1, per_user_limit=0, global_limit=1000)
    await enforce_daily_quota(session, user_id=1, per_user_limit=0, global_limit=1000)
    await enforce_daily_quota(session, user_id=1, per_user_limit=0, global_limit=1000)
    assert calls["n"] == 1, "TTL 内 global 用量必须命中缓存，不能每请求都全表聚合"

    # 强制失效后重新查询
    audit_mod._reset_usage_cache()
    await enforce_daily_quota(session, user_id=1, per_user_limit=0, global_limit=1000)
    assert calls["n"] == 2


async def test_global_quota_blocks_with_cached_value(session: AsyncSession):
    """缓存值超限仍要熔断：TTL 缓存不能把 429 语义缓存没。"""
    await _seed(session, user_id=1, tokens=80)

    # 第一次调用把超限的 global 用量写入缓存（80 >= 50）
    with pytest.raises(QuotaExceededError, match="全局"):
        await enforce_daily_quota(session, user_id=2, per_user_limit=0, global_limit=50)

    # TTL 内第二次：读缓存，同样必须熔断
    with pytest.raises(QuotaExceededError, match="全局"):
        await enforce_daily_quota(session, user_id=2, per_user_limit=0, global_limit=50)


async def test_per_user_quota_not_cached(session: AsyncSession, monkeypatch):
    """per-user 查询不做缓存：每个用户自己的限额必须每请求精确判断。"""
    import app.audit as audit_mod

    await _seed(session, user_id=5, tokens=40)

    calls = {"n": 0}
    real = audit_mod.summarize_usage

    async def counting(session_: AsyncSession, **kw: object) -> object:
        calls["n"] += 1
        return await real(session_, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(audit_mod, "summarize_usage", counting)

    await enforce_daily_quota(session, user_id=5, per_user_limit=100, global_limit=0)
    await enforce_daily_quota(session, user_id=5, per_user_limit=100, global_limit=0)
    assert calls["n"] == 2, "per-user 限额查询必须每请求执行（有 (user_id, create_time) 索引）"


def test_summarize_usage_has_no_scene_param():
    """P1-12 语义 bug 防回归：scene 参数过滤的是 msg_role（口径错误）且无调用方，已删除。"""
    import inspect

    params = inspect.signature(summarize_usage).parameters
    assert "scene" not in params, (
        "scene 过滤的是 Message.msg_role（按场景统计的口径是错的）；"
        "如需按场景统计，应加 scene 列与正确过滤，而不是复活此参数"
    )


def test_result_codes_match_ddl_comment():
    """结果码必须与 agent_tables.sql 的列注释一致，避免 MySQL/Java/Python 三端语义漂移。"""
    assert (RESULT_PENDING, RESULT_OK, RESULT_FAIL, RESULT_REJECTED) == (0, 1, 2, 3)
    ddl = Path(__file__).resolve().parents[2] / "docs" / "ddl" / "agent_tables.sql"
    assert ddl.exists(), f"DDL 未归档（AGENTS §4.3）：{ddl}"
    assert "0-待定 1-成功 2-失败 3-被拒绝" in ddl.read_text(encoding="utf-8")
