# -*- coding: utf-8 -*-
"""预算异步化 + 24h 缓存的离线测试（不触网、不调真实 LLM、不依赖 MySQL/Redis）"""
import asyncio
import copy
import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import trip_planner_agent as agent_mod
from app.agents.trip_planner_agent import TripPlannerService
from app.api.routes import jobs as jobs_routes
from app.models.schemas import (
    Budget,
    BudgetJobRequest,
    TripDetailRequest,
    TripPlan,
    TripRequest,
    OutlineDay,
    OutlineSpot,
    PlanOutline,
)
from app.services import cache_service, job_service, metrics_service
from app.services import budget_cache as bc
from app.services import trip_execution
from app.services.cache_service import MemoryCacheStore
from app.services.metrics_service import MemoryMetricsStore
from app.services.plan_store import MemoryPlanStore


# ---------------- 公共夹具 ----------------

def _plan_payload(**over) -> dict:
    """最小合法 TripPlan dict（详情已定稿、无预算）"""
    payload = {
        "city": "北京", "start_date": "2026-03-01", "end_date": "2026-03-02",
        "days": [
            {"date": "2026-03-01", "day_index": 0, "description": "第一天",
             "transportation": "公共交通", "accommodation": "经济型酒店",
             "hotel": {"name": "如家", "estimated_cost": 200},
             "attractions": [
                 {"name": "故宫", "visit_duration": 120, "description": "d",
                  "ticket_price": 60, "category": "景点"},
                 {"name": "景山", "visit_duration": 60, "description": "d",
                  "ticket_price": 2, "category": "景点"}],
             "meals": [{"type": "lunch", "name": "炸酱面", "estimated_cost": 40},
                       {"type": "dinner", "name": "烤鸭", "estimated_cost": 100}]},
            {"date": "2026-03-02", "day_index": 1, "description": "第二天",
             "transportation": "公共交通", "accommodation": "经济型酒店",
             "hotel": {"name": "如家", "estimated_cost": 200},
             "attractions": [{"name": "颐和园", "visit_duration": 180,
                              "description": "d", "ticket_price": 30}],
             "meals": [{"type": "lunch", "name": "包子", "estimated_cost": 30}]},
        ],
        "weather_info": [{"date": "2026-03-01", "day_weather": "晴"}],
        "overall_suggestions": "s", "budget": None, "source": "",
    }
    payload.update(over)
    return payload


def _plan(**over) -> TripPlan:
    return TripPlan(**_plan_payload(**over))


def _request(mock=True) -> TripRequest:
    return TripRequest(city="北京", start_date="2026-03-01", end_date="2026-03-02",
                       travel_days=2, transportation="公共交通",
                       accommodation="经济型酒店", client_tag="pytest",
                       mock_llm=mock)


@pytest.fixture(autouse=True)
def _memory_budget_cache(monkeypatch):
    """预算缓存统一打桩成内存：测试永不触碰真实 Redis"""
    cache = bc.MemoryBudgetCache()
    monkeypatch.setattr(bc, "_cache", cache)
    return cache


# ---------------- 1. 缓存键 ----------------

class TestBudgetKey:
    def test_stable_and_ignores_non_billing_fields(self):
        """同一路线：日期/天气/描述/坐标/source 变化不改键"""
        a = _plan()
        b = _plan(**json.loads(json.dumps(_plan_payload())))  # 深拷贝等价物
        assert bc.make_budget_key(a) == bc.make_budget_key(b)

        c_payload = _plan_payload(
            start_date="2026-07-01", end_date="2026-07-02",
            weather_info=[{"date": "2026-07-01", "day_weather": "暴雨"}],
            overall_suggestions="完全不同的建议", source="cache")
        c_payload["days"][0]["date"] = "2026-07-01"
        c_payload["days"][1]["date"] = "2026-07-02"
        c_payload["days"][0]["description"] = "另一段描述"
        c_payload["days"][0]["attractions"][0]["description"] = "改过的景点描述"
        c_payload["days"][0]["attractions"][0]["location"] = {
            "longitude": 116.4, "latitude": 39.9}
        assert bc.make_budget_key(TripPlan(**c_payload)) == bc.make_budget_key(a)

    def test_changes_on_billing_fields(self):
        """账单字段（票价/酒店价/餐饮价/交通方式/城市）变化必须换键"""
        base = bc.make_budget_key(_plan())

        pricier = _plan_payload()
        pricier["days"][0]["attractions"][0]["ticket_price"] = 61
        assert bc.make_budget_key(TripPlan(**pricier)) != base

        renamed = _plan_payload()
        renamed["days"][0]["hotel"]["name"] = "汉庭"
        assert bc.make_budget_key(TripPlan(**renamed)) != base

        assert bc.make_budget_key(_plan(city="上海")) != base

    def test_accepts_plain_dict(self):
        """dict 入参（模型 dump 出的等价 dict）与 TripPlan 对象算出同一键"""
        assert bc.make_budget_key(_plan().model_dump(mode="json")) == \
            bc.make_budget_key(_plan())

    def test_request_billing_fields_change_key(self):
        """请求级预算口径（交通/住宿偏好/自由文本）必须入键：公交 vs 打车不同预算"""
        plan = _plan()
        bus = _request(mock=False)
        taxi = bus.model_copy(update={"transportation": "打车"})
        flight = bus.model_copy(update={"free_text_input": "往返坐飞机"})

        assert bc.make_budget_key(plan, bus) == bc.make_budget_key(plan, bus)
        assert bc.make_budget_key(plan, taxi) != bc.make_budget_key(plan, bus)
        assert bc.make_budget_key(plan, flight) != bc.make_budget_key(plan, bus)
        # 不传 request 保持纯路线键（向后兼容）
        assert bc.make_budget_key(plan) != bc.make_budget_key(plan, bus)


# ---------------- 2. 缓存实现与降级 ----------------

class TestBudgetCacheImpl:
    def test_memory_cache_hit_and_expiry(self):
        cache = bc.MemoryBudgetCache()
        cache.put("k1", {"budget": {"total": 1}}, ttl_sec=60)
        assert cache.get("k1") == {"budget": {"total": 1}}
        cache.put("k2", {"budget": {"total": 2}}, ttl_sec=60)
        # 把过期时间戳直接拨到过去，验证惰性过期清除
        cache._rows[bc.KEY_PREFIX + "k2"] = (0, {"budget": {"total": 2}})
        assert cache.get("k2") is None
        assert (bc.KEY_PREFIX + "k2") not in cache._rows

    def test_singleton_falls_back_to_memory(self, monkeypatch):
        """redis 包导入/连接失败：单例降级内存实现，不抛错"""
        bc.reset_budget_cache()

        class _Boom:
            @staticmethod
            def from_url(*a, **kw):
                raise ConnectionError("redis 不通")

        monkeypatch.setitem(sys.modules, "redis", _Boom)
        cache = bc.get_budget_cache()
        assert isinstance(cache, bc.MemoryBudgetCache)
        assert bc.get_budget_cache() is cache  # 定型后不再重连
        bc.reset_budget_cache()

    def test_real_redis_roundtrip_if_available(self, monkeypatch):
        """本机 Redis 活着时走真 Redis（服务不在则跳过）"""
        pytest.importorskip("redis")
        try:
            cache = bc.RedisBudgetCache("redis://127.0.0.1:6379/1")
        except Exception:
            pytest.skip("本机 Redis 未运行")
        key = "pytest-probe"
        cache.put(key, {"budget": {"total": 99}}, ttl_sec=5)
        assert cache.get(key)["budget"]["total"] == 99
        cache._client.delete(bc.KEY_PREFIX + key)  # 清理探针


# ---------------- 3. execute_budget 缓存命中/未命中 ----------------

class TestExecuteBudget:
    def _job(self, plan=None, mock=True) -> BudgetJobRequest:
        return BudgetJobRequest(plan_id=1, request=_request(mock=mock),
                                plan=plan or _plan())

    def test_miss_calls_llm_and_writes_cache(self, monkeypatch, _memory_budget_cache):
        calls = []

        async def fake_plan_budget(plan, request=None):
            calls.append(1)
            return Budget(total=500, total_attractions=92), "llm"

        monkeypatch.setattr(trip_execution, "get_trip_planner_agent",
                            lambda: type("A", (), {"plan_budget": staticmethod(fake_plan_budget)})())

        result = asyncio.run(trip_execution.execute_budget(self._job(mock=False)))

        assert result.source == "llm" and result.budget.total == 500
        assert len(calls) == 1
        entry = _memory_budget_cache.get(
            bc.make_budget_key(_plan(), _request(mock=False)))
        assert entry and entry["budget"]["total"] == 500 and entry["created_at"]

    def test_cache_read_failure_treated_as_miss(self, monkeypatch):
        """Redis 中途故障（get 抛错）：按未命中继续，不拖垮预算任务"""

        class _BrokenCache:
            def get(self, key):
                raise ConnectionError("redis died")

            def put(self, key, entry, ttl_sec):
                raise ConnectionError("redis died")

        monkeypatch.setattr(trip_execution, "get_budget_cache", lambda: _BrokenCache())

        async def fake_plan_budget(plan, request=None):
            return Budget(total=100), "llm"

        monkeypatch.setattr(trip_execution, "get_trip_planner_agent",
                            lambda: type("A", (), {"plan_budget": staticmethod(fake_plan_budget)})())

        result = asyncio.run(trip_execution.execute_budget(self._job(mock=False)))
        assert result.source == "llm" and result.budget.total == 100

    def test_hit_skips_llm(self, monkeypatch, _memory_budget_cache):
        key = bc.make_budget_key(_plan(), _request(mock=False))
        _memory_budget_cache.put(key, {"budget": Budget(total=777).model_dump(),
                                       "created_at": "2026-10-24 00:00:00"}, ttl_sec=60)

        def _explode(*a, **kw):
            raise AssertionError("缓存命中不得调用 LLM")

        monkeypatch.setattr(trip_execution, "get_trip_planner_agent", _explode)

        result = asyncio.run(trip_execution.execute_budget(self._job(mock=False)))
        assert result.source == "cache" and result.budget.total == 777


# ---------------- 4. worker 执行 budget 任务 ----------------

class TestBudgetWorker:
    def test_run_one_budget_mock_succeeds(self, monkeypatch):
        """worker 执行 mock 预算任务：入队→执行→结果含预算，且行程库零写入"""
        store = job_service.MemoryJobStore()
        monkeypatch.setattr(job_service, "_store", store)
        monkeypatch.setattr(metrics_service, "_store", MemoryMetricsStore())
        monkeypatch.setattr(cache_service, "_store", MemoryCacheStore())
        plans = MemoryPlanStore()
        monkeypatch.setattr(trip_execution, "get_plan_store", lambda: plans)

        payload = self._payload()
        jid, _ = store.create("budget", "owner-x", payload, "idem-b1")
        asyncio.run(job_service._run_one(store.get(jid)))

        done = store.get(jid)
        assert done["status"] == "succeeded"
        result = json.loads(done["result"])
        assert result["source"] == "mock" and result["budget"]["total"] > 0

    @staticmethod
    def _payload() -> dict:
        return BudgetJobRequest(
            plan_id=2, record_id=None, request=_request(mock=True),
            plan=_plan()).model_dump(mode="json")


# ---------------- 5. /api/jobs 路由：校验 + 幂等 ----------------

class TestBudgetJobsApi:
    def _app(self, monkeypatch):
        store = job_service.MemoryJobStore()
        monkeypatch.setattr(job_service, "_store", store)
        app = FastAPI()
        app.include_router(jobs_routes.router, prefix="/api")
        return TestClient(app), store

    def test_malformed_budget_payload_422(self, monkeypatch):
        client, _ = self._app(monkeypatch)
        resp = client.post("/api/jobs", json={
            "kind": "budget", "payload": {"plan_id": 9},  # 缺 plan、plan_id 越界
            "idempotency_key": "client-uuid-1"})
        assert resp.status_code == 422

    def test_same_content_same_day_is_idempotent(self, monkeypatch):
        """同路线内容同天重复提交（哪怕客户端换了 uuid）命中同一任务"""
        client, store = self._app(monkeypatch)
        payload = TestBudgetWorker._payload()

        r1 = client.post("/api/jobs", json={
            "kind": "budget", "payload": payload, "idempotency_key": "uuid-aaaa"})
        r2 = client.post("/api/jobs", json={
            "kind": "budget", "payload": payload, "idempotency_key": "uuid-bbbb"})
        assert r1.status_code == 202 and r2.status_code == 202
        assert r1.json()["created"] is True
        assert r2.json()["created"] is False
        assert r1.json()["job_id"] == r2.json()["job_id"]
        # 服务端重写的幂等键带 budget- 前缀
        job = store.get(r1.json()["job_id"])
        assert job["idempotency_key"].startswith("budget-")

    def test_failed_budget_job_is_retryable(self, monkeypatch):
        """失败的预算任务可重试：重提同内容不命中死任务，另起新任务"""
        client, store = self._app(monkeypatch)
        payload = TestBudgetWorker._payload()

        r1 = client.post("/api/jobs", json={
            "kind": "budget", "payload": payload, "idempotency_key": "uuid-retry1"})
        jid1 = r1.json()["job_id"]
        store.mark(jid1, "failed", error="成本熔断")

        r2 = client.post("/api/jobs", json={
            "kind": "budget", "payload": payload, "idempotency_key": "uuid-retry2"})
        assert r2.json()["created"] is True
        assert r2.json()["job_id"] != jid1  # 新任务而非幂等命中死任务

    def test_different_content_creates_distinct_jobs(self, monkeypatch):
        client, _ = self._app(monkeypatch)
        payload_a = TestBudgetWorker._payload()
        payload_b = copy.deepcopy(payload_a)
        payload_b["plan"]["days"][0]["attractions"][0]["ticket_price"] = 99

        r1 = client.post("/api/jobs", json={
            "kind": "budget", "payload": payload_a, "idempotency_key": "uuid-cc1"})
        r2 = client.post("/api/jobs", json={
            "kind": "budget", "payload": payload_b, "idempotency_key": "uuid-cc1"})
        assert r1.json()["job_id"] != r2.json()["job_id"]


# ---------------- 6. mock 预算确定性求和 ----------------

class TestMockBudget:
    def test_mock_budget_sums_details(self, monkeypatch):
        class _ExplodingLLM:
            def invoke(self, *a, **kw):
                raise AssertionError("mock 预算不得调用 LLM")

        monkeypatch.setattr(agent_mod, "get_llm", lambda: _ExplodingLLM())
        monkeypatch.setattr(metrics_service, "_store", MemoryMetricsStore())

        service = TripPlannerService()
        budget, source = asyncio.run(service.plan_budget(_plan(), _request(mock=True)))

        # 门票 60+2+30=92；酒店 200*2=400；餐饮 40+100+30=170；交通 30*2 天=60
        assert source == "mock"
        assert budget.total_attractions == 92
        assert budget.total_hotels == 400
        assert budget.total_meals == 170
        assert budget.total_transportation == 60
        assert budget.total == 722


# ---------------- 7. prompt 契约 ----------------

class TestBudgetPrompt:
    def test_planner_prompt_no_longer_requires_budget(self):
        from app.services.prompt_service import get_prompt, reset_prompts
        reset_prompts()
        text, version = get_prompt("planner")
        assert version.startswith("planner.v2")
        assert "total_attractions" not in text  # 详情 prompt 不再要求预算汇总
        assert "不要输出预算汇总" in text

    def test_budget_prompt_versioned(self):
        from app.services.prompt_service import get_prompt, reset_prompts
        reset_prompts()
        text, version = get_prompt("budget")
        assert version.startswith("budget.v1")
        assert "total_transportation" in text

    def test_parse_budget_tolerates_wrapper(self):
        wrapped = '```json\n{"budget": {"total_attractions": 1, "total_hotels": 2, ' \
                  '"total_meals": 3, "total_transportation": 4, "total": 10}}\n```'
        budget = TripPlannerService._parse_budget(wrapped)
        assert budget.total == 10 and budget.total_meals == 3


# ---------------- 8. 详情持久化回放剥除旧预算 ----------------

class TestStoredReplayStripsBudget:
    def test_stored_detail_replay_has_no_budget(self, monkeypatch):
        """旧库 trip_details 里带 budget 的 payload：回放时剥除（预算只走后台缓存）"""
        plans = MemoryPlanStore()
        monkeypatch.setattr(trip_execution, "get_plan_store", lambda: plans)
        monkeypatch.setattr(metrics_service, "_store", MemoryMetricsStore())
        monkeypatch.setattr(cache_service, "_store", MemoryCacheStore())

        rid = plans.save_record("owner-x", _request(mock=False).model_dump(mode="json"),
                                {"plans": []})
        legacy = _plan_payload()
        legacy["budget"] = {"total_attractions": 92, "total_hotels": 400,
                            "total_meals": 170, "total_transportation": 60,
                            "total": 722}
        plans.save_detail(rid, 1, "经典全景", legacy)

        outline = PlanOutline(plan_id=1, style="经典全景", days=[
            OutlineDay(date="2026-03-01", day_index=0,
                       spots=[OutlineSpot(name="故宫")])])
        req = TripDetailRequest(request=_request(mock=False), outline=outline,
                                record_id=rid)

        plan = asyncio.run(trip_execution.execute_detail(req, "owner-x"))

        assert plan.source == "stored"
        assert plan.budget is None  # 旧预算不复活
