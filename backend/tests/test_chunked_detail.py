# -*- coding: utf-8 -*-
"""长行程分段详情 + 截断检测的离线测试（不触网、不调真实 LLM）"""
import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agents import trip_planner_agent as agent_mod
from app.agents.trip_planner_agent import DETAIL_CHUNK_DAYS
from app.models.schemas import OutlineDay, OutlineSpot, PlanOutline, TripRequest
from app.services import cache_service, metrics_service
from app.services.cache_service import MemoryCacheStore
from app.services.llm_service import _patch_invoke_with_usage, get_thread_last_usage
from app.services.metrics_service import MemoryMetricsStore


def _make_service(monkeypatch):
    class _ExplodingLLM:
        def invoke(self, *a, **kw):
            raise AssertionError("测试不得调用真实 LLM")

    monkeypatch.setattr(agent_mod, "get_llm", lambda: _ExplodingLLM())
    store = MemoryMetricsStore()
    monkeypatch.setattr(metrics_service, "_store", store)
    # 缓存也打桩成内存存储：否则真实 MySQL 缓存会让相邻测试互相命中
    monkeypatch.setattr(cache_service, "_store", MemoryCacheStore())
    return agent_mod.TripPlannerService(), store


def _request(days: int) -> TripRequest:
    end = (datetime(2026, 11, 1) + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    return TripRequest(
        city="深圳", start_date="2026-11-01", end_date=end,
        travel_days=days, transportation="公共交通", accommodation="经济型酒店",
        client_tag="pytest", mock_llm=False,
    )


def _outline(days: int) -> PlanOutline:
    return PlanOutline(
        plan_id=1, style="经典全景",
        days=[OutlineDay(
            date=(datetime(2026, 11, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
            day_index=i, title=f"第{i + 1}天",
            spots=[OutlineSpot(name=f"景点{i}", address="某地址")],
        ) for i in range(days)],
    )


def _block_json(day_indices, start="2026-11-01") -> str:
    base = datetime.strptime(start, "%Y-%m-%d")
    days = [{
        "date": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
        "day_index": i, "description": "测试日", "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "attractions": [{"name": f"景点{i}", "address": "addr", "location": None,
                         "visit_duration": 60, "description": "d", "ticket_price": 10}],
        "meals": [{"type": "lunch", "name": "饭", "estimated_cost": 20}],
    } for i in day_indices]
    n = len(day_indices)
    return json.dumps({
        "city": "深圳", "start_date": start,
        "end_date": (base + timedelta(days=max(day_indices))).strftime("%Y-%m-%d"),
        "days": days, "overall_suggestions": "分段建议",
    })


# ---------------- 截断检测与 usage 补丁 ----------------

def test_is_truncated():
    assert agent_mod.TripPlannerService._is_truncated({"finish_reason": "length"})
    assert not agent_mod.TripPlannerService._is_truncated({"finish_reason": "stop"})
    assert not agent_mod.TripPlannerService._is_truncated(None)


def test_invoke_patch_captures_finish_reason_and_budget():
    completions = SimpleNamespace(kwargs=None)

    def fake_create(**kwargs):
        completions.kwargs = kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3,
                                  completion_tokens_details=None),
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"),
                                     finish_reason="length")],
        )

    fake_llm = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))),
        model="m", temperature=0.3, max_tokens=24576,
    )
    _patch_invoke_with_usage(fake_llm)

    out = fake_llm.invoke([{"role": "user", "content": "hi"}], max_tokens=123)
    assert out == "{}"
    usage = get_thread_last_usage()
    assert usage["total_tokens"] == 3
    assert usage["finish_reason"] == "length"
    assert completions.kwargs["max_tokens"] == 123  # 自定义预算透传到底层 client


# ---------------- 分段详情 ----------------

def test_chunked_detail_merges_blocks(monkeypatch):
    """7 天行程 → 2 段（5+2）生成，合并后 7 天有序、预算恒为 None、按块记账"""
    service, store = _make_service(monkeypatch)
    monkeypatch.setattr(service, "_gather_hotels_weather",
                        lambda req: asyncio.sleep(0, result=([], [])))

    calls = []

    async def fake_timed(system_prompt, prompt, max_tokens=None):
        calls.append(prompt)
        idxs = list(range(0, DETAIL_CHUNK_DAYS)) if len(calls) == 1 else [5, 6]
        return _block_json(idxs), {"total_tokens": 10, "finish_reason": "stop"}, 100, 0

    monkeypatch.setattr(service, "_timed_llm", fake_timed)

    plan = asyncio.run(service.plan_detail(_request(7), _outline(7)))

    assert len(calls) == 2  # 5+2 两段
    assert [d.day_index for d in plan.days] == list(range(7))
    assert [a.name for a in plan.days[6].attractions] == ["景点6"]
    # 预算已移出详情阶段（后台 budget 任务单独生成）
    assert plan.budget is None
    assert plan.overall_suggestions == "分段建议"

    phases = [r["phase"] for r in store.recent()]
    assert sorted(phases) == ["detail_b1", "detail_b2"]
    assert all(r["status"] == "success" for r in store.recent())


def test_chunked_detail_bad_block_degrades_only_that_block(monkeypatch):
    """某段两次都返回坏 JSON：只该段降级兜底，其余段保留真实结果"""
    service, store = _make_service(monkeypatch)
    monkeypatch.setattr(service, "_gather_hotels_weather",
                        lambda req: asyncio.sleep(0, result=([], [])))

    calls = []

    async def fake_timed(system_prompt, prompt, max_tokens=None):
        calls.append(prompt)
        if len(calls) == 1:
            return _block_json(list(range(0, DETAIL_CHUNK_DAYS))), {"finish_reason": "stop"}, 100, 0
        return "这不是 JSON", {"finish_reason": "stop"}, 100, 0  # 第 2 段两次都坏

    monkeypatch.setattr(service, "_timed_llm", fake_timed)

    plan = asyncio.run(service.plan_detail(_request(7), _outline(7)))

    assert len(calls) == 3  # 段1 一次成 + 段2 首次+修复重试
    assert [d.day_index for d in plan.days] == list(range(7))
    assert plan.days[0].attractions[0].name == "景点0"          # 段1 真实
    assert "推荐景点" in plan.days[5].attractions[0].name        # 段2 已兜底
    assert plan.budget is None                                  # 详情阶段不再有预算

    rows = {r["phase"]: r["status"] for r in store.recent()}
    assert rows["detail_b1"] == "success"
    assert rows["detail_b2"] == "error"
    assert rows["detail_b2_repair"] == "error"


def test_truncated_call_recorded_and_retried_with_bigger_budget(monkeypatch):
    """finish_reason=length：首次记 truncated，重试带 32K 预算"""
    service, store = _make_service(monkeypatch)
    monkeypatch.setattr(service, "_gather_hotels_weather",
                        lambda req: asyncio.sleep(0, result=([], [])))

    budgets = []

    async def fake_timed(system_prompt, prompt, max_tokens=None):
        budgets.append(max_tokens)
        if len(budgets) == 1:
            return '{"days": [', {"finish_reason": "length"}, 100, 0  # 截断
        return _block_json([0, 1]), {"finish_reason": "stop"}, 100, 0

    monkeypatch.setattr(service, "_timed_llm", fake_timed)

    plan = asyncio.run(service.plan_detail(_request(2), _outline(2)))

    assert budgets[0] is None  # 首次用默认预算
    assert budgets[1] == agent_mod.TRUNCATED_RETRY_MAX_TOKENS  # 截断重试加预算
    assert len(plan.days) == 2
    rows = {r["phase"]: r["status"] for r in store.recent()}
    assert rows["detail"] == "truncated"
    assert rows["detail_repair"] == "success"
