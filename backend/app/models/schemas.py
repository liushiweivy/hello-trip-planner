"""数据模型定义"""

import re
from datetime import datetime
from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

# ============ 输入边界校验（企业级底线）============
# 动机：线上日志抓到过真实 XSS 探针（city="<img src=x onerror=alert(1)>"）。
# 前端 Vue 默认转义只是展示层防线，后端必须在边界拒绝畸形输入：
# 脏字符串会进缓存键、日志、监控列与 LLM prompt，放任会污染整条链路。

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_ANGLE_BRACKETS = re.compile(r"[<>]")
_DATE_FMT = "%Y-%m-%d"


def _clean_label(v: str, field: str, max_len: int) -> str:
    """短标签类字段（城市/交通/住宿/偏好）：去空白、限长、禁 <> 与控制符"""
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{field}不能为空")
    if len(v) > max_len:
        raise ValueError(f"{field}超长（{len(v)} > {max_len} 字符）")
    if _ANGLE_BRACKETS.search(v):
        raise ValueError(f"{field}含非法字符 < 或 >")
    if _CONTROL_CHARS.search(v):
        raise ValueError(f"{field}含非法控制字符")
    return v


def _clean_free_text(v: Optional[str], max_len: int = 500) -> str:
    """自由文本：允许 <>（如"预算<2000"），但去控制符、限长（进 LLM prompt）"""
    v = (v or "").strip()
    if len(v) > max_len:
        raise ValueError(f"额外要求超长（{len(v)} > {max_len} 字符）")
    if _CONTROL_CHARS.search(v):
        raise ValueError("额外要求含非法控制字符")
    return v


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """旅行规划请求"""
    city: str = Field(..., description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    client_tag: Optional[str] = Field(default="", description="来源/压测批次标记（监控分组用）", example="loadtest-20261018")
    mock_llm: bool = Field(default=False, description="测试模式：不调真实 LLM，快速返回模拟数据（零 token 消耗）")

    @field_validator("city")
    @classmethod
    def _v_city(cls, v: str) -> str:
        return _clean_label(v, "城市", 20)

    @field_validator("transportation")
    @classmethod
    def _v_transportation(cls, v: str) -> str:
        return _clean_label(v, "交通方式", 20)

    @field_validator("accommodation")
    @classmethod
    def _v_accommodation(cls, v: str) -> str:
        return _clean_label(v, "住宿偏好", 20)

    @field_validator("preferences")
    @classmethod
    def _v_preferences(cls, v: List[str]) -> List[str]:
        if len(v) > 6:
            raise ValueError(f"偏好标签过多（{len(v)} > 6 个）")
        return [_clean_label(item, "偏好标签", 20) for item in v]

    @field_validator("free_text_input")
    @classmethod
    def _v_free_text(cls, v: Optional[str]) -> str:
        return _clean_free_text(v)

    @field_validator("client_tag")
    @classmethod
    def _v_client_tag(cls, v: Optional[str]) -> str:
        v = (v or "").strip()
        return v[:64]

    @field_validator("start_date", "end_date")
    @classmethod
    def _v_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, _DATE_FMT)
        except (ValueError, TypeError):
            raise ValueError(f"日期格式必须为 YYYY-MM-DD：{v!r}")
        return v

    @model_validator(mode="after")
    def _v_date_order(self):
        if self.start_date > self.end_date:  # 同格式字符串可安全按字典序比较
            raise ValueError("开始日期不能晚于结束日期")
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(default="", description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位"""
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    source: str = Field(default="", description="来源: llm/cache/seed（空=LLM 新生成）")


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")


# ============ 方案大纲（两阶段生成：先大纲后详情） ============

class OutlineSpot(BaseModel):
    """大纲中的景点（只携带定位所需的最小信息）"""
    name: str = Field(..., description="景点名称")
    address: str = Field(default="", description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    category: str = Field(default="景点", description="类别")


class OutlineDay(BaseModel):
    """大纲中的单日"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    title: str = Field(default="", description="当日主题")
    spots: List[OutlineSpot] = Field(default_factory=list, description="当日景点")


class PlanOutline(BaseModel):
    """一套方案的大纲：风格 + 每天时间轴，详情按需二次生成"""
    plan_id: int = Field(..., description="方案编号")
    style: str = Field(..., description="方案风格名")
    tagline: str = Field(default="", description="一句话卖点")
    highlights: List[str] = Field(default_factory=list, description="方案亮点")
    estimated_budget: int = Field(default=0, description="人均粗略总预算(元)")
    days: List[OutlineDay] = Field(default_factory=list, description="每日时间轴")


class TripOutlineData(BaseModel):
    """大纲阶段响应数据：4 套方案 + 共享天气"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    plans: List[PlanOutline] = Field(default_factory=list, description="方案大纲列表")
    weather_info: List[WeatherInfo] = Field(default_factory=list, description="天气信息")
    source: str = Field(default="", description="来源: llm/cache/seed（空=LLM 新生成）")
    record_id: Optional[int] = Field(default=None, description="行程持久化记录 ID（历史行程回填/详情关联用）")


class TripOutlineResponse(BaseModel):
    """方案大纲响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripOutlineData] = Field(default=None, description="方案大纲数据")


class TripDetailRequest(BaseModel):
    """详情阶段请求：原始需求 + 用户选定的大纲"""
    request: TripRequest = Field(..., description="原始旅行需求")
    outline: PlanOutline = Field(..., description="用户选定的方案大纲")
    record_id: Optional[int] = Field(default=None, description="关联的行程记录 ID（有则详情持久化到该记录下，且优先读已存详情）")


class BudgetJobRequest(BaseModel):
    """预算后台任务入参：已定稿路线（不含预算）+ 原始请求（mock 标记/监控归因用）"""
    record_id: Optional[int] = Field(default=None, description="关联的行程记录 ID（仅日志归因，不据此持久化预算）")
    plan_id: int = Field(..., ge=1, le=4, description="方案编号 1-4")
    request: Optional[TripRequest] = Field(default=None, description="原始旅行需求（缺省时按真实 LLM 处理）")
    plan: TripPlan = Field(..., description="已定稿路线详情")


class BudgetJobResult(BaseModel):
    """预算后台任务结果：只在缓存与任务结果里短期存在，不进行程库表"""
    budget: Budget = Field(..., description="预算汇总")
    source: str = Field(default="llm", description="来源: llm/cache/mock/fallback(解析失败降级求和)")


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")

