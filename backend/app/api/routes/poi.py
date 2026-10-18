"""POI 相关 API 路由"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ...config import get_settings
from ...services.amap_service import get_amap_service
from ...services.unsplash_service import get_unsplash_service

router = APIRouter(prefix="/poi", tags=["POI"])


class POIDetailResponse(BaseModel):
    """POI 详情响应"""
    success: bool
    message: str
    data: Optional[dict] = None


@router.get("/detail/{poi_id}", response_model=POIDetailResponse, summary="获取 POI 详情")
def get_poi_detail(poi_id: str):
    try:
        result = get_amap_service().get_poi_detail(poi_id)
        return POIDetailResponse(success=True, message="获取POI详情成功", data=result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"获取POI详情失败: {exc}") from exc


@router.get("/search", summary="搜索 POI")
def search_poi(keywords: str, city: str = "北京"):
    try:
        result = get_amap_service().search_poi(keywords, city)
        return {"success": True, "message": "搜索成功", "data": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"搜索POI失败: {exc}") from exc


@router.get("/photo", summary="获取景点配图（Unsplash，未配置 Key 时返回空 URL）")
def get_attraction_photo(name: str):
    settings = get_settings()
    photo_url = None
    if settings.unsplash_access_key:
        unsplash = get_unsplash_service()
        photo_url = unsplash.get_photo_url(f"{name} China landmark") or unsplash.get_photo_url(name)
    return {
        "success": True,
        "message": "获取图片成功" if photo_url else "未配置 Unsplash 或无匹配图片",
        "data": {"name": name, "photo_url": photo_url},
    }
