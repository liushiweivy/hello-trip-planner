# -*- coding: utf-8 -*-
"""大纲契约校验：LLM 大纲 / 预制路线共用的一套结构断言（评测集核心）。

校验项（与应用运行时依赖一一对应）：
1. plans 恰好 4 套，plan_id 为 1-4
2. 风格两两互异（4 套必须有区分度）
3. 每套 days 覆盖全部旅行天数（day_index 连续从 0 开始）
4. 每天 1-3 个景点（深度慢游允许 1 个，但不能为 0）
5. 经济实惠预算必须显著低于经典全景（卡位不失效）
6. 坐标规则：要么 null（serve 时高德反查补全），要么落在中国范围内（防编造）
"""
from __future__ import annotations

from typing import Any, Dict, List

# 中国疆域经纬度包围盒（粗框，防编造坐标用，不做精确边界）
CN_LON_RANGE = (73.0, 135.5)
CN_LAT_RANGE = (17.5, 54.0)


class ContractViolation(AssertionError):
    """契约违反（带定位信息）"""


def _fail(msg: str) -> None:
    raise ContractViolation(msg)


def assert_outline_contract(payload: Dict[str, Any], travel_days: int,
                            label: str = "") -> None:
    """校验一份大纲 payload（{"plans": [...]}）；违规抛 ContractViolation"""
    where = f"[{label}] " if label else ""
    plans: List[Dict[str, Any]] = payload.get("plans") or []
    if len(plans) != 4:
        _fail(f"{where}plans 必须恰好 4 套，实际 {len(plans)} 套")
    ids = [p.get("plan_id") for p in plans]
    if ids != [1, 2, 3, 4]:
        _fail(f"{where}plan_id 必须为 1-4，实际 {ids}")

    styles = [p.get("style", "") for p in plans]
    if len(set(styles)) != 4:
        _fail(f"{where}4 套风格必须互异，实际 {styles}")

    for p in plans:
        tag = f"{where}方案{p.get('plan_id')}「{p.get('style')}」"
        days = p.get("days") or []
        indexes = [d.get("day_index") for d in days]
        if indexes != list(range(travel_days)):
            _fail(f"{tag} days 必须覆盖 0..{travel_days - 1}，实际 {indexes}")
        for d in days:
            spots = d.get("spots") or []
            if not (1 <= len(spots) <= 3):
                _fail(f"{tag} 第{d.get('day_index')}天景点数 {len(spots)} 超出 1-3")
            for s in spots:
                if not s.get("name"):
                    _fail(f"{tag} 存在无名景点")
                loc = s.get("location")
                if loc is not None:
                    lon, lat = loc.get("longitude"), loc.get("latitude")
                    if not (CN_LON_RANGE[0] <= (lon or 0) <= CN_LON_RANGE[1]
                            and CN_LAT_RANGE[0] <= (lat or 0) <= CN_LAT_RANGE[1]):
                        _fail(f"{tag} 景点「{s.get('name')}」坐标异常"
                              f"（{lon},{lat} 不在中国范围，疑似编造）")

    budgets = {p.get("style", ""): p.get("estimated_budget", 0) for p in plans}
    econ = next((v for k, v in budgets.items() if "经济" in k), None)
    classic = next((v for k, v in budgets.items() if "经典" in k), None)
    if econ is not None and classic is not None and not (econ < classic):
        _fail(f"{where}经济实惠预算（{econ}）必须显著低于经典全景（{classic}）")
