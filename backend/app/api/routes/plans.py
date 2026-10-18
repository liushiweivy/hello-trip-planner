# -*- coding: utf-8 -*-
"""历史行程端点：列表 / 回填详情 / 删除（按 X-Owner-Key 归属过滤）"""
import logging

from fastapi import APIRouter, Header, HTTPException

from ...services.plan_store import get_plan_store

logger = logging.getLogger("trip.api")

router = APIRouter(prefix="/plans", tags=["历史行程"])


def _owner(x_owner_key: str) -> str:
    owner = (x_owner_key or "").strip()[:64]
    if not owner:
        raise HTTPException(
            status_code=400,
            detail="缺少 X-Owner-Key 头（前端 localStorage client_id）")
    return owner


@router.get("", summary="我的历史行程列表")
async def list_plans(x_owner_key: str = Header(default="")):
    owner = _owner(x_owner_key)
    return {"success": True,
            "data": get_plan_store().list_records(owner),
            "backend": get_plan_store().backend}


@router.get("/{record_id}", summary="历史行程回填（请求快照 + 4 套大纲）")
async def get_plan(record_id: int, x_owner_key: str = Header(default="")):
    owner = _owner(x_owner_key)
    rec = get_plan_store().get_record(owner, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="行程不存在或不属于当前用户")
    return {"success": True, "data": rec}


@router.delete("/{record_id}", summary="删除历史行程（连带各方案详情）")
async def delete_plan(record_id: int, x_owner_key: str = Header(default="")):
    owner = _owner(x_owner_key)
    if not get_plan_store().delete_record(owner, record_id):
        raise HTTPException(status_code=404, detail="行程不存在或不属于当前用户")
    logger.info("🗑️  历史行程已删除：record_id=%d", record_id)
    return {"success": True, "message": "已删除"}
