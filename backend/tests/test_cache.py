# -*- coding: utf-8 -*-
"""规划缓存 + 预制路线的离线测试（不触网、不调真实 LLM、不依赖 MySQL）"""
import asyncio
import json

import pytest

from app.agents import trip_planner_agent as agent_mod
from app.agents.trip_planner_agent import TripPlannerService
from app.models.schemas import OutlineDay, OutlineSpot, PlanOutline, TripRequest
from app.services import cache_service, metrics_service
from app.services.cache_service import (
    MemoryCacheStore,
    make_detail_key,
    make_outline_key,
    rebase_outline_payload,
    rebase_plan_payload,
    seed_eligible,
)
from app.services.metrics_service import MemoryMetricsStore


def _make_service(monkeypatch):
    """规划服务：LLM 一触即炸 + 缓存/监控都用内存存储 + 关高德"""
    class _ExplodingLLM:
        def invoke(self, *a, **kw):
            raise AssertionError("测试不得调用真实 LLM")

    monkeypatch.setattr(agent_mod, "get_llm", lambda: _ExplodingLLM())
    cache = MemoryCacheStore()
    metrics = MemoryMetricsStore()
    monkeypatch.setattr(cache_service, "_store", cache)
    monkeypatch.setattr(metrics_service, "_store", metrics)
    service = TripPlannerService()
    monkeypatch.setattr(service.settings, "amap_api_key", "")  # 关高德：不触网
    return service, cache, metrics


def _request(city="北京", days=3, start="2026-03-01", **kw) -> TripRequest:
    from datetime import datetime, timedelta
    end = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    base = dict(city=city, start_date=start, end_date=end, travel_days=days,
                transportation="公共交通", accommodation="经济型酒店",
                client_tag="pytest", mock_llm=False)
    base.update(kw)
    return TripRequest(**base)


def _outline_plans_payload(days=3):
    return {"plans": [{
        "plan_id": 1, "style": "经典全景", "tagline": "t", "highlights": ["h"],
        "estimated_budget": 100,
        "days": [{"date": "", "day_index": i, "title": f"第{i + 1}天",
                  "spots": [{"name": f"景点{i}", "address": "addr", "category": "景点"}]}
                 for i in range(days)],
    }]}


# ---------------- 缓存键归一化 ----------------

def test_outline_key_ignores_dates_and_normalizes():
    """日期/偏好顺序/空白差异不改变缓存键；城市/天数/自由文本改变则变"""
    a = _request(start="2026-03-01", preferences=["美食", "历史文化"], free_text_input="  慢 一点 ")
    b = _request(start="2026-06-01", preferences=["历史文化", "美食"], free_text_input="慢 一点")
    assert make_outline_key(a) == make_outline_key(b)

    assert make_outline_key(_request(city="上海")) != make_outline_key(a)
    assert make_outline_key(_request(days=4)) != make_outline_key(a)
    assert make_outline_key(_request(free_text_input="")) != make_outline_key(a)


def test_detail_key_distinguishes_outlines():
    """同一请求下不同大纲（风格/选点）必须分开缓存"""
    req = _request()
    o1 = PlanOutline(plan_id=1, style="经典全景", days=[
        OutlineDay(date="2026-03-01", day_index=0, spots=[OutlineSpot(name="故宫")])])
    o2 = PlanOutline(plan_id=2, style="深度慢游", days=[
        OutlineDay(date="2026-03-01", day_index=0, spots=[OutlineSpot(name="故宫")])])
    o3 = PlanOutline(plan_id=1, style="经典全景", days=[
        OutlineDay(date="2026-03-01", day_index=0, spots=[OutlineSpot(name="颐和园")])])
    assert make_detail_key(req, o1) != make_detail_key(req, o2)
    assert make_detail_key(req, o1) != make_detail_key(req, o3)


def test_seed_eligible_rules():
    assert seed_eligible(_request())
    assert not seed_eligible(_request(preferences=["美食"]))
    assert not seed_eligible(_request(free_text_input="带老人"))


# ---------------- 内存存储行为 ----------------

def test_memory_store_put_get_touch_ttl_and_clear():
    store = MemoryCacheStore()
    store.put("k1", "outline", "测试城", 3, None, {"plans": []}, ttl_days=7)
    entry = store.get("k1")
    assert entry and entry["payload"] == {"plans": []}
    store.touch("k1")
    assert store.get("k1")["hit_count"] == 1

    store.put("k2", "outline", "测试城", 3, None, {"plans": []}, ttl_days=-1)  # 已过期
    assert store.get("k2") is None

    removed = store.clear(city="测试城")
    assert removed >= 1  # k1 必清；过期的 k2 也一并扫掉属正常
    assert store.get("k1") is None


def test_memory_store_seeds_loaded_and_protected():
    store = MemoryCacheStore()
    entry = store.get_seed_outline("北京", 3)
    assert entry and entry["seed"] == 1
    assert len(entry["payload"]["plans"]) == 4  # 每城 4 套风格
    # 预制路线默认受保护，include_seed 才能清
    assert store.clear(include_seed=False) == 0
    assert store.get_seed_outline("北京", 3) is not None


# ---------------- 日期重排 ----------------

def test_rebase_outline_payload_shifts_dates_by_day_index():
    plans = rebase_outline_payload(_outline_plans_payload(3)["plans"], "2026-05-10")
    assert [d["date"] for d in plans[0]["days"]] == ["2026-05-10", "2026-05-11", "2026-05-12"]
    # 纯函数：入参不被修改
    assert _outline_plans_payload(1)["plans"][0]["days"][0]["date"] == ""


def test_rebase_plan_payload_shifts_dates_and_range():
    plan = {"city": "北京", "start_date": "2026-01-01", "end_date": "2026-01-02",
            "days": [{"date": "2026-01-01", "day_index": 0},
                     {"date": "2026-01-02", "day_index": 1}],
            "weather_info": [], "overall_suggestions": "s"}
    out = rebase_plan_payload(plan, "2026-08-01", "2026-08-02")
    assert out["start_date"] == "2026-08-01"
    assert [d["date"] for d in out["days"]] == ["2026-08-01", "2026-08-02"]
    assert plan["days"][0]["date"] == "2026-01-01"  # 入参不变


# ---------------- 大纲缓存命中路径 ----------------

def test_outline_cache_hit_skips_llm_and_rebases(monkeypatch):
    """精确键命中：零 LLM 调用，日期按请求重排，记 cache_hit"""
    service, cache, metrics = _make_service(monkeypatch)
    req = _request(start="2026-03-01")
    cache.put(make_outline_key(req), "outline", "北京", 3, None,
              _outline_plans_payload(3), ttl_days=7)

    data = asyncio.run(service.plan_outlines(req))

    assert data.source == "cache"
    assert len(data.plans) == 1
    assert [d.date for d in data.plans[0].days] == ["2026-03-01", "2026-03-02", "2026-03-03"]
    assert cache.get(make_outline_key(req))["hit_count"] == 1
    rows = metrics.recent()
    assert len(rows) == 1 and rows[0]["status"] == "cache_hit" and rows[0]["phase"] == "outline"


def test_outline_seed_hit_skips_llm(monkeypatch):
    """预制路线命中：北京 3 天无偏好 → 4 套大纲秒开，零 LLM 调用"""
    service, cache, metrics = _make_service(monkeypatch)
    req = _request(city="北京", days=3, start="2026-07-01")

    data = asyncio.run(service.plan_outlines(req))

    assert data.source == "seed"
    assert len(data.plans) == 4
    assert {p.style for p in data.plans} == {"经典全景", "深度慢游", "经济实惠", "自然风光"}
    assert data.plans[0].days[0].date == "2026-07-01"
    assert data.plans[0].days[0].spots[0].name  # 真实景点名而非兜底占位
    assert metrics.recent()[0]["status"] == "cache_hit"


def test_outline_with_preferences_bypasses_seed(monkeypatch):
    """有偏好时不适用预制路线：走到 LLM 路径（mock 掉采集与 LLM）并写缓存"""
    service, cache, metrics = _make_service(monkeypatch)
    req = _request(city="北京", days=3, preferences=["美食"])
    monkeypatch.setattr(service, "_gather_context",
                        lambda r: asyncio.sleep(0, result=([], [], [])))

    async def fake_llm(system_prompt, prompt, max_tokens=None):
        return json.dumps(_outline_plans_payload(3), ensure_ascii=False), {
            "total_tokens": 10, "finish_reason": "stop"}, 100, 0

    monkeypatch.setattr(service, "_timed_llm", fake_llm)

    data = asyncio.run(service.plan_outlines(req))

    assert data.source == ""  # LLM 新生成
    assert cache.get(make_outline_key(req)) is not None  # 成功后已入缓存


# ---------------- 详情缓存命中路径 ----------------

def _detail_payload():
    return {
        "city": "北京", "start_date": "2026-01-01", "end_date": "2026-01-03",
        "days": [{"date": "2026-01-01", "day_index": 0, "description": "d",
                  "transportation": "公共交通", "accommodation": "经济型酒店",
                  "attractions": [{"name": "故宫", "address": "a", "location": None,
                                   "visit_duration": 120, "description": "d",
                                   "ticket_price": 60}],
                  "meals": [{"type": "lunch", "name": "饭", "estimated_cost": 50}]}],
        "weather_info": [], "overall_suggestions": "s", "budget": None, "source": "",
    }


def _detail_outline() -> PlanOutline:
    return PlanOutline(plan_id=1, style="经典全景", days=[
        OutlineDay(date="2026-03-01", day_index=0,
                   spots=[OutlineSpot(name="故宫", address="a")])])


def test_detail_cache_hit_skips_llm_and_rebases(monkeypatch):
    service, cache, metrics = _make_service(monkeypatch)
    req = _request(start="2026-03-01")
    outline = _detail_outline()
    cache.put(make_detail_key(req, outline), "detail", "北京", 3, 1,
              _detail_payload(), ttl_days=7)

    plan = asyncio.run(service.plan_detail(req, outline))

    assert plan.source == "cache"
    assert plan.start_date == "2026-03-01"
    assert plan.days[0].date == "2026-03-01"
    assert plan.days[0].attractions[0].name == "故宫"
    rows = metrics.recent()
    assert rows[0]["status"] == "cache_hit" and rows[0]["phase"] == "detail"


def test_degraded_chunked_detail_not_cached(monkeypatch):
    """分段详情有降级块：质量不完整，不入缓存（下次重新生成）"""
    service, cache, metrics = _make_service(monkeypatch)
    days = 7
    req = _request(days=days, city="深圳", start="2026-11-01")
    outline = PlanOutline(plan_id=1, style="经典全景", days=[
        OutlineDay(date=f"2026-11-0{i + 1}", day_index=i, title=f"第{i + 1}天",
                   spots=[OutlineSpot(name=f"景点{i}", address="a")])
        for i in range(days)])
    monkeypatch.setattr(service, "_gather_hotels_weather",
                        lambda r: asyncio.sleep(0, result=([], [])))

    calls = []

    async def fake_llm(system_prompt, prompt, max_tokens=None):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps(_detail_payload(), ensure_ascii=False), {
                "finish_reason": "stop"}, 100, 0
        return "这不是 JSON", {"finish_reason": "stop"}, 100, 0  # 第 2 段两次都坏

    monkeypatch.setattr(service, "_timed_llm", fake_llm)

    plan = asyncio.run(service.plan_detail(req, outline))

    assert plan is not None
    assert cache.get(make_detail_key(req, outline)) is None  # 降级不入缓存
