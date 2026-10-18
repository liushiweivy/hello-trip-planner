# -*- coding: utf-8 -*-
"""成本与 LLM 供应商状态端点"""
from fastapi import APIRouter

from ...services.cost_service import cost_status

router = APIRouter(prefix="/cost", tags=["cost"])


@router.get("/status")
async def get_cost_status():
    """当日 token 预算用量 + 断路器/备用供应商状态（压测台预算卡数据源）"""
    return cost_status()
