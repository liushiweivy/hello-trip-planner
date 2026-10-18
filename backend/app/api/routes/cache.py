# -*- coding: utf-8 -*-
"""缓存管理：查看缓存状态 / 清理缓存（预制路线默认保护，需显式 include_seed）"""
import logging

from fastapi import APIRouter, Query

from ...services.cache_service import get_cache_store

logger = logging.getLogger("trip.api")

router = APIRouter(prefix="/cache", tags=["缓存"])


@router.get("/stats")
async def cache_stats():
    """缓存总览：条目数/分类/命中排行（预制路线 seed=1）"""
    store = get_cache_store()
    try:
        data = store.stats()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "backend": store.backend, "message": str(exc)}
    return {"success": True, "backend": store.backend, "data": data}


@router.post("/clear")
async def cache_clear(
    city: str = Query(default="", description="只清某城市"),
    kind: str = Query(default="", description="只清某类: outline/detail"),
    include_seed: bool = Query(default=False, description="连预制路线一起清（默认保护）"),
):
    """清理缓存：默认只清运行时缓存，预制路线需 include_seed=true"""
    store = get_cache_store()
    try:
        removed = store.clear(city=city, kind=kind, include_seed=include_seed)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "backend": store.backend, "message": str(exc)}
    logger.info("⚡ 缓存清理：city=%s kind=%s include_seed=%s → 删除 %d 条",
                city or "全部", kind or "全部", include_seed, removed)
    return {"success": True, "backend": store.backend, "data": {"removed": removed}}
