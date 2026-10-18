"""旅行规划 API 路由"""

import logging
import time

from fastapi import APIRouter, Header, HTTPException

from ...config import get_settings
from ...models.schemas import (
    TripDetailRequest,
    TripOutlineResponse,
    TripPlanResponse,
    TripRequest,
)
from ...services.cost_service import BudgetExceeded
from ...services.trip_execution import execute_detail, execute_outline

logger = logging.getLogger("trip.api")

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",
    response_model=TripOutlineResponse,
    summary="生成旅行方案大纲",
    description="阶段一：结合高德实时数据与 LLM 一次生成 4 套固定风格（经典全景/深度慢游/经济实惠/自然风光）的方案大纲（轻量，速度快）",
)
async def plan_trip(request: TripRequest, x_owner_key: str = Header(default="")):
    """生成 4 套旅行方案大纲"""
    t0 = time.monotonic()
    logger.info("📥 方案大纲请求: %s | %s~%s | %d天 | 偏好: %s%s",
                request.city, request.start_date, request.end_date,
                request.travel_days, request.preferences,
                f" | 补充: {request.free_text_input}" if request.free_text_input else "")
    try:
        outline_data = await execute_outline(request, x_owner_key.strip())
        logger.info("📤 方案大纲响应: %s 成功（%d 套），接口总耗时 %.1fs",
                    request.city, len(outline_data.plans), time.monotonic() - t0)
        return TripOutlineResponse(success=True, message="方案大纲生成成功", data=outline_data)

    except BudgetExceeded as exc:
        logger.warning("💰 方案大纲被成本熔断拦截: %s（%s）", request.city, exc)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("❌ 方案大纲失败: %s（耗时 %.1fs）", request.city, time.monotonic() - t0)
        raise HTTPException(status_code=500, detail=f"生成方案大纲失败: {exc}") from exc


@router.post(
    "/detail",
    response_model=TripPlanResponse,
    summary="生成选定方案的完整详情",
    description="阶段二：把用户选定的方案大纲扩写成完整旅行计划（景点锁定，只补详情）",
)
async def plan_detail(detail_request: TripDetailRequest,
                      x_owner_key: str = Header(default="")):
    """生成选定方案的完整详情"""
    t0 = time.monotonic()
    request = detail_request.request
    logger.info("📥 方案详情请求: %s | 方案%d「%s」",
                request.city, detail_request.outline.plan_id, detail_request.outline.style)
    try:
        trip_plan = await execute_detail(detail_request, x_owner_key.strip())
        logger.info("📤 方案详情响应: %s 成功，接口总耗时 %.1fs",
                    request.city, time.monotonic() - t0)
        return TripPlanResponse(success=True, message="旅行计划生成成功", data=trip_plan)

    except BudgetExceeded as exc:
        logger.warning("💰 方案详情被成本熔断拦截: %s（%s）", request.city, exc)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("❌ 方案详情失败: %s（耗时 %.1fs）", request.city, time.monotonic() - t0)
        raise HTTPException(status_code=500, detail=f"生成方案详情失败: {exc}") from exc


@router.get("/health", summary="旅行规划服务健康检查")
async def health_check():
    """健康检查"""
    settings = get_settings()
    return {
        "status": "healthy",
        "service": "trip-planner",
        "model": settings.llm_model_id,
        "amap_enabled": settings.amap_enabled,
    }
