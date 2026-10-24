# -*- coding: utf-8 -*-
"""异步任务端点：提交（幂等）+ 轮询"""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ...models.schemas import BudgetJobRequest, TripDetailRequest, TripRequest, TripPlan
from ...services import job_service
from ...services.budget_cache import budget_idempotency_key

logger = logging.getLogger("trip.api")

router = APIRouter(prefix="/jobs", tags=["异步任务"])


class JobSubmitRequest(BaseModel):
    """任务提交：kind=outline 时 payload 为 TripRequest；detail 时为 TripDetailRequest；
    budget 时为 BudgetJobRequest"""
    kind: str = Field(..., description="任务类型: outline/detail/budget", example="outline")
    payload: Dict[str, Any] = Field(..., description="任务参数（与同步端点请求体相同）")
    idempotency_key: str = Field(..., description="幂等键（客户端生成 uuid；budget 任务由服务端按内容重写）",
                                 min_length=8, max_length=80)


def _validate_payload(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """提交时就用同步端点同一套模型校验（含输入边界校验），脏任务不进队列"""
    try:
        if kind == "outline":
            return TripRequest(**payload).model_dump(mode="json")
        if kind == "detail":
            return TripDetailRequest(**payload).model_dump(mode="json")
        if kind == "budget":
            return BudgetJobRequest(**payload).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"payload 校验失败: {exc}") from exc
    raise HTTPException(status_code=400,
                        detail=f"未知任务类型: {kind}（可选 outline/detail/budget）")


@router.post("", status_code=202, summary="提交规划任务（幂等）")
async def submit_job(req: JobSubmitRequest, x_owner_key: str = Header(default="")):
    owner = x_owner_key.strip()[:64]
    payload = _validate_payload(req.kind, req.payload)
    # budget 幂等键由服务端按「路线内容 + 日期」重写：双击/轮询重提/换页面都命中同一任务，
    # 不依赖客户端是否复用 key；跨天新键但 worker 先查 24h 缓存，缓存未过仍零 LLM。
    idem_key = req.idempotency_key.strip()
    if req.kind == "budget":
        idem_key = budget_idempotency_key(
            TripPlan(**payload["plan"]),
            TripRequest(**payload["request"]) if payload.get("request") else None)
    job_id, created = job_service.create_job(
        req.kind, owner, payload, idem_key)
    if req.kind == "budget" and not created:
        existing = job_service.get_job(job_id)
        if existing and existing["status"] == "failed":
            # 失败任务可被重试：加随机后缀生成新任务（否则幂等命中死任务，重试按钮空转）
            idem_key = f"{idem_key}-r{uuid.uuid4().hex[:6]}"
            job_id, created = job_service.create_job(
                req.kind, owner, payload, idem_key)
            logger.info("🧵 预算任务重试：前次失败，另起新任务 %s", job_id[:8])
    logger.info("🧵 任务提交：%s kind=%s %s", job_id[:8], req.kind,
                "（新建）" if created else "（幂等命中，复用已有任务）")
    return {"success": True, "job_id": job_id, "created": created,
            "status": job_service.get_job(job_id)["status"]}


@router.get("/{job_id}", summary="查询任务状态（前端 3s 轮询）")
async def get_job(job_id: str, x_owner_key: str = Header(default="")):
    job = job_service.get_job(job_id, x_owner_key.strip()[:64])
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或不属于当前用户")
    return {"success": True, **job}
