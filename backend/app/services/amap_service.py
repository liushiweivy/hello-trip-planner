"""高德地图 REST 服务封装

优化点（相对原 MCP/uvx 版本）：
- 不再依赖 uvx 启动 amap-mcp-server 子进程，直接 httpx 调用高德 Web 服务 REST API
  （省掉 uv/python 子进程开销，Windows 上无需安装 uv，启动更快更稳）
- 真正解析返回数据（原实现全是 TODO，永远返回空列表）
- 单例 httpx.Client，超时/错误统一处理
- 客户端限速（默认 2.5 QPS）+ CUQPS 超限退避重试：免费 key 有 QPS 上限，
  2026-10-18 实测并发采集时 CUQPS_HAS_EXCEEDED_THE_LIMIT 静默丢整组候选
"""

import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings
from ..models.schemas import Location, POIInfo, WeatherInfo

AMAP_BASE_URL = "https://restapi.amap.com/v3"
_MIN_INTERVAL = 0.4          # 进程内请求最小间隔（≤2.5 QPS，免费 key 安全线）
_QPS_RETRIES = 3             # CUQPS 超限时的退避重试次数


class AmapApiError(RuntimeError):
    """高德 API 业务错误"""


def _clean(value: Any, default: str = "") -> str:
    """高德部分字段空值会返回 [] 而非空字符串，统一清洗"""
    if value is None or isinstance(value, list):
        return default
    return str(value)


def _parse_location(location: str) -> Optional[Location]:
    """解析 '经度,纬度' 字符串"""
    try:
        lng_str, lat_str = location.split(",")
        return Location(longitude=float(lng_str), latitude=float(lat_str))
    except (ValueError, AttributeError):
        return None


class AmapService:
    """高德地图服务（REST 直连，同步客户端；在异步路由中通过 to_thread 调用）"""

    def __init__(self):
        settings = get_settings()
        if not settings.amap_api_key:
            raise ValueError("AMAP_API_KEY 未配置，高德实时数据不可用")
        self._key = settings.amap_api_key
        self._client = httpx.Client(timeout=settings.request_timeout)
        self._adcode_cache: Dict[str, str] = {}
        self._rate_lock = threading.Lock()
        self._last_call = 0.0

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发起 GET 请求并校验业务状态码；进程内限速，CUQPS 超限退避重试"""
        params = {"key": self._key, **params}
        last_exc: Optional[AmapApiError] = None
        for attempt in range(_QPS_RETRIES + 1):
            with self._rate_lock:
                wait = _MIN_INTERVAL - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
            response = self._client.get(f"{AMAP_BASE_URL}{path}", params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "1":
                return data
            last_exc = AmapApiError(
                f"高德接口错误 {path}: infocode={data.get('infocode')} info={data.get('info')}"
            )
            # CUQPS_HAS_EXCEEDED_THE_LIMIT 类限流才重试，业务错误（key 无效等）立即抛
            if "CUQPS" not in str(data.get("info")) or attempt == _QPS_RETRIES:
                raise last_exc
            time.sleep(0.8 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    # ============ 地理编码 ============

    def get_city_adcode(self, city: str) -> Optional[str]:
        """获取城市 adcode（天气/公交路线接口需要）"""
        if city in self._adcode_cache:
            return self._adcode_cache[city]
        data = self._get("/geocode/geo", {"address": city})
        geocodes = data.get("geocodes") or []
        adcode = _clean(geocodes[0].get("adcode")) if geocodes else ""
        if adcode:
            self._adcode_cache[city] = adcode
        return adcode or None

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """地理编码：地址转坐标"""
        params: Dict[str, Any] = {"address": address}
        if city:
            params["city"] = city
        data = self._get("/geocode/geo", params)
        geocodes = data.get("geocodes") or []
        if not geocodes:
            return None
        return _parse_location(_clean(geocodes[0].get("location")))

    # ============ POI 搜索 ============

    def search_poi(
        self,
        keywords: str,
        city: str,
        citylimit: bool = True,
        limit: Optional[int] = None,
    ) -> List[POIInfo]:
        """关键词搜索 POI（景点/酒店/餐饮等）"""
        settings = get_settings()
        data = self._get(
            "/place/text",
            {
                "keywords": keywords,
                "city": city,
                "citylimit": "true" if citylimit else "false",
                "offset": limit or settings.max_poi_per_keyword,
                "page": 1,
                "extensions": "all",
            },
        )
        pois: List[POIInfo] = []
        for item in data.get("pois") or []:
            location = _parse_location(_clean(item.get("location")))
            if location is None:
                continue
            pois.append(
                POIInfo(
                    id=_clean(item.get("id")),
                    name=_clean(item.get("name")),
                    type=_clean(item.get("type"), "景点"),
                    address=_clean(item.get("address"))
                    or _clean(item.get("adname"))
                    or city,
                    location=location,
                    tel=_clean(item.get("tel")) or None,
                )
            )
        return pois

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """POI 详情"""
        data = self._get("/place/detail", {"id": poi_id})
        pois = data.get("pois") or []
        return pois[0] if pois else {}

    # ============ 天气 ============

    def get_weather(self, city: str) -> List[WeatherInfo]:
        """查询未来 4 天天气预报（高德免费版预报天数）"""
        adcode = self.get_city_adcode(city)
        if not adcode:
            raise AmapApiError(f"无法解析城市 adcode: {city}")

        data = self._get(
            "/weather/weatherInfo", {"city": adcode, "extensions": "all"}
        )
        forecasts = data.get("forecasts") or []
        if not forecasts:
            return []

        result: List[WeatherInfo] = []
        for cast in forecasts[0].get("casts") or []:
            result.append(
                WeatherInfo(
                    date=_clean(cast.get("date")),
                    day_weather=_clean(cast.get("dayweather")),
                    night_weather=_clean(cast.get("nightweather")),
                    day_temp=_clean(cast.get("daytemp"), "0"),
                    night_temp=_clean(cast.get("nighttemp"), "0"),
                    wind_direction=(
                        f"{_clean(cast.get('daywind'))}风 "
                        f"{_clean(cast.get('daypower'))}级"
                    ).strip(),
                    wind_power=_clean(cast.get("daypower")),
                )
            )
        return result

    # ============ 路线规划 ============

    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking",
    ) -> Dict[str, Any]:
        """规划两点间路线（walking/driving/transit）"""
        origin = self.geocode(origin_address, origin_city)
        destination = self.geocode(destination_address, destination_city)
        if origin is None or destination is None:
            raise AmapApiError("起终点地理编码失败")

        origin_str = f"{origin.longitude},{origin.latitude}"
        dest_str = f"{destination.longitude},{destination.latitude}"

        if route_type == "driving":
            data = self._get(
                "/direction/driving",
                {"origin": origin_str, "destination": dest_str},
            )
            paths = (data.get("route") or {}).get("paths") or []
        elif route_type == "transit":
            city = origin_city or self.get_city_adcode(origin_address) or ""
            data = self._get(
                "/direction/transit/integrated",
                {"origin": origin_str, "destination": dest_str, "city": city},
            )
            transits = (data.get("route") or {}).get("transits") or []
            paths = transits[:1]
        else:
            data = self._get(
                "/direction/walking",
                {"origin": origin_str, "destination": dest_str},
            )
            paths = (data.get("route") or {}).get("paths") or []

        if not paths:
            raise AmapApiError("未规划出路线")

        path = paths[0]
        return {
            "distance": float(_clean(path.get("distance"), "0")),
            "duration": int(float(_clean(path.get("duration"), "0"))),
            "route_type": route_type,
            "description": f"{route_type} 路线，全程约"
            f"{float(_clean(path.get('distance'), '0')) / 1000:.1f} 公里",
        }

    def close(self) -> None:
        self._client.close()


# 全局单例
_amap_service: Optional[AmapService] = None


def get_amap_service() -> AmapService:
    """获取高德服务实例（单例）；未配置 Key 时抛出明确异常"""
    global _amap_service
    if _amap_service is None:
        _amap_service = AmapService()
    return _amap_service
