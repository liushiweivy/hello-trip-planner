"""地图服务 API 路由（同步 httpx 客户端，使用 def 交给 FastAPI 线程池）"""

from fastapi import APIRouter, HTTPException, Query

from ...models.schemas import (
    POISearchResponse,
    RouteRequest,
    RouteResponse,
    WeatherResponse,
)
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/map", tags=["地图服务"])


@router.get("/poi", response_model=POISearchResponse, summary="搜索 POI")
def search_poi(
    keywords: str = Query(..., description="搜索关键词"),
    city: str = Query(..., description="城市"),
    citylimit: bool = Query(True, description="是否限制在城市范围内"),
):
    try:
        pois = get_amap_service().search_poi(keywords, city, citylimit)
        return POISearchResponse(success=True, message="POI搜索成功", data=pois)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"POI搜索失败: {exc}") from exc


@router.get("/weather", response_model=WeatherResponse, summary="查询天气")
def get_weather(city: str = Query(..., description="城市名称")):
    try:
        weather = get_amap_service().get_weather(city)
        return WeatherResponse(success=True, message="天气查询成功", data=weather)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"天气查询失败: {exc}") from exc


@router.post("/route", response_model=RouteResponse, summary="规划路线")
def plan_route(request: RouteRequest):
    try:
        route = get_amap_service().plan_route(
            origin_address=request.origin_address,
            destination_address=request.destination_address,
            origin_city=request.origin_city,
            destination_city=request.destination_city,
            route_type=request.route_type,
        )
        return RouteResponse(success=True, message="路线规划成功", data=route)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"路线规划失败: {exc}") from exc


@router.get("/health", summary="地图服务健康检查")
def health_check():
    from ...config import get_settings

    return {
        "status": "healthy",
        "service": "map-service",
        "amap_enabled": get_settings().amap_enabled,
    }
