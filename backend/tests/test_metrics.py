# -*- coding: utf-8 -*-
"""监控系统离线测试：聚合逻辑 / 内存存储 / usage 提取 / mock 压测路径。

全部不触网、不调真实 LLM（零 token 消耗）。
"""
import asyncio

import pytest

from app.models.schemas import PlanOutline, TripRequest
from app.services import metrics_service
from app.services.llm_service import _extract_usage
from app.services.metrics_service import (
    MemoryMetricsStore,
    MetricsRecord,
    missing_migration_columns,
    summarize_rows,
)


def _rec(**kw) -> MetricsRecord:
    base = dict(endpoint="/api/trip/plan", phase="outline", status="success",
                latency_ms=1000)
    base.update(kw)
    return MetricsRecord(**base)


# ---------------- usage 提取 ----------------

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_extract_usage_from_object_with_reasoning():
    usage = _Obj(prompt_tokens=100, completion_tokens=50, total_tokens=150,
                 completion_tokens_details=_Obj(reasoning_tokens=30))
    assert _extract_usage(usage) == {
        "prompt_tokens": 100, "completion_tokens": 50,
        "total_tokens": 150, "reasoning_tokens": 30,
    }


def test_extract_usage_from_dict_without_details():
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert _extract_usage(usage) == {
        "prompt_tokens": 10, "completion_tokens": 5,
        "total_tokens": 15, "reasoning_tokens": None,
    }


def test_extract_usage_none():
    assert _extract_usage(None) is None


# ---------------- 聚合 ----------------

def test_summarize_rows_aggregation():
    rows = [
        {"phase": "outline", "status": "success", "latency_ms": 100,
         "prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": 2,
         "total_tokens": 15},
        {"phase": "outline", "status": "error", "latency_ms": 300,
         "prompt_tokens": None, "completion_tokens": None,
         "reasoning_tokens": None, "total_tokens": None},
        {"phase": "detail", "status": "retry_success", "latency_ms": 200,
         "prompt_tokens": 20, "completion_tokens": 10, "reasoning_tokens": 6,
         "total_tokens": 30},
    ]
    s = summarize_rows(rows)
    assert s["count"] == 3
    assert s["success"] == 2  # error 不算成功，retry_success 算
    assert s["success_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["latency_avg_ms"] == 200
    assert s["latency_p50_ms"] == 200
    assert s["latency_max_ms"] == 300
    assert s["prompt_tokens"] == 30
    assert s["completion_tokens"] == 15
    assert s["reasoning_tokens"] == 8
    assert s["total_tokens"] == 45
    assert s["by_phase"]["outline"]["count"] == 2
    assert s["by_phase"]["detail"]["tokens"] == 30


def test_summarize_rows_empty():
    s = summarize_rows([])
    assert s["count"] == 0 and s["success_rate"] == 0.0
    assert s["latency_p95_ms"] == 0 and s["total_tokens"] == 0
    assert s["by_phase"] == {} and s["by_status"] == {} and s["by_city"] == {}
    assert s["reasoning_share"] == 0.0


def test_summarize_rows_categories_phase_status_city():
    rows = [
        {"phase": "outline", "status": "success", "latency_ms": 100,
         "prompt_tokens": 10, "completion_tokens": 10, "reasoning_tokens": 4,
         "total_tokens": 20, "city": "北京"},
        {"phase": "outline", "status": "truncated", "latency_ms": 500,
         "prompt_tokens": 10, "completion_tokens": 20, "reasoning_tokens": 16,
         "total_tokens": 30, "city": "北京"},
        {"phase": "detail", "status": "success", "latency_ms": 300,
         "prompt_tokens": 30, "completion_tokens": 30, "reasoning_tokens": 10,
         "total_tokens": 60, "city": "上海"},
    ]
    s = summarize_rows(rows)

    # by_phase：次数/成败/四类 token/平均与最大耗时/截断数/推理占比
    op = s["by_phase"]["outline"]
    assert op["count"] == 2 and op["success"] == 1
    assert op["success_rate"] == 0.5
    assert op["latency_avg_ms"] == 300 and op["latency_max"] == 500
    assert op["prompt_tokens"] == 20 and op["completion_tokens"] == 30
    assert op["reasoning_tokens"] == 20 and op["tokens"] == 50
    assert op["truncated"] == 1
    assert op["reasoning_share"] == pytest.approx(20 / 30, abs=1e-3)

    # by_status：结局分布（截断单列，压测时一眼看出预算问题）
    assert s["by_status"] == {"success": 2, "truncated": 1}

    # by_city：多城市压测对比
    assert s["by_city"]["北京"] == {"count": 2, "success": 1, "tokens": 50,
                                   "latency_avg_ms": 300, "success_rate": 0.5}
    assert s["by_city"]["上海"]["count"] == 1

    # 顶层推理占比：30 / 60
    assert s["reasoning_share"] == pytest.approx(0.5, abs=1e-3)


def test_missing_migration_columns_diff():
    # 老表（无新列）→ 全量缺
    missing = missing_migration_columns({"id", "endpoint", "city"})
    names = [n for n, _ in missing]
    assert names == ["finish_reason", "max_tokens", "attempt",
                     "queue_ms", "prefs", "prompt_excerpt", "trace_id"]
    # 新表（列齐）→ 不缺
    assert missing_migration_columns(set(names) | {"id"}) == []
    # 部分补过 → 只补缺的那几列
    partial = missing_migration_columns({"finish_reason", "queue_ms"})
    assert [n for n, _ in partial] == ["max_tokens", "attempt", "prefs",
                                       "prompt_excerpt", "trace_id"]
    # DDL 片段合法（_ensure_schema 拼成 ALTER TABLE ... ADD COLUMN {name} {ddl}）
    assert all(ddl and "(" in ddl or "INT" in ddl for _, ddl in missing)


def test_record_new_fields_defaults_and_values():
    # 默认：老构造方式不受影响
    r = _rec()
    assert r.finish_reason == "" and r.max_tokens is None
    assert r.attempt == 1 and r.queue_ms == 0
    assert r.prefs == "" and r.prompt_excerpt == ""
    row = r.to_row()
    assert row["attempt"] == 1 and row["queue_ms"] == 0
    # 赋值后随 row 输出（压测历史列）
    r2 = _rec(finish_reason="length", max_tokens=16384, attempt=2,
              queue_ms=1500, prefs="美食、亲子", prompt_excerpt="x" * 300)
    row2 = r2.to_row()
    assert row2["finish_reason"] == "length" and row2["max_tokens"] == 16384
    assert row2["attempt"] == 2 and row2["queue_ms"] == 1500
    assert row2["prefs"] == "美食、亲子"
    assert len(row2["prompt_excerpt"]) == 300


# ---------------- 内存存储 ----------------

def test_memory_store_filters_by_batch_prefix_and_mock():
    store = MemoryMetricsStore()
    store.record(_rec(client_tag="b1|u1", mock=True))
    store.record(_rec(client_tag="b1|u2", mock=True, phase="detail"))
    store.record(_rec(client_tag="b2", mock=False))

    assert len(store.recent()) == 3
    # 批次前缀过滤：b1 命中 b1|u1、b1|u2
    assert len(store.recent(client_tag="b1")) == 2
    assert len(store.recent(client_tag="b1", mock="real")) == 0
    assert len(store.recent(mock="mock")) == 2
    assert len(store.recent(mock="real")) == 1

    s = store.summary(client_tag="b1")
    assert s["count"] == 2
    assert s["by_phase"]["detail"]["count"] == 1


def test_memory_store_batches_strip_user_suffix():
    store = MemoryMetricsStore()
    store.record(_rec(client_tag="lt-1|u1"))
    store.record(_rec(client_tag="lt-1|u2"))
    store.record(_rec(client_tag="lt-2"))
    store.record(_rec(client_tag=""))  # 空 tag 不进批次列表

    batches = {b["client_tag"]: b["calls"] for b in store.batches()}
    assert batches == {"lt-1": 2, "lt-2": 1}


# ---------------- 请求模型 ----------------

def test_trip_request_accepts_client_tag_and_mock():
    req = TripRequest(
        city="北京", start_date="2026-11-01", end_date="2026-11-02",
        travel_days=2, transportation="公共交通", accommodation="经济型酒店",
        client_tag="lt-x|u3", mock_llm=True,
    )
    assert req.client_tag == "lt-x|u3"
    assert req.mock_llm is True
    # 默认关闭，不影响正常请求
    req2 = TripRequest(
        city="北京", start_date="2026-11-01", end_date="2026-11-02",
        travel_days=2, transportation="公共交通", accommodation="经济型酒店",
    )
    assert req2.mock_llm is False and req2.client_tag == ""


# ---------------- mock 压测路径（零 token） ----------------

def _make_service(monkeypatch):
    """构造不碰真实 LLM 的 service；LLM 一旦被调用立即炸"""
    from app.agents import trip_planner_agent as agent_mod

    class _ExplodingLLM:
        def invoke(self, *a, **kw):
            raise AssertionError("mock 模式不得调用真实 LLM")

    monkeypatch.setattr(agent_mod, "get_llm", lambda: _ExplodingLLM())
    # 压测不该真睡 1~3 秒
    monkeypatch.setattr(agent_mod.random, "uniform", lambda a, b: 0.01)
    store = MemoryMetricsStore()
    monkeypatch.setattr(metrics_service, "_store", store)
    return agent_mod.TripPlannerService(), store


def _mock_request(tag: str = "pytest|u1") -> TripRequest:
    return TripRequest(
        city="北京", start_date="2026-11-01", end_date="2026-11-02",
        travel_days=2, transportation="公共交通", accommodation="经济型酒店",
        client_tag=tag, mock_llm=True,
    )


def test_mock_plan_outlines_returns_4_styles_without_llm(monkeypatch):
    service, store = _make_service(monkeypatch)
    data = asyncio.run(service.plan_outlines(_mock_request()))

    assert len(data.plans) == 4
    styles = [p.style for p in data.plans]
    assert len(set(styles)) == 4  # 风格互不相同
    assert data.city == "北京"

    rows = store.recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["phase"] == "outline" and row["status"] == "success"
    assert row["mock"] == 1 and row["client_tag"] == "pytest|u1"
    assert row["latency_ms"] >= 0


def test_mock_plan_detail_without_llm(monkeypatch):
    service, store = _make_service(monkeypatch)
    req = _mock_request()
    outline = service._create_fallback_outlines(req)[0]
    assert isinstance(outline, PlanOutline)

    plan = asyncio.run(service.plan_detail(req, outline))

    assert plan.city == "北京"
    assert len(plan.days) == req.travel_days

    rows = store.recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["phase"] == "detail" and row["status"] == "success"
    assert row["mock"] == 1
    assert row["plan_id"] == outline.plan_id
    assert row["plan_style"] == outline.style
