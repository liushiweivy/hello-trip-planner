# -*- coding: utf-8 -*-
"""监控查询 API：给压测/监控页提供耗时与 token 数据"""

import logging

from fastapi import APIRouter, Query

from ...services.metrics_service import get_metrics_store

logger = logging.getLogger("trip.metrics_api")

router = APIRouter(prefix="/metrics", tags=["监控"])


@router.get("/recent", summary="最近的 LLM 调用记录")
async def recent(
    limit: int = Query(default=50, ge=1, le=500),
    client_tag: str = Query(default="", description="按批次过滤"),
    mock: str = Query(default="all", pattern="^(all|mock|real)$"),
):
    store = get_metrics_store()
    return {
        "success": True,
        "backend": store.backend,
        "data": store.recent(limit=limit, client_tag=client_tag, mock=mock),
    }


@router.get("/summary", summary="聚合摘要：成功率/耗时分位/token 合计")
async def summary(
    client_tag: str = Query(default="", description="按批次过滤"),
    mock: str = Query(default="all", pattern="^(all|mock|real)$"),
):
    store = get_metrics_store()
    return {
        "success": True,
        "backend": store.backend,
        "data": store.summary(client_tag=client_tag, mock=mock),
    }


@router.get("/batches", summary="压测批次列表")
async def batches(limit: int = Query(default=20, ge=1, le=100)):
    store = get_metrics_store()
    return {
        "success": True,
        "backend": store.backend,
        "data": store.batches(limit=limit),
    }
