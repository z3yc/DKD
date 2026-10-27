"""运行观测写入器单测（F-02）。

验证四件事（AGENTS §8 要求覆盖拒绝路径与旁路纪律）：
1. 正常路径：run/step/tool_call 的字段映射正确；
2. 幂等与脱敏：证据中文不转义、工具参数只存摘要；
3. 旁路纪律：观测关闭不写、写失败不抛、非法参数跳过；
4. 启动扫描：返回影响行数、失败返回 0。

只用假 store，**不连 MySQL**（AGENTS §8：测试不得连接数据库通道）。
"""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.obs import run_recorder as rr


class FakeStore:
    """记录调用的假 store（可模拟失败）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = False
        self.stale_count = 3

    def _record(self, op: str, kwargs: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.calls.append((op, kwargs))

    async def insert_run(self, **kwargs: Any) -> None:
        self._record("insert_run", kwargs)

    async def update_run(self, **kwargs: Any) -> None:
        self._record("update_run", kwargs)

    async def insert_step(self, **kwargs: Any) -> None:
        self._record("insert_step", kwargs)

    async def insert_tool_call(self, **kwargs: Any) -> None:
        self._record("insert_tool_call", kwargs)

    async def interrupt_stale_runs(self, **kwargs: Any) -> int:
        self._record("interrupt_stale_runs", kwargs)
        return self.stale_count


def make_settings(**overrides: Any) -> Settings:
    """构造 Settings 而不读环境变量/.env（model_construct 只填给定字段 + 默认值）。"""
    base: dict[str, Any] = {"obs_enabled": True, "run_stale_minutes": 30}
    base.update(overrides)
    return Settings.model_construct(**base)


async def test_start_run_writes_expected_fields() -> None:
    store = FakeStore()
    await rr.start_run(
        make_settings(),
        store,
        run_id="r1",
        scene_code="fault",
        subject_type="vm",
        subject_id="A1000001",
        trigger_type=2,
        config_version=7,
    )

    assert store.calls[0][0] == "insert_run"
    kwargs = store.calls[0][1]
    assert kwargs["run_id"] == "r1"
    assert kwargs["scene_code"] == "fault"
    assert kwargs["subject_type"] == "vm"
    assert kwargs["subject_id"] == "A1000001"
    assert kwargs["trigger_type"] == 2
    assert kwargs["config_version"] == 7
    # 无网关/定时任务注入时写 NULL，不编造（纪律 3）
    assert kwargs["request_id"] is None


async def test_finish_run_only_marks_finished_on_terminal_status() -> None:
    store = FakeStore()
    settings = make_settings()

    await rr.finish_run(settings, store, run_id="r1", status="waiting_confirm")
    await rr.finish_run(settings, store, run_id="r1", status="done", stop_reason="已建单")

    assert store.calls[0][1]["finished"] is False
    assert store.calls[1][1]["finished"] is True
    assert store.calls[1][1]["stop_reason"] == "已建单"


async def test_record_step_keeps_chinese_readable() -> None:
    store = FakeStore()
    await rr.record_step(
        make_settings(),
        store,
        run_id="r1",
        step_no=2,
        role_code="diagnostician",
        action="diagnose",
        summary="3 号货道出货异常",
        evidence={"证据": ["订单异常", "库存异常"]},
    )

    kwargs = store.calls[0][1]
    assert kwargs["step_no"] == 2
    # ensure_ascii=False：页面直接可读，不出现 \uXXXX
    assert "订单异常" in kwargs["evidence_json"]
    assert json.loads(kwargs["evidence_json"])["证据"] == ["订单异常", "库存异常"]


async def test_record_tool_call_stores_digest_not_raw_params() -> None:
    store = FakeStore()
    await rr.record_tool_call(
        make_settings(),
        store,
        run_id="r1",
        step_no=1,
        call_seq=1,
        tool_code="list_region_assignees",
        params={"mobile": "13800000000", "region_id": 3},
        row_count=12,
        duration_ms=35,
    )

    kwargs = store.calls[0][1]
    assert kwargs["params_digest"].startswith("sha256:")
    # 原始参数（含手机号）不得落库（AGENTS §7.8）
    assert "13800000000" not in json.dumps(kwargs, ensure_ascii=False)
    assert kwargs["row_count"] == 12


async def test_disabled_observation_writes_nothing() -> None:
    store = FakeStore()
    settings = make_settings(obs_enabled=False)

    await rr.start_run(
        settings,
        store,
        run_id="r1",
        scene_code="fault",
        subject_type="vm",
        subject_id="A1000001",
    )
    await rr.record_step(
        settings,
        store,
        run_id="r1",
        step_no=1,
        role_code="diagnostician",
        action="diagnose",
    )

    assert store.calls == []


async def test_store_failure_does_not_break_business() -> None:
    store = FakeStore()
    store.fail = True

    # 不抛异常：观测是旁路，业务不能因为写观测失败而失败
    await rr.start_run(
        make_settings(),
        store,
        run_id="r1",
        scene_code="fault",
        subject_type="vm",
        subject_id="A1000001",
    )
    await rr.finish_run(make_settings(), store, run_id="r1", status="done")


async def test_invalid_scene_and_status_are_skipped() -> None:
    store = FakeStore()
    settings = make_settings()

    await rr.start_run(
        settings,
        store,
        run_id="r1",
        scene_code="FAULT",  # 大写拼错
        subject_type="vm",
        subject_id="A1000001",
    )
    await rr.finish_run(settings, store, run_id="r1", status="runing")  # 拼错
    await rr.record_step(
        settings,
        store,
        run_id="r1",
        step_no=0,  # 轮次从 1 开始
        role_code="diagnostician",
        action="diagnose",
    )

    assert store.calls == []


async def test_sweep_stale_runs_returns_count() -> None:
    store = FakeStore()
    swept = await rr.sweep_stale_runs(make_settings(), store, minutes=15)

    assert swept == 3
    assert store.calls[0][0] == "interrupt_stale_runs"
    assert store.calls[0][1]["older_than_minutes"] == 15
    # 终止原因必须可读，页面直接展示
    assert "服务重启" in store.calls[0][1]["reason"]


async def test_sweep_failure_returns_zero() -> None:
    store = FakeStore()
    store.fail = True

    assert await rr.sweep_stale_runs(make_settings(), store) == 0


async def test_digest_params_handles_empty() -> None:
    assert rr.digest_params(None) is None
    assert rr.digest_params({}) is None
    # 同一参数产生同一摘要（对账用），不同参数不同摘要
    assert rr.digest_params({"a": 1}) == rr.digest_params({"a": 1})
    assert rr.digest_params({"a": 1}) != rr.digest_params({"a": 2})
