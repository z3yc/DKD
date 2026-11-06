"""2-7 三载体配置接入 + 2-8 config_version 埋点（契约 §5.2 / §5.3）。

## 这份测试锚定什么

配置化的验收不是“能读到配置”，而是两条**反向**保证同时成立：

1. **配置 = 代码默认 ⇒ 行为逐字不变**（chat 四场景 prompt、restock 模板、fault 阈值/窗口、
   LLM 参数不传=现状）——否则“上了配置台”本身就成了行为变更，线上差异无法归因；
2. **配置真的生效**（自定义场景工具子集、停用/未知场景拒绝、阈值/窗口/版本按配置走）——
   否则只是“配了但不生效”，比不配更危险（运营会以为已经调过）。

## 纪律

全部打桩：不连 MySQL（`agent_config` 用 `AgentConfigSnapshot` 假快照 / `config_loader` 假加载器）、
不调 LLM（LLM 客户端全部注入假实现）。真库路径由 `GET /agents/config/effective` 与联调覆盖。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from app.agent_defaults import default_system_prompt
from app.checkpoint import SqliteCheckpointStore
from app.config import Settings
from app.config_store import AgentConfigSnapshot
from app.graphs import chat_graph, fault_graph
from app.graphs.restock_calibration import calibrate_plan
from app.graphs.restock_state import PLAN_STATUS_SUGGESTED, RestockItem, RestockPlan
from app.llm import build_chat_model
from app.prompts import assistant_chat, restock_calibration
from app.services import carrier_config
from app.services.diagnosis import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, compose_diagnosis
from app.services.restock_analyzer import STATUS_ANALYZED, RestockAnalyzer
from app.tools.read_tools import MachineProfile, OrderAnomaly


def _snapshot(**overrides: Any) -> AgentConfigSnapshot:
    """构造一个假配置快照（字段与 `config_store.AgentConfigSnapshot` 一一对应）。"""
    base: dict[str, Any] = {
        "code": "chat",
        "name": "测试智能体",
        "runtime": "chat",
        "scene": 1,
        "builtin": False,
        "system_prompt": None,
        "tools": (),
        "model": None,
        "temperature": None,
        "max_tokens": None,
        "timeout_s": 60,
        "max_retries": 2,
        "params": {},
        "enabled": True,
        "version": 3,
    }
    base.update(overrides)
    return AgentConfigSnapshot(**base)


# ======================================================================================
# chat：配置=默认 ⇒ prompt 逐字相同；override 生效；坏模板回落
# ======================================================================================


@pytest.mark.parametrize("scene", [1, 2, 3, 4])
def test_chat_prompt_with_default_config_is_byte_identical(scene: int) -> None:
    """chat 四场景：用 seed 的默认 prompt 作 override，渲染结果必须与代码默认**逐字相同**。

    这条是排期 2-7 的验收锚点（契约 §5.3）——seed 就是从 `BASE_PROMPT` 导出的，
    所以“配置=默认”时行为不能有任何变化（包括 focus/window/diagnosis 追加段）。
    """
    override = default_system_prompt("chat")
    assert override == assistant_chat.BASE_PROMPT, "seed 的 chat 提示词必须逐字引用同一份常量"

    kwargs: dict[str, Any] = {
        "focus_inner_code": "A1000001",
        "window_days": 90,
        "diagnosis_hint": "\n\n## 本轮排查进度\n",
    }
    assert assistant_chat.system_prompt(
        scene, base_prompt_override=override, **kwargs
    ) == assistant_chat.system_prompt(scene, **kwargs)


def test_chat_prompt_override_is_rendered_and_bad_template_falls_back() -> None:
    """自定义提示词作为基座渲染；未知占位符/花括号不配对 → 回落默认 + 不抛穿。"""
    custom = "【自定义角色】\n{focus}\n\n可用工具：{tool_list}\n\n约束：{write_guidance}"
    rendered = assistant_chat.system_prompt(2, base_prompt_override=custom)
    assert rendered.startswith("【自定义角色】")
    assert "当前对话场景：**智能补货**" in rendered, "focus 必须按场景拼进去"
    assert "`list_restock_suggestions`" in rendered, "tool_list 必须拼进去"

    default = assistant_chat.system_prompt(2)
    # 未知占位符（历史脏数据/手工改库）必须回落默认模板，而不是把对话打成 500
    assert assistant_chat.system_prompt(2, base_prompt_override="坏模板 {unknown}") == default
    # 花括号不配对同样回落
    assert assistant_chat.system_prompt(2, base_prompt_override="坏模板 {") == default


# ======================================================================================
# restock：默认模板逐字相同；override 生效
# ======================================================================================


def test_restock_prompt_with_default_config_is_byte_identical() -> None:
    """restock 默认模板：seed prompt 就是 `SYSTEM_PROMPT`，默认参数下渲染逐字相同。"""
    assert default_system_prompt("restock") == restock_calibration.SYSTEM_PROMPT
    default = restock_calibration.system_prompt(factor_min=0.7, factor_max=1.5)
    with_override = restock_calibration.system_prompt(
        factor_min=0.7,
        factor_max=1.5,
        template_override=default_system_prompt("restock"),
    )
    assert with_override == default
    assert "{fmin}" not in default and "0.7" in default and "1.5" in default


def test_restock_prompt_override_and_bad_template_falls_back() -> None:
    text = restock_calibration.system_prompt(
        factor_min=0.8, factor_max=1.3, template_override="只输出 JSON，系数 {fmin}~{fmax}"
    )
    assert text == "只输出 JSON，系数 0.8~1.3"
    assert restock_calibration.system_prompt(template_override="坏 {oops}") == (
        restock_calibration.system_prompt()
    )


# ======================================================================================
# chat 场景校验：停用/未知场景在建流前拒绝（HTTP 400），无配置 = 现状
# ======================================================================================


def test_disabled_builtin_scene_is_rejected_before_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内置 1-4 被停用 → HTTP 400（且**不能**先开 SSE 流再报错）。"""

    async def fake(scene: int) -> AgentConfigSnapshot:
        return _snapshot(enabled=False)

    monkeypatch.setattr(carrier_config, "load_chat_config", fake)

    resp = client.post("/agent/chat", json={"message": "hi", "scene": 1})

    assert resp.status_code == 400
    assert "停用" in resp.json()["msg"]
    assert "text/event-stream" not in resp.headers.get("content-type", "")


def test_builtin_scene_without_config_keeps_legacy_behavior(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无配置（DB 关闭/无记录）时 1-4 场景照旧可用——「未配置 = 现状」。"""

    async def fake(scene: int) -> None:
        return None

    monkeypatch.setattr(carrier_config, "load_chat_config", fake)

    resp = client.post("/agent/chat", json={"message": "hi", "scene": 2})

    assert resp.status_code == 200
    assert "event: done" in resp.text


def test_custom_scene_requires_enabled_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """≥101 自定义场景：未启用/不存在 → 400；命中已启用配置 → 允许。"""

    async def missing(scene: int) -> None:
        return None

    monkeypatch.setattr(carrier_config, "load_chat_config", missing)
    rejected = client.post("/agent/chat", json={"message": "hi", "scene": 101})
    assert rejected.status_code == 400
    assert "场景不存在或未启用" in rejected.json()["msg"]

    async def enabled(scene: int) -> AgentConfigSnapshot:
        return _snapshot(scene=101, code="custom_a", runtime="chat", tools=("find_devices",))

    monkeypatch.setattr(carrier_config, "load_chat_config", enabled)
    allowed = client.post("/agent/chat", json={"message": "hi", "scene": 101})
    assert allowed.status_code == 200


def test_reserved_scene_range_is_still_parameter_error(client: TestClient) -> None:
    """5~100 是保留空洞（1-4 内置、101 起自定义）：沿用改造前的 422 语义。"""
    resp = client.post("/agent/chat", json={"message": "hi", "scene": 5})
    assert resp.status_code == 422


# ======================================================================================
# chat 工具：按场景求值 + 自定义场景按配置过滤
# ======================================================================================


def _named_tools(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=name) for name in names]


async def test_resolve_scene_tools_filters_custom_scene_by_config() -> None:
    registry = _named_tools("find_devices", "get_machine_inventory", "get_sales_summary")

    def builtin(scene: int) -> list[Any]:
        return [SimpleNamespace(name="list_restock_suggestions")]

    async def loader(scene: int) -> AgentConfigSnapshot:
        return _snapshot(scene=scene, tools=("find_devices", "not_registered"))

    async def none(scene: int) -> None:
        return None

    async def call(scene: int, *, config_loader: Any, builtin_tools: Any = builtin) -> list[str]:
        tools = await chat_graph.resolve_scene_tools(
            scene,
            builtin_tools=builtin_tools,
            registry=lambda: registry,
            config_loader=config_loader,
        )
        return [tool.name for tool in tools]

    # 自定义场景：只保留配置里**且**注册表里存在的工具（拼错的不抛穿）
    assert await call(101, config_loader=loader) == ["find_devices"]
    # 内置 2/3：仍用 tools_for_scene（行为不变）
    assert await call(2, config_loader=loader) == ["list_restock_suggestions"]
    assert await call(3, config_loader=loader) == ["list_restock_suggestions"]
    # 内置 1/4：本来就无工具
    assert await call(1, config_loader=loader) == []
    assert await call(4, config_loader=loader) == []
    # 配置不可读 / 未命中 → 空工具，不抛穿
    assert await call(101, config_loader=none) == []
    assert await call(101, config_loader=None) == []


async def test_chat_graph_resolves_tools_and_config_per_turn(monkeypatch, tmp_path) -> None:
    """每轮 respond 都重新求值工具集与配置（契约 §5.2：配置改后**下一次调用**生效）。"""
    snapshot = _snapshot(
        scene=101,
        code="custom_a",
        runtime="chat",
        system_prompt="【自定义角色】\n{focus}",
        model="deepseek-reasoner",
        temperature=0.3,
        max_tokens=512,
        max_retries=1,
    )
    provider_calls: list[int] = []
    loader_calls: list[int] = []
    prompts: list[str] = []
    llm_kwargs: list[dict[str, Any]] = []

    class _FakeModel:
        def bind_tools(self, tools: Any) -> _FakeModel:
            return self

        async def ainvoke(self, messages: Any) -> AIMessage:
            prompts.append(str(messages[0].content))
            return AIMessage(
                content="好的。",
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                response_metadata={"model_name": "stub"},
            )

    fake_model = _FakeModel()

    async def _noop_tool(inner_code: str = "") -> str:  # noqa: ARG001 —— 桩工具不参与执行
        return "{}"

    tool = StructuredTool.from_function(
        coroutine=_noop_tool, name="find_devices", description="桩工具（本用例不会执行它）"
    )

    def _fake_build(*_args: Any, **kwargs: Any) -> _FakeModel:
        llm_kwargs.append(kwargs)
        return fake_model

    monkeypatch.setattr(chat_graph, "build_chat_model", _fake_build)

    def provider(scene: int) -> list[Any]:
        provider_calls.append(scene)
        return [tool]

    async def loader(scene: int) -> AgentConfigSnapshot:
        loader_calls.append(scene)
        return snapshot

    store = SqliteCheckpointStore(tmp_path / "cp-provider.db")
    saver = await store.open()
    try:
        graph = chat_graph.build_chat_graph(
            checkpointer=saver,
            use_llm=True,
            tools_provider=provider,
            tool_registry=[tool],
            config_loader=loader,
        )
        config = {"configurable": {"thread_id": "t-provider"}}
        for _ in range(2):
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "hi"}], "scene": 101}, config=config
            )
    finally:
        await store.close()

    assert provider_calls == [101, 101], "工具集必须每轮求值（不能启动时定死）"
    assert loader_calls == [101, 101], "配置必须每轮求值"
    assert "【自定义角色】" in prompts[0], "配置的提示词必须真的进入 system"
    assert llm_kwargs and llm_kwargs[0]["temperature"] == 0.3
    assert llm_kwargs[0]["model"] == "deepseek-reasoner"
    assert llm_kwargs[0]["max_tokens"] == 512


# ======================================================================================
# fault：默认阈值/窗口不变；配置值生效；2-8 config_version 透传 / 回落 None
# ======================================================================================


def _order(order_id: int) -> OrderAnomaly:
    return OrderAnomaly(
        kind="paid_not_vended",
        order_id=order_id,
        order_no=f"NO{order_id}",
        status=1,
        reason="支付完成超过阀值未出货",
    )


def _profile() -> MachineProfile:
    return MachineProfile(vm_id=1, inner_code="A1000001", vm_status=1, addr="测试地址")


def _fault_facts() -> dict[str, Any]:
    return {
        "profile": _profile(),
        "orders": [],
        "inventory": [],
        "inflight": [],
        "history": None,
        "stock": [],
        "window": {"hours": 24},
    }


def test_fault_confidence_threshold_default_unchanged() -> None:
    """默认阈值仍是 2（= 改造前硬编码）：1 条 medium、2 条 high；调高阈值才变 medium。"""
    one = compose_diagnosis(profile=_profile(), order_anomalies=[_order(1)])
    assert one.confidence == CONFIDENCE_MEDIUM

    two = compose_diagnosis(profile=_profile(), order_anomalies=[_order(1), _order(2)])
    assert two.confidence == CONFIDENCE_HIGH

    raised = compose_diagnosis(
        profile=_profile(), order_anomalies=[_order(1), _order(2)], order_threshold=3
    )
    assert raised.confidence == CONFIDENCE_MEDIUM, "阈值参数必须真的生效（不是装饰）"


class _FakeRunStore:
    """记录调用序列的假观测写入器（满足 RunStore 协议，不连库）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def insert_run(self, **kwargs: Any) -> None:
        self.calls.append(("insert_run", kwargs))

    async def update_run(self, **kwargs: Any) -> None:
        self.calls.append(("update_run", kwargs))

    async def insert_step(self, **kwargs: Any) -> None:
        self.calls.append(("insert_step", kwargs))

    async def insert_tool_call(self, **kwargs: Any) -> None:
        self.calls.append(("insert_tool_call", kwargs))

    async def interrupt_stale_runs(self, **kwargs: Any) -> int:
        return 0

    def first(self, op: str) -> dict[str, Any]:
        for name, kwargs in self.calls:
            if name == op:
                return kwargs
        raise AssertionError(f"没有调用 {op}")


def _obs_settings() -> Settings:
    """只构造观测必需的字段（与 tests/test_fault_diagnosis.py 的 make_settings 同做法）。"""
    return Settings.model_construct(obs_enabled=True)


def _fault_deps(store: _FakeRunStore) -> fault_graph.FaultDeps:
    return fault_graph.FaultDeps(
        settings=_obs_settings(),
        store=store,
        now=lambda: datetime(2026, 11, 3, 9, 0, 0),
    )


def _patch_fault_collect(monkeypatch: pytest.MonkeyPatch, seen: dict[str, int]) -> None:
    async def fake_collect(
        deps: fault_graph.FaultDeps,
        *,
        run_id: str,
        inner_code: str,
        window_hours: int,
        step_no: int = 1,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        seen["window_hours"] = window_hours
        return _fault_facts(), [], None

    monkeypatch.setattr(fault_graph, "collect_facts", fake_collect)


async def test_fault_run_uses_config_window_and_records_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fault：窗口取配置 params.window_hours；agent_run.config_version = 实际生效版本。"""
    store = _FakeRunStore()
    seen: dict[str, int] = {}
    _patch_fault_collect(monkeypatch, seen)

    async def loader() -> AgentConfigSnapshot:
        return _snapshot(
            code="fault",
            runtime="fault",
            scene=3,
            params={"window_hours": 48, "confidence_order_threshold": 3},
            version=9,
        )

    state = await fault_graph.run_fault_diagnosis(
        _fault_deps(store), inner_code="A1000001", config_loader=loader
    )

    assert state["window_hours"] == 48
    assert seen["window_hours"] == 48, "配置的窗口必须真的传给取数层"
    assert store.first("insert_run")["config_version"] == 9


async def test_fault_run_falls_back_to_code_defaults_and_none_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置回落（DB 关闭/无记录）→ 窗口 24、版本 None（**不编造版本号**）。"""
    store = _FakeRunStore()
    seen: dict[str, int] = {}
    _patch_fault_collect(monkeypatch, seen)

    async def loader() -> None:
        return None

    await fault_graph.run_fault_diagnosis(
        _fault_deps(store), inner_code="A1000001", config_loader=loader
    )

    assert seen["window_hours"] == 24, "默认窗口必须仍是 24（未配置=现状）"
    assert store.first("insert_run")["config_version"] is None


# ======================================================================================
# restock：params 覆盖 Settings、prompt 覆盖、batch_size、2-8 config_version
# ======================================================================================


def _restock_item(code: str, *, qty: int = 8, current: int = 2, capacity: int = 20) -> RestockItem:
    return RestockItem(
        sku_id=1,
        sku_name="可口可乐 330ml",
        channel_id=4732,
        channel_code=code,
        current_quantity=current,
        max_capacity=capacity,
        suggested_quantity=qty,
        after_restock_quantity=current + qty,
        estimated_days=2,
        priority=3,
        reason="基线依据",
    )


def _restock_plan(count: int = 3) -> RestockPlan:
    return RestockPlan(
        plan_date="2026-11-03",
        vm_id=80,
        inner_code="A1000001",
        status=PLAN_STATUS_SUGGESTED,
        items=[_restock_item(f"1-{index + 1}") for index in range(count)],
    )


def test_effective_restock_settings_override_only_configured_fields() -> None:
    base = Settings(_env_file=None)
    snapshot = _snapshot(
        code="restock",
        runtime="restock",
        scene=2,
        params={
            "quantile": 0.9,
            "coverage_days": 3.0,
            "service_factor": 1.5,
            "factor_min": 0.8,
            "factor_max": 1.4,
            "batch_size": 7,
        },
    )
    effective = carrier_config.effective_restock_settings(base, snapshot)

    assert effective.restock_quantile == 0.9
    assert effective.restock_coverage_days == 3.0
    assert effective.restock_service_factor == 1.5
    assert effective.restock_calibration_min_factor == 0.8
    assert effective.restock_calibration_max_factor == 1.4
    assert effective.restock_calibration_batch_size == 7
    # 配置缺失 = 现状：返回原对象，不造一份“看起来有值”的新 Settings
    assert carrier_config.effective_restock_settings(base, None) is base


def test_llm_options_omit_unset_values_but_keep_config_defaults() -> None:
    """NULL 类参数（温度/输出上限）不传 = 模型默认；数值默认与代码默认一致。"""
    assert carrier_config.llm_options(None) == {}

    unset = _snapshot()
    options = carrier_config.llm_options(unset)
    assert "temperature" not in options and "max_tokens" not in options
    # timeout/retries 在 DDL 里 NOT NULL，默认 60/2 与改造前硬编码一致 → 传了也等价
    assert options == {"max_retries": 2, "timeout_s": 60}

    configured = carrier_config.llm_options(
        _snapshot(
            model="deepseek-chat", temperature=0.2, max_tokens=1024, max_retries=1, timeout_s=30
        )
    )
    assert configured == {
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 1024,
        "max_retries": 1,
        "timeout_s": 30,
    }


def test_build_chat_model_none_means_unchanged() -> None:
    """`build_chat_model` 不传配置参数时行为与改造前一致（Q3：NULL = 未设置）。"""
    settings = Settings(
        _env_file=None, DKD_DEEPSEEK_API_KEY="sk-unit-test-placeholder", DKD_AGENT_LLM_TIMEOUT=12.5
    )
    plain = build_chat_model(settings)
    assert (plain.model_name, plain.request_timeout, plain.max_retries) == (
        "deepseek-chat",
        12.5,
        2,
    )
    assert plain.temperature is None and plain.max_tokens is None

    overridden = build_chat_model(
        settings,
        model="deepseek-reasoner",
        temperature=0.3,
        max_tokens=512,
        max_retries=4,
        timeout_s=20.0,
    )
    assert (overridden.model_name, overridden.request_timeout, overridden.max_retries) == (
        "deepseek-reasoner",
        20.0,
        4,
    )
    assert overridden.temperature == 0.3
    assert overridden.max_tokens == 512


class _FakeChatModel:
    """假 LLM：按顺序回放内容，并记录收到的 messages。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return AIMessage(
            content=self._replies[index],
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            response_metadata={"model_name": "fake-model"},
        )


async def test_restock_calibration_batches_by_config_and_uses_prompt_override() -> None:
    """batch_size 用配置值（默认 20）：3 条货道 + batch=2 → 2 次请求；提示词走 override。"""
    model = _FakeChatModel(
        [
            json.dumps(
                {
                    "calibrations": [
                        {"channelCode": "1-1", "factor": 1.2},
                        {"channelCode": "1-2", "factor": 1.1},
                    ]
                }
            ),
            json.dumps({"calibrations": [{"channelCode": "1-3", "factor": 1.2}]}),
        ]
    )
    settings = Settings(_env_file=None, DKD_AGENT_RESTOCK_CALIBRATION_BATCH_SIZE="2")

    outcome = await calibrate_plan(
        _restock_plan(3),
        model=model,
        settings=settings,
        system_prompt_override="自定义校准模板：系数 {fmin}~{fmax}",
    )

    assert len(model.calls) == 2, "batch_size=2 时 3 条货道必须分 2 次请求"
    assert "自定义校准模板：系数 0.7~1.5" in str(model.calls[0][0])
    assert outcome.applied is True
    assert [entry.channel_code for entry in outcome.entries] == ["1-1", "1-2", "1-3"]
    assert outcome.items[0].suggested_quantity == 10  # round(8 × 1.2)
    assert outcome.tokens_in == 20  # 两次调用各 10，必须累加


async def test_restock_calibration_single_batch_matches_legacy_call() -> None:
    """默认 batch_size=20 时 ≤20 条货道只发一次请求（与改造前一致，不因分批多花 token）。"""
    model = _FakeChatModel([json.dumps({"calibrations": [{"channelCode": "1-1", "factor": 1.0}]})])
    outcome = await calibrate_plan(_restock_plan(1), model=model, settings=Settings(_env_file=None))
    assert len(model.calls) == 1
    assert outcome.applied is True


class _FakeGraph:
    """假补货图：回放固定结果（不读库、不调 LLM）。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return self.result


class _FakePauseStore:
    async def get(self) -> Any:
        return SimpleNamespace(paused=False, paused_by=None, reason=None)


def _analyzer(
    *,
    run_store: _FakeRunStore,
    config_loader: Any,
) -> RestockAnalyzer:
    return RestockAnalyzer(
        graph_factory=lambda: _FakeGraph({"plans": [], "summary": {}}),
        pause_store=_FakePauseStore(),
        settings=Settings(_env_file=None, DKD_AGENT_OBS_ENABLED="1"),
        run_store=run_store,
        config_loader=config_loader,
        today_provider=lambda: date(2026, 11, 3),
    )


async def test_restock_analyzer_records_config_version() -> None:
    """2-8：restock 每次运行写 agent_run.config_version = 本次实际生效版本。"""
    store = _FakeRunStore()

    async def loader() -> AgentConfigSnapshot:
        return _snapshot(code="restock", runtime="restock", scene=2, version=5)

    outcome = await _analyzer(run_store=store, config_loader=loader).analyze()

    assert outcome.status == STATUS_ANALYZED
    assert store.first("insert_run")["config_version"] == 5
    assert store.first("update_run")["status"] == "done"


async def test_restock_analyzer_config_version_none_when_config_falls_back() -> None:
    """配置回落/DB 关闭 → config_version 写 None（不编造版本号）。"""
    store = _FakeRunStore()

    async def loader() -> None:
        return None

    await _analyzer(run_store=store, config_loader=loader).analyze()

    assert store.first("insert_run")["config_version"] is None


# ======================================================================================
# main.py：health 报配置来源（只读配置值，不连库）
# ======================================================================================


def test_health_reports_config_source_fields(client: TestClient) -> None:
    body = client.get("/health").json()

    # conftest 统一把 DKD_AGENT_CONFIG_FROM_DB 置 0（测试不连真库），health 必须如实反映
    assert body["config_from_db"] is False
    assert body["config_ttl_s"] == 30.0
