"""接单人分配策略单测（排期任务 1-8 验收项 ①）。

「10 台设备跨区域可正确分配」这件事里，**可单测的部分**是：
在给定候选人与负载的前提下，选出来的人是谁、以及“没人可选”时是否如实返回 None。
真实库的 10 台跨区域验收（含 `role_code/status/region_id` 三道过滤）
放在 `test_restock_assignee_live.py`（-m live）与
`docs/scripts/verify-1-8-assignee-concurrency.py`（真库证据留档）。

这里全部是纯函数调用：不连库、不起图、不碰 LLM（AGENTS §8）。
"""

from __future__ import annotations

from app.graphs.restock_assignee import pick_assignee
from app.tools.read_tools import Assignee


def _assignee(emp_id: int, name: str = "运营", region_id: int = 1) -> Assignee:
    return Assignee(emp_id=emp_id, user_name=name, region_id=region_id)


def test_no_candidate_returns_none_not_a_cross_region_guess():
    """拒绝路径：区域内没人必须返回 None（计划落 6-待指派）。

    若这里“换个人凑上”，Java 侧会以「员工区域与设备区域不一致」拒绝，
    运营看到的是系统故障而不是“这个区域没人”。
    """
    assert pick_assignee([], loads={}) is None


def test_picks_least_loaded_candidate():
    candidates = [_assignee(6), _assignee(7), _assignee(52)]
    picked = pick_assignee(candidates, loads={6: 3, 7: 1, 52: 2})
    assert picked is not None and picked.emp_id == 7


def test_ties_break_by_emp_id_for_determinism():
    """负载相同时按 emp_id 升序——同一份输入永远得到同一个人（可复现）。"""
    candidates = [_assignee(52), _assignee(6), _assignee(7)]
    first = pick_assignee(candidates, loads={6: 1, 7: 1, 52: 1})
    # 输入顺序变了，结果不变（不依赖 SQL 的 order by 才碰巧正确）
    second = pick_assignee(list(reversed(candidates)), loads={6: 1, 7: 1, 52: 1})
    assert first is not None and first.emp_id == 6
    assert second is not None and second.emp_id == 6


def test_missing_load_counts_as_zero():
    one = pick_assignee([_assignee(6), _assignee(7)], loads={6: 5})
    two = pick_assignee([_assignee(6), _assignee(7)], loads={})
    assert one is not None and one.emp_id == 7
    assert two is not None and two.emp_id == 6


def test_single_candidate_is_always_chosen_even_if_busy():
    """只有一个人可用时也要派给他（不给“上限”这种没依据的规则）。"""
    picked = pick_assignee([_assignee(6)], loads={6: 99})
    assert picked is not None and picked.emp_id == 6
