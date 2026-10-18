# -*- coding: utf-8 -*-
"""异步任务端点：提交（幂等）+ 轮询"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ...models.schemas import TripDetailRequest, TripRequest
from ...services import job_service

logger = logging.getLogger("trip.api")

router = APIRouter(prefix="/jobs", tags=["异步任务"])


class JobSubmitRequest(BaseModel):
    """任务提交：kind=outline 时 payload 为 TripRequest；detail 时为 TripDetailRequest"""
    kind: str = Field(..., description="任务类型: outline/detail", example="outline")
    payload: Dict[str, Any] = Field(..., description="任务参数（与同步端点请求体相同）")
    idempotency_key: str = Field(..., description="幂等键（客户端生成 uuid；重复提交返回同一任务）",
                                 min_length=8, max_length=80)


def _validate_payload(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """提交时就用同步端点同一套模型校验（含输入边界校验），脏任务不进队列"""
    try:
        if kind == "outline":
            return TripRequest(**payload).model_dump(mode="json")
        if kind == "detail":
            return TripDetailRequest(**payload).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"payload 校验失败: {exc}") from exc
    raise HTTPException(status_code=400, detail=f"未知任务类型: {kind}（可选 outline/detail）")


@router.post("", status_code=202, summary="提交规划任务（幂等）")
async def submit_job(req: JobSubmitRequest, x_owner_key: str = Header(default="")):
    owner = x_owner_key.strip()[:64]
    payload = _validate_payload(req.kind, req.payload)
    job_id, created = job_service.create_job(
        req.kind, owner, payload, req.idempotency_key.strip())
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
