# -*- coding: utf-8 -*-
"""企业级模块测试：输入校验 / 断路器 / 成本熔断 / 行程持久化 / 异步任务
（全 mock，零 token，不触网，不依赖 MySQL）"""
import asyncio
import json

import pytest
from pydantic import ValidationError

from app.models.schemas import TripRequest
from app.services import job_service, metrics_service
from app.services.cost_service import BudgetExceeded, ensure_budget
from app.services.llm_service import CircuitBreaker
from app.services.metrics_service import MemoryMetricsStore
from app.services.plan_store import MemoryPlanStore


def _req(**kw) -> dict:
    base = dict(city="北京", start_date="2026-03-01", end_date="2026-03-03",
                travel_days=3, transportation="公共交通", accommodation="经济型酒店")
    base.update(kw)
    return base


# ---------------- 输入校验 ----------------

class TestInputValidation:
    def test_xss_probe_city_rejected(self):
        """线上抓到过的真实 XSS 探针必须在边界被拒（短探针命中 <> 规则）"""
        with pytest.raises(ValidationError, match="非法字符"):
            TripRequest(**_req(city="<img onerror=x>"))

    def test_long_xss_probe_also_rejected(self):
        """日志里的真实探针全文（28 字符）—— 即使不含 <> 也过不了长度关"""
        with pytest.raises(ValidationError):
            TripRequest(**_req(city='<img src=x onerror=alert(1)>'))

    def test_control_chars_rejected(self):
        with pytest.raises(ValidationError, match="控制字符"):
            TripRequest(**_req(city="北京\x01"))

    def test_overlong_city_rejected(self):
        with pytest.raises(ValidationError, match="超长"):
            TripRequest(**_req(city="超" * 21))

    def test_too_many_preferences_rejected(self):
        with pytest.raises(ValidationError, match="过多"):
            TripRequest(**_req(preferences=["美食"] * 7))

    def test_free_text_capped(self):
        with pytest.raises(ValidationError, match="超长"):
            TripRequest(**_req(free_text_input="长" * 501))

    def test_bad_date_format_rejected(self):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            TripRequest(**_req(start_date="2026/03/01"))

    def test_date_order_rejected(self):
        with pytest.raises(ValidationError, match="开始日期不能晚于结束日期"):
            TripRequest(**_req(start_date="2026-03-05", end_date="2026-03-01"))

    def test_valid_request_normalized(self):
        req = TripRequest(**_req(city=" 北京 ", free_text_input="预算<2000，多博物馆"))
        assert req.city == "北京"  # strip 归一化
        assert "<2000" in req.free_text_input  # 自由文本允许 <>


# ---------------- 断路器 ----------------

class TestCircuitBreaker:
    def test_closed_below_threshold(self):
        b = CircuitBreaker(threshold=3, cooldown_seconds=100)
        b.record_failure()
        b.record_failure()
        assert b.can_try_primary()
        assert b.status()["state"] == "closed"

    def test_opens_at_threshold_and_blocks(self):
        b = CircuitBreaker(threshold=3, cooldown_seconds=100)
        for _ in range(3):
            b.record_failure()
        assert not b.can_try_primary()
        st = b.status()
        assert st["state"] == "open" and st["open_remaining_s"] > 0

    def test_success_resets(self):
        b = CircuitBreaker(threshold=3, cooldown_seconds=100)
        b.record_failure()
        b.record_failure()
        b.record_success()
        b.record_failure()
        b.record_failure()
        assert b.can_try_primary()  # 未达阈值（计数已被成功清零）

    def test_cooldown_zero_allows_half_open_probe(self):
        b = CircuitBreaker(threshold=1, cooldown_seconds=0)
        b.record_failure()  # 立即熔断，但冷却 0s → 立即可半开探测
        assert b.can_try_primary()


# ---------------- 成本熔断 ----------------

class TestCostGuard:
    def _metrics_with_tokens(self, monkeypatch, tokens: int, mock: bool = False):
        store = MemoryMetricsStore()
        from app.services.metrics_service import MetricsRecord
        store.record(MetricsRecord(endpoint="/api/trip/plan", phase="outline",
                                   status="success", latency_ms=1, mock=mock,
                                   total_tokens=tokens))
        monkeypatch.setattr(metrics_service, "_store", store)
        return store

    def test_budget_zero_means_unlimited(self, monkeypatch):
        self._metrics_with_tokens(monkeypatch, 10**9)
        monkeypatch.setattr("app.services.cost_service.get_settings",
                            lambda: type("S", (), {"daily_token_budget": 0})())
        ensure_budget()  # 不抛

    def test_over_budget_raises(self, monkeypatch):
        self._metrics_with_tokens(monkeypatch, 500)
        monkeypatch.setattr("app.services.cost_service.get_settings",
                            lambda: type("S", (), {"daily_token_budget": 100})())
        with pytest.raises(BudgetExceeded):
            ensure_budget()

    def test_mock_requests_bypass(self, monkeypatch):
        self._metrics_with_tokens(monkeypatch, 10**9)
        monkeypatch.setattr("app.services.cost_service.get_settings",
                            lambda: type("S", (), {"daily_token_budget": 1})())
        ensure_budget(mock=True)  # mock 压测不受预算拦截

    def test_mock_rows_not_counted(self, monkeypatch):
        self._metrics_with_tokens(monkeypatch, 10**9, mock=True)  # 用量全是 mock 行
        monkeypatch.setattr("app.services.cost_service.get_settings",
                            lambda: type("S", (), {"daily_token_budget": 1})())
        ensure_budget()  # tokens_today 只算非 mock，不抛


# ---------------- 行程持久化 ----------------

class TestPlanStore:
    def test_save_list_get_delete_flow(self):
        store = MemoryPlanStore()
        rid = store.save_record("owner-a", _req(), {"plans": [{"style": "经典全景"}],
                                                      "source": "llm"})
        assert rid == 1
        listed = store.list_records("owner-a")
        assert len(listed) == 1 and listed[0]["city"] == "北京"
        rec = store.get_record("owner-a", rid)
        assert rec["request"]["city"] == "北京" and rec["outlines"]["plans"]
        assert store.delete_record("owner-a", rid)
        assert store.get_record("owner-a", rid) is None

    def test_owner_isolation(self):
        store = MemoryPlanStore()
        rid = store.save_record("owner-a", _req(), {"plans": []})
        assert store.get_record("owner-b", rid) is None  # 别人的记录读不到
        assert store.list_records("owner-b") == []
        assert not store.delete_record("owner-b", rid)  # 也删不掉

    def test_detail_cascade_delete(self):
        store = MemoryPlanStore()
        rid = store.save_record("o", _req(), {"plans": []})
        store.save_detail(rid, 1, "经典全景", {"city": "北京", "days": []})
        assert store.get_detail(rid, 1)["city"] == "北京"
        store.delete_record("o", rid)
        assert store.get_detail(rid, 1) is None  # 详情随记录一起删


# ---------------- 异步任务 ----------------

class TestJobStore:
    def test_idempotent_create(self):
        store = job_service.MemoryJobStore()
        jid1, created1 = store.create("outline", "o", {}, "idem-1")
        jid2, created2 = store.create("outline", "o", {}, "idem-1")
        assert created1 and not created2 and jid1 == jid2  # 重复提交复用同一任务

    def test_fifo_and_transitions(self):
        store = job_service.MemoryJobStore()
        j1, _ = store.create("outline", "o", {}, "k1")
        j2, _ = store.create("outline", "o", {}, "k2")
        assert store.next_queued()["id"] == j1  # 先建先执行
        store.mark(j1, "running", trace_id="t1")
        assert store.next_queued()["id"] == j2
        store.mark(j1, "succeeded", result='{"ok": 1}')
        assert store.get(j1)["status"] == "succeeded"

    def test_requeue_interrupted(self):
        store = job_service.MemoryJobStore()
        j1, _ = store.create("outline", "o", {}, "k1")
        store.mark(j1, "running", trace_id="t")
        assert store.requeue_interrupted() == 1  # 进程重启：running 回 queued
        assert store.next_queued()["id"] == j1

    def test_owner_scoped_get(self):
        store = job_service.MemoryJobStore()
        j1, _ = store.create("outline", "owner-a", {}, "k1")
        assert store.get(j1, "owner-b") is None


class TestJobWorker:
    def test_run_one_mock_outline_succeeds(self, monkeypatch):
        """worker 执行 mock 大纲任务：零 token 跑通「入队→执行→落结果」全链路"""
        from app.services import cache_service
        from app.services.cache_service import MemoryCacheStore
        from app.services.job_service import MemoryJobStore

        store = MemoryJobStore()
        monkeypatch.setattr(job_service, "_store", store)
        # 坑 29：横切存储层全部打桩，防真实 MySQL 互染
        monkeypatch.setattr(metrics_service, "_store", MemoryMetricsStore())
        monkeypatch.setattr(cache_service, "_store", MemoryCacheStore())

        payload = _req(mock_llm=True, city="测试城")
        jid, _ = store.create("outline", "owner-x", payload, "idem-w1")
        job = store.get(jid)
        asyncio.run(job_service._run_one(job))

        done = store.get(jid)
        assert done["status"] == "succeeded"
        result = json.loads(done["result"])
        assert result["city"] == "测试城" and len(result["plans"]) == 4
        assert done["trace_id"]  # 任务执行有全链路 ID

    def test_run_one_failure_marks_failed(self, monkeypatch):
        store = job_service.MemoryJobStore()
        monkeypatch.setattr(job_service, "_store", store)
        jid, _ = store.create("outline", "o", {"bogus": True}, "idem-bad")
        asyncio.run(job_service._run_one(store.get(jid)))
        failed = store.get(jid)
        assert failed["status"] == "failed" and failed["error"]
