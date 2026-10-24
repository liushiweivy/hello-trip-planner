# -*- coding: utf-8 -*-
"""共享执行器：同步路由与异步 worker 走同一条执行路径（含持久化）。

- execute_outline：生成大纲 + best-effort 落历史行程（record_id 回填到返回值）
- execute_detail：持久化详情回放优先 → agent 生成 → best-effort 落库
持久化规则（mock/兜底/无 owner 跳过）与路由层原实现一致，只搬不改。
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..agents.trip_planner_agent import get_trip_planner_agent
from ..config import get_settings
from ..models.schemas import (
    Budget,
    BudgetJobRequest,
    BudgetJobResult,
    TripDetailRequest,
    TripOutlineData,
    TripPlan,
    TripRequest,
)
from .budget_cache import get_budget_cache, make_budget_key
from .plan_store import get_plan_store

logger = logging.getLogger("trip.exec")


def persist_outline(owner_key: str, request: TripRequest,
                    outline_data: TripOutlineData) -> None:
    """大纲落历史行程（best-effort）：mock/兜底/无 owner 跳过，失败不影响主流程"""
    if not owner_key or request.mock_llm or outline_data.source == "fallback":
        return
    try:
        rid = get_plan_store().save_record(
            owner_key, request.model_dump(mode="json"),
            outline_data.model_dump(mode="json"))
        outline_data.record_id = rid
        logger.info("🗂️  行程已持久化：record_id=%d（%s %d天）",
                    rid, request.city, request.travel_days)
    except Exception:  # noqa: BLE001
        logger.warning("🗂️  行程持久化失败（不影响主流程）", exc_info=True)


async def execute_outline(request: TripRequest, owner_key: str = "") -> TripOutlineData:
    outline_data = await get_trip_planner_agent().plan_outlines(request)
    persist_outline(owner_key, request, outline_data)
    return outline_data


async def execute_detail(detail_request: TripDetailRequest,
                         owner_key: str = "") -> TripPlan:
    request = detail_request.request
    store = get_plan_store() if (owner_key and detail_request.record_id) else None
    record_ok = False
    if store is not None:
        # 归属校验：record_id 必须属于该 owner，否则视为未关联（不持久化也不读库）
        record_ok = store.get_record(owner_key, detail_request.record_id) is not None
        if record_ok and not request.mock_llm:
            # 已持久化的详情直接回放：历史行程在缓存 TTL 过期后依然零成本可看
            stored = store.get_detail(detail_request.record_id,
                                      detail_request.outline.plan_id)
            if stored is not None:
                logger.info("🗂️  详情持久化命中：record_id=%d 方案%d（零 LLM 调用）",
                            detail_request.record_id, detail_request.outline.plan_id)
                # 旧版库存的预算不复活：预算改由后台任务按 24h 缓存现取
                stored.pop("budget", None)
                stored["source"] = "stored"
                return TripPlan(**stored)

    trip_plan = await get_trip_planner_agent().plan_detail(
        request, detail_request.outline)
    if record_ok and not request.mock_llm and trip_plan.source != "fallback":
        try:
            store.save_detail(detail_request.record_id,
                              detail_request.outline.plan_id,
                              detail_request.outline.style,
                              trip_plan.model_dump(mode="json"))
        except Exception:  # noqa: BLE001
            logger.warning("🗂️  详情持久化失败（不影响主流程）", exc_info=True)
    return trip_plan


async def execute_budget(budget_request: BudgetJobRequest,
                         owner_key: str = "") -> BudgetJobResult:
    """预算后台任务：先按路线账单查 24h 缓存（Redis/内存），未命中才调 LLM。

    预算只写短期缓存，绝不落 trip_details/plan_cache 等库表。
    """
    plan = budget_request.plan  # 缓存键只取账单字段，budget/source/日期均不参与
    cache = get_budget_cache()
    key = make_budget_key(plan, budget_request.request)
    loop = asyncio.get_running_loop()

    try:
        entry = await loop.run_in_executor(None, cache.get, key)
    except Exception:  # noqa: BLE001 - Redis 中途故障按缓存未命中处理（与 put 同约定）
        logger.warning("💰 预算缓存读取失败（按未命中继续）", exc_info=True)
        entry = None
    if entry is not None and entry.get("budget"):
        logger.info("💰 预算缓存命中：%s 方案%d（零 LLM 调用）",
                    plan.city, budget_request.plan_id)
        return BudgetJobResult(budget=Budget(**entry["budget"]), source="cache")

    budget, source = await get_trip_planner_agent().plan_budget(
        plan, budget_request.request)
    ttl_sec = max(1, get_settings().budget_cache_ttl_hours) * 3600
    entry = {"budget": budget.model_dump(mode="json"),
             "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        await loop.run_in_executor(None, cache.put, key, entry, ttl_sec)
    except Exception:  # noqa: BLE001 - 缓存写失败只丢性能不丢结果
        logger.warning("💰 预算缓存写入失败（不影响结果）", exc_info=True)
    return BudgetJobResult(budget=budget, source=source)
