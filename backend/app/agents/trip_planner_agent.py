"""旅行规划 Agent

架构优化（相对原多 Agent 版本）：
- 原实现：景点/天气/酒店/规划 4 个 SimpleAgent 串行 run，靠提示词里的
  ``[TOOL_CALL:...]`` 文本约定驱动 MCP，任何一环格式跑偏即整体降级到假数据。
- 现实现：高德 REST 并发取真实数据（POI/天气）→ 一次高质量 LLM 规划调用。
  LLM 只负责它擅长的组织与写作，工具调用不再依赖模型生成格式的稳定性。
- LLM 不可用/JSON 解析失败时：一次带错误信息的修复重试，仍失败才走兜底行程。
"""

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("trip.planner")

# 用户没填偏好时的默认检索词组：刻意跨类别，避免候选池被单一类型
# （实测「著名景点」一词在高德返回里几乎全是公园）垄断推荐结果
DEFAULT_POI_KEYWORDS = ["著名景点", "主题公园", "海滨海岛", "博物馆", "特色街区", "古迹"]
MAX_LLM_CANDIDATES = 30  # 喂给 LLM 的候选 POI 上限（去重后），再多 prompt 冗长反而不聚焦
DETAIL_CHUNK_DAYS = 5   # 单次详情调用最多生成的天数：14 天一次出 JSON 必超 max_tokens 截断
TRUNCATED_RETRY_MAX_TOKENS = 32768  # 输出被截断时的重试预算（默认 24576 不够用才走到这）
BUDGET_MAX_TOKENS = 1024  # 预算汇总 JSON 很小，独立调用只需极小输出预算
MAX_ENRICH_SPOTS = 40   # 缓存/预制大纲 serve 时坐标反查补全的景点上限（一次性，补完回写缓存）

from hello_agents import SimpleAgent

from ..config import get_settings
from ..models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Location,
    Meal,
    OutlineDay,
    OutlineSpot,
    PlanOutline,
    POIInfo,
    TripOutlineData,
    TripPlan,
    TripRequest,
    WeatherInfo,
)
from ..services.amap_service import AmapApiError, get_amap_service
from ..services.cache_service import (
    get_cache_store,
    make_detail_key,
    make_outline_key,
    rebase_outline_payload,
    rebase_plan_payload,
    seed_eligible,
)
from ..services.cost_service import ensure_budget
from ..services.llm_service import get_llm, get_thread_last_usage
from ..services.metrics_service import MetricsRecord, record_metric
from ..services.prompt_service import get_prompt

PLANNER_SYSTEM_PROMPT = """你是一名资深中国境内旅行规划师。请根据用户需求和提供的实时数据，制定一份可直接执行的旅行计划。

工作原则：
1. 景点优先从「实时 POI 数据」中挑选，必须沿用其真实名称、地址和经纬度坐标，禁止编造坐标。
2. 天气以「实时天气预报」为准，据此安排室内外景点（如下雨优先安排博物馆）。
3. 每天安排 2-3 个景点，考虑景点间距离与顺路程度，节奏合理，包含早中晚三餐的具体建议。
   整趟行程要**覆盖不同类型**（海滨/主题公园/文化古迹/特色街区/自然风光），
   除非用户明确只要某一类，否则不要整趟都是同一类型（比如全是公园）。
4. 酒店从「实时酒店数据」中挑选，位置尽量靠近主要景点或交通枢纽。
5. 各项门票/餐饮/酒店单价要如实填写（ticket_price / estimated_cost），但**不要输出预算汇总**，budget 字段不需要生成。
6. 只输出一个 JSON 对象，不要输出 JSON 之外的任何解释文字，不要使用 markdown 代码块。
7. 请高效思考，快速给出最终 JSON，避免冗长推理。

JSON 结构：
{
  "city": "城市名",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "当日行程概述（30字内）",
      "transportation": "当日主要交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称", "address": "地址",
        "location": {"longitude": 0.0, "latitude": 0.0},
        "price_range": "300-500元", "rating": "4.5", "distance": "距景区约2公里",
        "type": "酒店类型", "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名", "address": "详细地址",
          "location": {"longitude": 0.0, "latitude": 0.0},
          "visit_duration": 120, "description": "景点介绍与游玩建议",
          "category": "类别", "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "名称", "description": "推荐菜/描述", "estimated_cost": 30},
        {"type": "lunch", "name": "名称", "description": "推荐菜/描述", "estimated_cost": 50},
        {"type": "dinner", "name": "名称", "description": "推荐菜/描述", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {"date": "YYYY-MM-DD", "day_weather": "晴", "night_weather": "多云",
     "day_temp": 25, "night_temp": 15, "wind_direction": "南风", "wind_power": "1-3级"}
  ],
  "overall_suggestions": "出行准备、交通、避坑等总体建议"
}"""


BUDGET_SYSTEM_PROMPT = """你是一名资深旅行费用核算师。用户会给你一份**已定稿**的旅行路线账单（城市、天数、每日景点门票、酒店价格、餐饮价格、交通方式）。请汇总整趟行程的人民币费用。

口径：
1. total_attractions / total_hotels / total_meals：按账单明细逐项求和；账单中缺失的常规花费（如免费景点无门票、某日未列酒店）按 0 计，不要凭空添加项目。
2. total_transportation：账单每日交通方式对应的市内交通费（公交地铁约 20-30 元/人/天，打车约 80-150 元/人/天，租车/自驾含油费停车费）；若用户需求提到跨城/往返大交通（高铁、飞机），按该城市常规票价计入。
3. total 必须等于四项之和。
4. 所有数字为非负整数，单位元，这是一人的花费。
5. 只输出一个 JSON 对象，不要输出 JSON 之外的任何文字，不要使用 markdown 代码块。请高效思考，快速给出结果。

JSON 结构：
{
  "total_attractions": 0,
  "total_hotels": 0,
  "total_meals": 0,
  "total_transportation": 0,
  "total": 0
}"""


OUTLINE_SYSTEM_PROMPT = """你是一名资深中国境内旅行规划师。请根据用户需求和提供的实时数据，设计 4 套风格截然不同的旅行方案大纲（只到大纲，不写详情）。

4 套方案为固定风格卡位，一套都不能少，且选点必须紧扣各自主题、互相明显区分：
1. 「经典全景」：地标名景打卡全覆盖，第一次去必看的那种。
2. 「深度慢游」：少而精、节奏舒缓，每天景点偏少，留足体验时间。
3. 「经济实惠」：优先免费/低价景点（公园、街区、免费博物馆等），estimated_budget 必须显著低于经典全景，住宿交通也以省钱为先。
4. 「自然风光」：优先山水、公园、海滨、湿地、森林等自然类景点，少排人文场馆。

工作原则：
1. 景点优先从「实时 POI 数据」中挑选，必须沿用其真实名称、地址和经纬度坐标，禁止编造坐标。
   若候选里自然类/免费类不足，可凭可靠知识补当地知名景点（坐标不确定就不给坐标，禁止编造）。
2. 每套方案每天 2-3 个景点（深度慢游可 1-2 个），同一天内顺路；4 套方案选点要有明显差异，地标只允许少量重合。
3. 天气以「实时天气预报」为准，据此安排室内外景点。
4. 只输出一个 JSON 对象，不要输出 JSON 之外的任何解释文字，不要使用 markdown 代码块。

JSON 结构：
{
  "plans": [
    {
      "plan_id": 1,
      "style": "方案风格名（4-8字）",
      "tagline": "一句话卖点（20字内）",
      "highlights": ["亮点1", "亮点2", "亮点3"],
      "estimated_budget": 1500,
      "days": [
        {
          "date": "YYYY-MM-DD",
          "day_index": 0,
          "title": "当日主题（12字内）",
          "spots": [
            {"name": "景点名", "address": "详细地址",
             "location": {"longitude": 0.0, "latitude": 0.0}, "category": "类别"}
          ]
        }
      ]
    }
  ]
}
estimated_budget 为该方案人均粗略总预算（元，含门票/住宿/餐饮/市内交通）。plans 必须恰好 4 套（plan_id 1-4），days 必须覆盖全部旅行天数。

请高效思考，快速给出最终 JSON，避免冗长推理。"""


def _safe(label: str, fn, *args):
    """线程任务包装：失败返回 None 而不是拖垮整组并发（label 在前以便 run_in_executor 传参）"""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - 数据增强失败统一降级
        logger.warning("⚠️  %s失败: %s", label, exc)
        return None


class TripPlannerService:
    """旅行规划服务"""

    def __init__(self):
        self.settings = get_settings()
        self.llm = get_llm()
        # LLM 并发闸门：4 套详情预生成正好打满，多用户时多余请求排队
        self._llm_sem = asyncio.Semaphore(self.settings.llm_max_concurrency)
        logger.info("✅ 旅行规划 Agent 初始化成功")

    def _new_planner(self, system_prompt: str = "") -> SimpleAgent:
        """每次规划新建 agent：SimpleAgent 会累积对话历史，
        单例复用会让上下文随请求数翻倍，推理模型会越来越慢。
        system_prompt 留空时取版本化的 planner prompt（app/prompts/planner.md）。"""
        return SimpleAgent(
            name="旅行规划专家",
            llm=self.llm,
            system_prompt=system_prompt or get_prompt("planner")[0],
        )

    # ============ LLM 调用计时与监控 ============

    def _llm_call_sync(self, system_prompt: str, prompt: str,
                       max_tokens: Optional[int] = None):
        """一次规划调用 + 取回 token 用量。

        必须整体跑在同一执行线程：invoke 的 usage 补丁走线程本地存储，
        拆成两次 run_in_executor 会落到不同线程而取不到。
        """
        planner = self._new_planner(system_prompt)
        t = time.monotonic()
        kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        raw = planner.run(prompt, **kwargs)
        return raw, get_thread_last_usage(), int((time.monotonic() - t) * 1000)

    async def _timed_llm(self, system_prompt: str, prompt: str,
                         max_tokens: Optional[int] = None):
        """返回 (raw, usage, llm_ms, queue_ms)：queue_ms 是并发闸门排队耗时，
        监控据此区分「排队等闸门」与「模型本身慢」（压测高并发时尤其关键）。"""
        loop = asyncio.get_running_loop()
        tq = time.monotonic()
        async with self._llm_sem:  # 并发闸门外排队，不占执行线程
            queue_ms = int((time.monotonic() - tq) * 1000)
            raw, usage, llm_ms = await loop.run_in_executor(
                None, self._llm_call_sync, system_prompt, prompt, max_tokens)
            return raw, usage, llm_ms, queue_ms

    @staticmethod
    def _is_truncated(usage: Optional[dict]) -> bool:
        """输出是否被 max_tokens 截断（截断的 JSON 必解析失败，同预算重试只会再截断）"""
        return bool(usage) and usage.get("finish_reason") == "length"

    @staticmethod
    def _record(endpoint: str, phase: str, status: str, latency_ms: int,
                usage: Optional[dict], prompt: str, raw: str, request: TripRequest,
                plan_id: Optional[int] = None, plan_style: str = "",
                error: Optional[str] = None, max_tokens: Optional[int] = None,
                attempt: int = 1, queue_ms: int = 0) -> None:
        # 偏好摘要：标签 + 自由文本，供「哪类请求慢/失败」分析（截 255 防炸列宽）
        prefs = "、".join(request.preferences or [])
        if request.free_text_input:
            prefs = f"{prefs}｜{request.free_text_input}" if prefs else request.free_text_input
        record_metric(MetricsRecord(
            endpoint=endpoint, phase=phase, status=status, latency_ms=latency_ms,
            mock=bool(getattr(request, "mock_llm", False)),
            # city/plan_style 列宽 64：超长会致整行 INSERT 失败丢监控，截断兜底
            city=(request.city or "")[:64], travel_days=request.travel_days,
            plan_id=plan_id, plan_style=(plan_style or "")[:64],
            prompt_chars=len(prompt or ""), output_chars=len(raw or ""),
            prompt_tokens=(usage or {}).get("prompt_tokens"),
            completion_tokens=(usage or {}).get("completion_tokens"),
            reasoning_tokens=(usage or {}).get("reasoning_tokens"),
            total_tokens=(usage or {}).get("total_tokens"),
            # 主备切换留痕：failover 走备用时 model 列加 @backup 后缀（按供应商归因失败率/成本）
            model=get_settings().llm_model_id + (
                "@backup" if (usage or {}).get("provider") == "backup" else ""),
            client_tag=(getattr(request, "client_tag", "") or "")[:64],
            error=error,
            finish_reason=(usage or {}).get("finish_reason") or "",
            max_tokens=max_tokens,
            attempt=attempt,
            queue_ms=queue_ms,
            prefs=prefs[:255],
            prompt_excerpt=(prompt or "")[:240],
        ))

    # ============ 缓存服务 ============

    async def _fetch_weather(self, city: str) -> List[WeatherInfo]:
        """缓存命中时的实时天气重查：缓存不存天气（易过期），serve 时现查"""
        if not self.settings.amap_enabled:
            return []
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, _safe, "天气查询", get_amap_service().get_weather, city)
        return result or []

    async def _enrich_outline_coords(self, plans: List[PlanOutline], city: str) -> bool:
        """预制/缓存大纲缺坐标的景点用高德反查补全（一次性，补完由调用方回写缓存）。
        坐标只认高德真实数据，禁止编造。"""
        if not self.settings.amap_enabled:
            return False
        missing = [spot for plan in plans for day in plan.days
                   for spot in day.spots if spot.location is None]
        if not missing:
            return False
        targets = missing[:MAX_ENRICH_SPOTS]
        loop = asyncio.get_running_loop()

        def _fill() -> int:
            filled = 0
            for spot in targets:
                try:
                    pois = get_amap_service().search_poi(spot.name, city, True)
                except Exception:  # noqa: BLE001 - 单个失败跳过，不拖垮整组
                    continue
                if pois:
                    spot.location = pois[0].location
                    if not spot.address:
                        spot.address = pois[0].address
                    filled += 1
            return filled

        filled = await loop.run_in_executor(None, _fill)
        if filled:
            logger.info("📍 大纲坐标补全：%d/%d 个景点（高德反查，回写缓存）",
                        filled, len(missing))
        return filled > 0

    async def _serve_outline_cache(self, request: TripRequest, t_start: float
                                   ) -> Optional[TripOutlineData]:
        """大纲缓存命中路径：精确键 → 预制路线两级查找；命中则重排日期、
        补全坐标（回写）、重查天气，零 LLM 调用返回"""
        cache = get_cache_store()
        key = make_outline_key(request)
        entry = cache.get(key)
        source = "cache"
        if entry is None and seed_eligible(request):
            entry = cache.get_seed_outline(request.city, request.travel_days)
            source = "seed"
        if entry is None:
            return None

        plans = [PlanOutline(**p) for p in
                 rebase_outline_payload(entry["payload"]["plans"], request.start_date)]
        if await self._enrich_outline_coords(plans, request.city):
            cache.update_payload(entry["cache_key"], {
                "plans": [p.model_dump(mode="json") for p in plans]})
        weather = await self._fetch_weather(request.city)
        cache.touch(entry["cache_key"])
        ms = int((time.monotonic() - t_start) * 1000)
        self._record("/api/trip/plan", "outline", "cache_hit", ms,
                     None, "", "", request)
        logger.info("⚡ 大纲%s命中：%s %d天 %d 套方案，耗时 %.1fs（零 LLM 调用）",
                    "缓存" if source == "cache" else "预制路线",
                    request.city, request.travel_days, len(plans), ms / 1000)
        return TripOutlineData(
            city=request.city, start_date=request.start_date,
            end_date=request.end_date, plans=plans,
            weather_info=weather, source=source)

    def _cache_outline_result(self, request: TripRequest,
                              plans: List[PlanOutline]) -> None:
        """LLM 大纲成功后写入运行时缓存（兜底大纲不入缓存）"""
        try:
            get_cache_store().put(
                make_outline_key(request), "outline", request.city,
                request.travel_days, None,
                {"plans": [p.model_dump(mode="json") for p in plans]},
                ttl_days=self.settings.cache_ttl_days)
            logger.info("⚡ 大纲已入缓存（TTL %d 天）", self.settings.cache_ttl_days)
        except Exception:  # noqa: BLE001 - 缓存失败不影响主流程
            logger.warning("⚡ 大纲缓存写入失败（不影响主流程）", exc_info=True)

    def _cache_detail_result(self, request: TripRequest, outline: PlanOutline,
                             plan: TripPlan) -> None:
        """LLM 详情成功后写入运行时缓存（兜底/部分降级不入缓存）"""
        try:
            get_cache_store().put(
                make_detail_key(request, outline), "detail", request.city,
                request.travel_days, outline.plan_id,
                plan.model_dump(mode="json"),
                ttl_days=self.settings.cache_ttl_days)
            logger.info("⚡ 详情已入缓存（方案%d，TTL %d 天）",
                        outline.plan_id, self.settings.cache_ttl_days)
        except Exception:  # noqa: BLE001 - 缓存失败不影响主流程
            logger.warning("⚡ 详情缓存写入失败（不影响主流程）", exc_info=True)

    # ============ 实时数据采集 ============

    async def _gather_context(
        self, request: TripRequest
    ) -> Tuple[List[Tuple[str, List[POIInfo]]], List[POIInfo], List[WeatherInfo]]:
        """并发采集：多关键词景点 POI + 酒店 + 天气预报。

        返回 [(关键词, 该词命中的POI组)]，决策日志要按词汇报命中情况。
        """
        if not self.settings.amap_enabled:
            logger.info("📡 高德未配置：跳过实时数据采集，纯 LLM 知识规划")
            return [], [], []

        loop = asyncio.get_running_loop()
        # 高德免费 key 有 QPS 上限（实测 8 并发触发 CUQPS_HAS_EXCEEDED_THE_LIMIT），
        # 信号量限 3 并发：宁可慢一点，不能静默丢一整组候选
        sem = asyncio.Semaphore(3)

        def throttled(label, fn, *args):
            async def runner():
                async with sem:
                    return await loop.run_in_executor(None, _safe, label, fn, *args)
            return runner()

        if request.preferences:
            # 用户偏好词（最多2个）+ 著名景点兜底：保证候选池里始终有主流景点
            keywords = list(dict.fromkeys(list(request.preferences[:2]) + ["著名景点"]))
            logger.info("🔎 检索策略：按用户偏好 %s 检索，另加「著名景点」兜底 → %s",
                        request.preferences, keywords)
        else:
            keywords = DEFAULT_POI_KEYWORDS
            logger.info("🔎 检索策略：未填偏好，用跨类别默认词组 %s（防单一类型垄断候选池）",
                        keywords)

        poi_tasks = [
            throttled(f"POI搜索[{keyword}]", get_amap_service().search_poi,
                      keyword, request.city, True)
            for keyword in keywords
        ]
        hotel_task = throttled("酒店搜索", get_amap_service().search_poi,
                               f"{request.accommodation}酒店", request.city, True)
        weather_task = throttled("天气查询", get_amap_service().get_weather, request.city)

        t0 = time.monotonic()
        poi_results, hotels, weather = await asyncio.gather(
            asyncio.gather(*poi_tasks), hotel_task, weather_task
        )
        elapsed = time.monotonic() - t0

        # 决策日志：每个检索词命中了什么，一目了然为什么候选池长这样
        pairs: List[Tuple[str, List[POIInfo]]] = []
        for keyword, group in zip(keywords, poi_results or []):
            group = group or []
            pairs.append((keyword, group))
            names = "、".join(p.name for p in group) or "（无结果）"
            logger.info("  └ POI[%s] 命中 %d 个：%s", keyword, len(group), names)
        logger.info("  └ 酒店 命中 %d 个：%s", len(hotels or []),
                    "、".join(p.name for p in (hotels or [])[:4]) or "（无结果）")
        logger.info("  └ 天气 命中 %d 天预报", len(weather or []))
        logger.info("📡 实时数据采集完成，耗时 %.1fs", elapsed)
        return pairs, hotels or [], weather or []

    @staticmethod
    def _format_pois(pairs: List[Tuple[str, List[POIInfo]]], hotels: List[POIInfo]) -> str:
        """把 POI 数据整理成给 LLM 的文本上下文（跨关键词去重，上限 MAX_LLM_CANDIDATES）"""
        lines: List[str] = []
        if pairs:
            lines.append("【实时景点 POI 数据】（请优先选用，坐标必须原样使用）")
            seen = set()
            kept = 0
            for _keyword, group in pairs:
                for poi in group:
                    if poi.name in seen or kept >= MAX_LLM_CANDIDATES:
                        continue
                    seen.add(poi.name)
                    kept += 1
                    lines.append(
                        f"- {poi.name}｜类别:{poi.type}｜地址:{poi.address}｜"
                        f"坐标:{poi.location.longitude},{poi.location.latitude}"
                    )
            logger.info("🧺 候选池：去重后喂给 LLM %d 个景点候选", kept)
        else:
            lines.append("【实时景点 POI 数据】无。请凭你的可靠知识推荐知名景点。")
            logger.info("🧺 候选池为空：LLM 将凭自身知识推荐")

        if hotels:
            lines.append("\n【实时酒店 POI 数据】")
            for poi in hotels[:4]:
                lines.append(
                    f"- {poi.name}｜地址:{poi.address}｜"
                    f"坐标:{poi.location.longitude},{poi.location.latitude}"
                    + (f"｜电话:{poi.tel}" if poi.tel else "")
                )
        return "\n".join(lines)

    @staticmethod
    def _format_weather(weather: List[WeatherInfo], request: TripRequest) -> str:
        if not weather:
            return "【实时天气预报】无。请按季节常识给出天气信息与穿衣建议。"
        try:
            start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
            end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
            in_range = [w for w in weather if start <= datetime.strptime(w.date, "%Y-%m-%d").date() <= end]
        except ValueError:
            in_range = weather
        chosen = in_range or weather
        lines = ["【实时天气预报】（weather_info 必须基于此数据）"]
        for w in chosen:
            lines.append(
                f"- {w.date}：{w.day_weather}/{w.night_weather}，"
                f"{w.night_temp}~{w.day_temp}℃，{w.wind_direction}"
            )
        if len(chosen) < request.travel_days:
            lines.append(
                f"注：免费预报仅覆盖 {len(chosen)} 天，其余日期请按当季气候合理补全。"
            )
        return "\n".join(lines)

    def _build_user_prompt(
        self,
        request: TripRequest,
        poi_text: str,
        weather_text: str,
        extra_instruction: str = "",
    ) -> str:
        prompt = f"""请生成以下旅行计划：

- 目的地：{request.city}
- 日期：{request.start_date} 至 {request.end_date}（共 {request.travel_days} 天）
- 交通方式偏好：{request.transportation}
- 住宿偏好：{request.accommodation}
- 兴趣偏好：{', '.join(request.preferences) if request.preferences else '不限'}

{poi_text}

{weather_text}
{extra_instruction}"""
        if request.free_text_input:
            prompt += f"\n\n【用户额外要求】{request.free_text_input}"
        return prompt

    # ============ LLM 调用与解析 ============

    @staticmethod
    def _log_plan_decision(plan: TripPlan, elapsed: float) -> None:
        """决策结果落日志：每天选了哪些景点/酒店、预算，方便追溯 LLM 为什么这样排"""
        logger.info("🗺️  规划结果：%s %s~%s 共 %d 天，总耗时 %.1fs",
                    plan.city, plan.start_date, plan.end_date, len(plan.days), elapsed)
        for day in plan.days:
            spots = "、".join(a.name for a in day.attractions) or "（无景点）"
            hotel = f"｜住:{day.hotel.name}" if getattr(day, "hotel", None) else ""
            logger.info("  └ D%d %s：%s%s", day.day_index + 1, day.date, spots, hotel)

    async def plan_trip(self, request: TripRequest) -> TripPlan:
        """主流程：采集数据 → LLM 规划 → 解析校验（带一次修复重试）"""
        t_start = time.monotonic()
        logger.info("🚀 开始规划：%s %d天 (%s~%s)｜交通:%s｜住宿:%s｜偏好:%s",
                    request.city, request.travel_days, request.start_date, request.end_date,
                    request.transportation, request.accommodation,
                    request.preferences or "不限")

        pairs, hotels, weather = await self._gather_context(request)
        poi_text = self._format_pois(pairs, hotels)
        weather_text = self._format_weather(weather, request)
        prompt = self._build_user_prompt(request, poi_text, weather_text)

        loop = asyncio.get_running_loop()
        planner = self._new_planner()
        logger.info("🤖 LLM 规划调用开始（prompt %d 字符；推理模型较慢，耐心等）", len(prompt))
        t_llm = time.monotonic()
        raw = await loop.run_in_executor(None, planner.run, prompt)
        logger.info("✅ LLM 规划完成：耗时 %.1fs，输出 %d 字符，开始解析",
                    time.monotonic() - t_llm, len(raw))

        try:
            plan = self._parse_trip_plan(raw)
            self._log_plan_decision(plan, time.monotonic() - t_start)
            return plan
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("⚠️  首次解析失败（%s），带错误信息重试一次", exc)
            repair_prompt = self._build_user_prompt(
                request,
                poi_text,
                weather_text,
                extra_instruction=(
                    f"\n\n你上一次的输出无法解析，错误：{exc}。"
                    "请严格只输出符合结构的单个 JSON 对象，不要输出其他任何内容。"
                ),
            )
            repair_planner = self._new_planner()  # 干净上下文，避免失败历史干扰
            t_retry = time.monotonic()
            raw_retry = await loop.run_in_executor(
                None, repair_planner.run, repair_prompt
            )
            logger.info("🤖 修复重试输出 %d 字符，耗时 %.1fs",
                        len(raw_retry), time.monotonic() - t_retry)
            try:
                plan = self._parse_trip_plan(raw_retry)
                self._log_plan_decision(plan, time.monotonic() - t_start)
                return plan
            except Exception as exc2:  # noqa: BLE001
                logger.error("❌ 修复重试仍失败: %s，启用兜底行程", exc2)
                return self._create_fallback_plan(request)

    # ============ 阶段一：3 套方案大纲 ============

    async def plan_outlines(self, request: TripRequest) -> TripOutlineData:
        """生成 4 套风格不同的大纲（轻量 JSON，比完整计划快得多）"""
        t_start = time.monotonic()
        endpoint = "/api/trip/plan"
        logger.info("🚀 开始生成方案大纲：%s %d天 (%s~%s)%s",
                    request.city, request.travel_days, request.start_date, request.end_date,
                    "｜MOCK 模式" if request.mock_llm else "")

        if request.mock_llm:
            # 压测模式：不碰高德与 LLM，随机延迟后返回兜底大纲（零 token 成本）
            await asyncio.sleep(random.uniform(1.0, 3.0))
            plans = self._create_fallback_outlines(request)
            self._record(endpoint, "outline", "success",
                         int((time.monotonic() - t_start) * 1000), None, "", "", request)
            logger.info("🗺️  MOCK 大纲完成：%d 套方案，耗时 %.1fs",
                        len(plans), time.monotonic() - t_start)
            return TripOutlineData(
                city=request.city, start_date=request.start_date,
                end_date=request.end_date, plans=plans, weather_info=[])

        # 缓存/预制路线命中：零 LLM 调用直接返回（日期重排 + 天气重查）
        cached = await self._serve_outline_cache(request, t_start)
        if cached is not None:
            return cached

        # 成本熔断：缓存未命中、真要烧 token 了才查预算（命中路径零成本不拦截）
        ensure_budget(request.mock_llm)

        pairs, hotels, weather = await self._gather_context(request)
        poi_text = self._format_pois(pairs, hotels)
        weather_text = self._format_weather(weather, request)
        prompt = self._build_user_prompt(request, poi_text, weather_text)

        outline_prompt, outline_ver = get_prompt("outline")
        logger.info("🤖 大纲 LLM 调用开始（prompt %d 字符，%s）",
                    len(prompt), outline_ver)
        raw, usage, llm_ms, queue_ms = await self._timed_llm(outline_prompt, prompt)
        logger.info("✅ 大纲 LLM 完成：耗时 %.1fs，输出 %d 字符", llm_ms / 1000, len(raw))

        plans: List[PlanOutline]
        truncated = self._is_truncated(usage)
        outline_source = ""  # 兜底大纲时置 "fallback"（不入历史行程）
        try:
            if truncated:
                raise ValueError(f"输出被 max_tokens 截断（{len(raw)} 字符）")
            plans = self._parse_outlines(raw)
            self._record(endpoint, "outline", "success", llm_ms, usage, prompt, raw,
                         request, queue_ms=queue_ms)
            self._cache_outline_result(request, plans)
        except (ValueError, json.JSONDecodeError) as exc:
            # 截断与格式错误分开记账：截断说明预算不够，重试必须加预算+降信息量
            self._record(endpoint, "outline", "truncated" if truncated else "error",
                         llm_ms, usage, prompt, raw, request, error=str(exc),
                         queue_ms=queue_ms)
            if truncated:
                logger.warning("⚠️  大纲输出被截断（%d 字符），加预算降信息量重试一次", len(raw))
                repair_instruction = (
                    "\n\n你上一次因输出过长被 max_tokens 截断。请压缩篇幅："
                    "tagline/highlights/title 从简，每天景点不超过 3 个，"
                    "只输出符合结构的单个 JSON 对象。"
                )
                retry_budget = TRUNCATED_RETRY_MAX_TOKENS
            else:
                logger.warning("⚠️  大纲首次解析失败（%s），带错误信息重试一次", exc)
                repair_instruction = (
                    f"\n\n你上一次的输出无法解析，错误：{exc}。"
                    "请严格只输出符合结构的单个 JSON 对象，不要输出其他任何内容。"
                )
                retry_budget = None
            repair_prompt = self._build_user_prompt(
                request, poi_text, weather_text, extra_instruction=repair_instruction)
            raw_retry, usage2, ms2, qms2 = await self._timed_llm(
                outline_prompt, repair_prompt, max_tokens=retry_budget)
            try:
                if self._is_truncated(usage2):
                    raise ValueError(f"重试仍被截断（{len(raw_retry)} 字符）")
                plans = self._parse_outlines(raw_retry)
                self._record(endpoint, "outline_repair", "success", ms2, usage2,
                             repair_prompt, raw_retry, request,
                             max_tokens=retry_budget, attempt=2, queue_ms=qms2)
                self._cache_outline_result(request, plans)
            except Exception as exc2:  # noqa: BLE001
                self._record(endpoint, "outline_repair", "error", ms2, usage2,
                             repair_prompt, raw_retry, request, error=str(exc2),
                             max_tokens=retry_budget, attempt=2, queue_ms=qms2)
                logger.error("❌ 大纲修复重试仍失败: %s，启用兜底大纲", exc2)
                plans = self._create_fallback_outlines(request)
                outline_source = "fallback"

        for p in plans:
            spots = sum(len(d.spots) for d in p.days)
            logger.info("  └ 方案%d「%s」：%d 天 %d 个景点｜预算约 %s 元",
                        p.plan_id, p.style, len(p.days), spots, p.estimated_budget)
        logger.info("🗺️  大纲生成完成：%d 套方案，总耗时 %.1fs", len(plans), time.monotonic() - t_start)
        return TripOutlineData(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            plans=plans,
            weather_info=weather,
            source=outline_source,
        )

    @staticmethod
    def _parse_outlines(raw: str) -> List[PlanOutline]:
        """从 LLM 输出提取大纲 JSON 并校验"""
        text = raw.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("响应中未找到 JSON 对象")
        data = json.loads(text[start : end + 1])
        raw_plans = data.get("plans")
        if not isinstance(raw_plans, list) or not raw_plans:
            raise ValueError("JSON 中缺少非空 plans 数组")
        return [PlanOutline(**p) for p in raw_plans]

    @staticmethod
    def _create_fallback_outlines(request: TripRequest) -> List[PlanOutline]:
        """LLM 完全不可用时的 4 套兜底大纲（不编造坐标）"""
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        styles = [
            ("经典全景", "地标打卡一次逛全"),
            ("深度慢游", "少而精的松弛节奏"),
            ("经济实惠", "免费低价为主，省钱优先"),
            ("自然风光", "山水公园，亲近自然"),
        ]
        plans: List[PlanOutline] = []
        for idx, (style, tagline) in enumerate(styles, start=1):
            days = []
            for i in range(request.travel_days):
                current = start_date + timedelta(days=i)
                days.append(OutlineDay(
                    date=current.strftime("%Y-%m-%d"),
                    day_index=i,
                    title=f"第{i + 1}天：{request.city}{style}",
                    spots=[
                        OutlineSpot(name=f"{request.city}推荐景点{i * 2 + 1}",
                                    address=f"{request.city}（请以实时地图为准）"),
                        OutlineSpot(name=f"{request.city}推荐景点{i * 2 + 2}",
                                    address=f"{request.city}（请以实时地图为准）"),
                    ],
                ))
            plans.append(PlanOutline(
                plan_id=idx, style=style, tagline=tagline,
                highlights=["AI 服务暂不可用，仅供参考"], estimated_budget=0, days=days,
            ))
        return plans

    # ============ 阶段二：选定方案的完整详情 ============

    async def _gather_hotels_weather(
        self, request: TripRequest
    ) -> Tuple[List[POIInfo], List[WeatherInfo]]:
        """详情阶段的轻量采集：景点已被大纲锁定，只需酒店 + 天气"""
        if not self.settings.amap_enabled:
            return [], []
        loop = asyncio.get_running_loop()
        hotels, weather = await asyncio.gather(
            loop.run_in_executor(
                None, _safe, "酒店搜索", get_amap_service().search_poi,
                f"{request.accommodation}酒店", request.city, True),
            loop.run_in_executor(
                None, _safe, "天气查询", get_amap_service().get_weather, request.city),
        )
        return hotels or [], weather or []

    @staticmethod
    def _format_outline_spots(outline: PlanOutline, hotels: List[POIInfo]) -> str:
        """把选定大纲整理成「必须沿用」的 POI 文本，替代实时景点候选"""
        lines = ["【已选定方案大纲】（每天景点必须严格沿用，名称与坐标禁止更改，不得增删景点）",
                 f"方案风格：{outline.style}｜{outline.tagline}"]
        for day in outline.days:
            names = "、".join(s.name for s in day.spots)
            lines.append(f"- D{day.day_index + 1} {day.date}「{day.title}」：{names}")
            for spot in day.spots:
                coord = (f"{spot.location.longitude},{spot.location.latitude}"
                         if spot.location else "无坐标（禁止编造，location 输出 null）")
                lines.append(f"  · {spot.name}｜地址:{spot.address}｜坐标:{coord}")
        if hotels:
            lines.append("\n【实时酒店 POI 数据】")
            for poi in hotels[:4]:
                lines.append(
                    f"- {poi.name}｜地址:{poi.address}｜"
                    f"坐标:{poi.location.longitude},{poi.location.latitude}"
                    + (f"｜电话:{poi.tel}" if poi.tel else "")
                )
        return "\n".join(lines)

    async def plan_detail(self, request: TripRequest, outline: PlanOutline) -> TripPlan:
        """把选定大纲扩写成完整计划：景点锁定，补描述/餐饮/酒店（预算已拆到后台任务）"""
        t_start = time.monotonic()
        endpoint = "/api/trip/detail"
        logger.info("🚀 生成方案详情：%s 方案%d「%s」%s",
                    request.city, outline.plan_id, outline.style,
                    "｜MOCK 模式" if request.mock_llm else "")

        if request.mock_llm:
            await asyncio.sleep(random.uniform(1.0, 3.0))
            plan = self._create_fallback_plan(request)
            self._record(endpoint, "detail", "success",
                         int((time.monotonic() - t_start) * 1000), None, "", "",
                         request, plan_id=outline.plan_id, plan_style=outline.style)
            logger.info("🗺️  MOCK 详情完成：方案%d，耗时 %.1fs",
                        outline.plan_id, time.monotonic() - t_start)
            return plan

        # 详情缓存命中：零 LLM 调用直接返回（日期重排 + 天气重查）
        cache = get_cache_store()
        dkey = make_detail_key(request, outline)
        entry = cache.get(dkey)
        if entry is not None:
            plan = TripPlan(**rebase_plan_payload(
                entry["payload"], request.start_date, request.end_date))
            fresh_weather = await self._fetch_weather(request.city)
            if fresh_weather:
                plan = plan.model_copy(update={"weather_info": fresh_weather})
            # 旧版详情缓存里带预算：预算已改为后台任务生成、不随行程返回，统一剥除
            plan = plan.model_copy(update={"source": "cache", "budget": None})
            cache.touch(dkey)
            ms = int((time.monotonic() - t_start) * 1000)
            self._record(endpoint, "detail", "cache_hit", ms, None, "", "",
                         request, plan_id=outline.plan_id, plan_style=outline.style)
            logger.info("⚡ 详情缓存命中：%s 方案%d「%s」，耗时 %.1fs（零 LLM 调用）",
                        request.city, outline.plan_id, outline.style, ms / 1000)
            return plan

        # 成本熔断：详情缓存未命中、真要烧 token 了才查预算
        ensure_budget(request.mock_llm)

        hotels, weather = await self._gather_hotels_weather(request)
        weather_text = self._format_weather(weather, request)

        # 长行程分段：14 天一次出全量 JSON 必超 max_tokens 截断（实测 24K 预算
        # 只够 ~5 天详情），按 DETAIL_CHUNK_DAYS 切块多次生成再合并
        if len(outline.days) > DETAIL_CHUNK_DAYS:
            merged, degraded = await self._detail_chunked(
                request, outline, hotels, weather, weather_text, t_start)
            if not degraded:
                self._cache_detail_result(request, outline, merged)
            return merged

        plan = await self._detail_single(
            request, outline, hotels, weather_text, "detail", t_start)
        if plan is not None:
            self._log_plan_decision(plan, time.monotonic() - t_start)
            self._cache_detail_result(request, outline, plan)
            return plan
        logger.error("❌ 详情生成失败，启用兜底行程")
        return self._create_fallback_plan(request)

    async def _detail_single(
        self, request: TripRequest, outline: PlanOutline,
        hotels: List[POIInfo], weather_text: str, phase: str, t_start: float,
        day_scope: str = "",
    ) -> Optional[TripPlan]:
        """单次详情调用（含一次修复重试）；失败返回 None 由调用方降级。

        day_scope: 分块时形如「只生成第 6 至 10 天」的范围锁定说明。
        """
        endpoint = "/api/trip/detail"
        poi_text = self._format_outline_spots(outline, hotels)
        extra = (
            f"\n\n用户已从多套大纲中选定「{outline.style}」方案，"
            "每天景点安排已在【已选定方案大纲】中锁定：名称、坐标、每日选点一律不得更改、不得增删；"
            "你只需为每个景点补全介绍/游玩时长/门票，并安排三餐、酒店、交通。"
            "不需要输出预算汇总（预算由后续独立环节生成）。"
            "overall_suggestions 需契合该方案风格。"
        )
        if day_scope:
            extra += (
                f"\n{day_scope}：days 数组只包含【已选定方案大纲】中列出的这几天，"
                "day_index 与日期必须与之完全一致。"
            )
        prompt = self._build_user_prompt(request, poi_text, weather_text, extra_instruction=extra)

        planner_prompt, planner_ver = get_prompt("planner")
        logger.info("🤖 详情 LLM 调用开始（%s，prompt %d 字符，%s）",
                    phase, len(prompt), planner_ver)
        raw, usage, llm_ms, queue_ms = await self._timed_llm(planner_prompt, prompt)
        logger.info("✅ 详情 LLM 完成（%s）：耗时 %.1fs，输出 %d 字符",
                    phase, llm_ms / 1000, len(raw))

        truncated = self._is_truncated(usage)
        try:
            if truncated:
                raise ValueError(f"输出被 max_tokens 截断（{len(raw)} 字符）")
            plan = self._parse_trip_plan(raw)
            self._record(endpoint, phase, "success", llm_ms, usage, prompt, raw,
                         request, plan_id=outline.plan_id, plan_style=outline.style,
                         queue_ms=queue_ms)
            return plan
        except (ValueError, json.JSONDecodeError) as exc:
            self._record(endpoint, phase, "truncated" if truncated else "error",
                         llm_ms, usage, prompt, raw, request,
                         plan_id=outline.plan_id, plan_style=outline.style,
                         error=str(exc), queue_ms=queue_ms)
            if truncated:
                logger.warning("⚠️  详情输出被截断（%s，%d 字符），加预算重试一次",
                               phase, len(raw))
                repair_extra = extra + (
                    "\n\n你上一次因输出过长被 max_tokens 截断。请压缩篇幅："
                    "景点描述 50 字内、三餐描述 15 字内，只输出符合结构的单个 JSON 对象。"
                )
                retry_budget = TRUNCATED_RETRY_MAX_TOKENS
            else:
                logger.warning("⚠️  详情首次解析失败（%s），带错误信息重试一次", exc)
                repair_extra = extra + (
                    f"\n\n你上一次的输出无法解析，错误：{exc}。"
                    "请严格只输出符合结构的单个 JSON 对象，不要输出其他任何内容。"
                )
                retry_budget = None
            repair_prompt = self._build_user_prompt(
                request, poi_text, weather_text, extra_instruction=repair_extra)
            raw_retry, usage2, ms2, qms2 = await self._timed_llm(
                planner_prompt, repair_prompt, max_tokens=retry_budget)
            try:
                if self._is_truncated(usage2):
                    raise ValueError(f"重试仍被截断（{len(raw_retry)} 字符）")
                plan = self._parse_trip_plan(raw_retry)
                self._record(endpoint, f"{phase}_repair", "success", ms2, usage2,
                             repair_prompt, raw_retry, request,
                             plan_id=outline.plan_id, plan_style=outline.style,
                             max_tokens=retry_budget, attempt=2, queue_ms=qms2)
                return plan
            except Exception as exc2:  # noqa: BLE001
                self._record(endpoint, f"{phase}_repair", "error", ms2, usage2,
                             repair_prompt, raw_retry, request,
                             plan_id=outline.plan_id, plan_style=outline.style,
                             error=str(exc2), max_tokens=retry_budget, attempt=2,
                             queue_ms=qms2)
                logger.error("❌ 详情修复重试仍失败（%s）: %s", phase, exc2)
                return None

    # ============ 长行程分段详情 ============

    async def _detail_chunked(
        self, request: TripRequest, outline: PlanOutline,
        hotels: List[POIInfo], weather: List[WeatherInfo],
        weather_text: str, t_start: float,
    ) -> Tuple[TripPlan, bool]:
        """按天块分段生成详情再合并：单块失败只降级该块，不拖垮整趟行程。
        返回 (合并计划, 是否有降级块)：有降级块的计划质量不完整，不入缓存。"""
        blocks = [outline.days[i:i + DETAIL_CHUNK_DAYS]
                  for i in range(0, len(outline.days), DETAIL_CHUNK_DAYS)]
        logger.info("🧩 长行程分段生成：方案%d「%s」%d 天 → %d 段",
                    outline.plan_id, outline.style, len(outline.days), len(blocks))

        block_plans: List[TripPlan] = []
        degraded = False
        for bi, block_days in enumerate(blocks):
            sub_outline = PlanOutline(
                plan_id=outline.plan_id, style=outline.style,
                tagline=outline.tagline, days=block_days)
            scope = (f"本段只生成第 {block_days[0].day_index + 1} 天至"
                     f"第 {block_days[-1].day_index + 1} 天")
            plan = await self._detail_single(
                request, sub_outline, hotels, weather_text,
                f"detail_b{bi + 1}", t_start, day_scope=scope)
            if plan is None:
                logger.warning("⚠️  第 %d 段详情生成失败，该段降级为兜底行程", bi + 1)
                plan = self._fallback_block_plan(request, block_days)
                degraded = True
            block_plans.append(plan)

        merged = self._merge_block_plans(request, outline, block_plans, weather)
        self._log_plan_decision(merged, time.monotonic() - t_start)
        return merged, degraded

    def _fallback_block_plan(self, request: TripRequest,
                             block_days: List[OutlineDay]) -> TripPlan:
        """单块兜底：复用整体兜底行程，只保留本块覆盖的天"""
        full = self._create_fallback_plan(request)
        wanted = {d.day_index for d in block_days}
        full.days = [d for d in full.days if d.day_index in wanted]
        return full

    @staticmethod
    def _merge_block_plans(
        request: TripRequest, outline: PlanOutline,
        blocks: List[TripPlan], weather: List[WeatherInfo],
    ) -> TripPlan:
        """合并分段详情：days 按 day_index 排序拼接（预算已不在详情阶段生成）"""
        days = sorted((d for bp in blocks for d in bp.days),
                      key=lambda d: d.day_index)
        suggestions = next(
            (bp.overall_suggestions for bp in blocks if bp.overall_suggestions), "")
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=weather,
            overall_suggestions=suggestions or f"{outline.style}方案（长行程分段生成）",
        )

    @staticmethod
    def _parse_trip_plan(raw: str) -> TripPlan:
        """从 LLM 输出中提取 JSON 并校验为 TripPlan"""
        text = raw.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("响应中未找到 JSON 对象")
        data = json.loads(text[start : end + 1])
        return TripPlan(**data)

    # ============ 阶段三：预算（路线定稿后由后台任务独立生成，不进行程库表） ============

    @staticmethod
    def _compute_budget(plan: TripPlan) -> Budget:
        """确定性口径：按明细求和（市内交通按 30 元/人/天兜底），mock 与 LLM 失败时用"""
        total_attractions = sum(a.ticket_price for d in plan.days for a in d.attractions)
        total_hotels = sum(d.hotel.estimated_cost for d in plan.days if d.hotel)
        total_meals = sum(m.estimated_cost for d in plan.days for m in d.meals)
        total_transportation = 30 * len(plan.days)
        return Budget(
            total_attractions=total_attractions,
            total_hotels=total_hotels,
            total_meals=total_meals,
            total_transportation=total_transportation,
            total=total_attractions + total_hotels + total_meals + total_transportation,
        )

    @staticmethod
    def _parse_budget(raw: str) -> Budget:
        """从 LLM 输出提取预算 JSON（兼容被 {"budget": {...}} 外壳包裹）"""
        text = raw.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("响应中未找到 JSON 对象")
        data = json.loads(text[start : end + 1])
        if isinstance(data.get("budget"), dict):
            data = data["budget"]
        return Budget(**data)

    @staticmethod
    def _build_budget_prompt(plan: TripPlan,
                             request: Optional[TripRequest] = None) -> str:
        """把已定稿路线压成紧凑「账单」：预算调用只需价格信息，不带描述/坐标/天气"""
        meal_labels = {"breakfast": "早餐", "lunch": "午餐",
                       "dinner": "晚餐", "snack": "小吃"}
        lines = [f"【城市】{plan.city}", f"【天数】{len(plan.days)}"]
        if request:
            lines.append(f"【交通偏好】{request.transportation}")
            lines.append(f"【住宿偏好】{request.accommodation}")
            if request.free_text_input:
                lines.append(f"【用户额外要求】{request.free_text_input}")
        lines.append("【路线账单】")
        for day in sorted(plan.days, key=lambda d: d.day_index):
            lines.append(f"D{day.day_index + 1} 交通:{day.transportation}")
            if day.attractions:
                lines.append("  景点: " + "、".join(
                    f"{a.name}(门票{a.ticket_price}元)" for a in day.attractions))
            if day.hotel:
                lines.append(f"  酒店: {day.hotel.name}({day.hotel.estimated_cost}元/晚)")
            if day.meals:
                lines.append("  餐饮: " + "、".join(
                    f"{meal_labels.get(m.type, m.type)} {m.name}({m.estimated_cost}元)"
                    for m in day.meals))
        return "\n".join(lines)

    async def plan_budget(self, plan: TripPlan,
                          request: Optional[TripRequest] = None) -> Tuple[Budget, str]:
        """路线定稿后的独立预算调用。返回 (预算, 来源 llm/mock)。

        - mock 请求：明细确定性求和，零 token（压测/演示）
        - 真实请求：过成本熔断 + 并发闸门，一次小调用（max_tokens=1024），
          解析失败带错误重试一次，再失败降级为确定性求和
        """
        t_start = time.monotonic()
        is_mock = bool(request and request.mock_llm)
        if is_mock:
            budget = self._compute_budget(plan)
            if request:
                self._record("/api/jobs", "budget", "success",
                             int((time.monotonic() - t_start) * 1000), None, "", "",
                             request)
            logger.info("💰 MOCK 预算完成：%s → 合计 %d 元", plan.city, budget.total)
            return budget, "mock"

        ensure_budget(False)  # 成本熔断：真要烧 token 前查每日预算
        prompt = self._build_budget_prompt(plan, request)
        budget_prompt, budget_ver = get_prompt("budget")
        logger.info("🤖 预算 LLM 调用开始（prompt %d 字符，%s）", len(prompt), budget_ver)
        raw, usage, llm_ms, queue_ms = await self._timed_llm(
            budget_prompt, prompt, max_tokens=BUDGET_MAX_TOKENS)
        source = "llm"
        try:
            budget = self._parse_budget(raw)
            if request:
                self._record("/api/jobs", "budget", "success", llm_ms, usage,
                             prompt, raw, request, queue_ms=queue_ms)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("⚠️  预算首次解析失败（%s），带错误信息重试一次", exc)
            if request:
                self._record("/api/jobs", "budget", "error", llm_ms, usage,
                             prompt, raw, request, error=str(exc), queue_ms=queue_ms)
            repair_prompt = prompt + (
                f"\n\n你上一次的输出无法解析，错误：{exc}。"
                "请严格只输出符合结构的单个 JSON 对象，不要输出其他任何内容。"
            )
            raw_retry, usage2, ms2, qms2 = await self._timed_llm(
                budget_prompt, repair_prompt, max_tokens=BUDGET_MAX_TOKENS)
            try:
                budget = self._parse_budget(raw_retry)
                if request:
                    self._record("/api/jobs", "budget_repair", "success", ms2, usage2,
                                 repair_prompt, raw_retry, request, attempt=2,
                                 queue_ms=qms2)
            except Exception as exc2:  # noqa: BLE001
                logger.error("❌ 预算修复重试仍失败（%s），降级为明细确定性求和", exc2)
                if request:
                    self._record("/api/jobs", "budget_repair", "error", ms2, usage2,
                                 repair_prompt, raw_retry, request, error=str(exc2),
                                 attempt=2, queue_ms=qms2)
                budget = self._compute_budget(plan)
                source = "fallback"  # 两次解析都失败：结果是确定性求和而非 LLM 产出
        logger.info("💰 预算完成：%s 合计 %d 元（门票 %d｜住宿 %d｜餐饮 %d｜交通 %d，%.1fs）",
                    plan.city, budget.total, budget.total_attractions,
                    budget.total_hotels, budget.total_meals,
                    budget.total_transportation, time.monotonic() - t_start)
        return budget, source

    # ============ 兜底 ============

    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """LLM 完全不可用时的兜底行程（不编造坐标）"""
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        days: List[DayPlan] = []
        for i in range(request.travel_days):
            current = start_date + timedelta(days=i)
            days.append(
                DayPlan(
                    date=current.strftime("%Y-%m-%d"),
                    day_index=i,
                    description=f"第{i + 1}天：{request.city}深度游",
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    attractions=[
                        Attraction(
                            name=f"{request.city}推荐景点{i * 2 + 1}",
                            address=f"{request.city}（请以实时地图为准）",
                            location=None,
                            visit_duration=120,
                            description="AI 服务暂不可用，请到地图 App 核实后前往。",
                            category="景点",
                        ),
                        Attraction(
                            name=f"{request.city}推荐景点{i * 2 + 2}",
                            address=f"{request.city}（请以实时地图为准）",
                            location=None,
                            visit_duration=120,
                            description="AI 服务暂不可用，请到地图 App 核实后前往。",
                            category="景点",
                        ),
                    ],
                    meals=[
                        Meal(type="breakfast", name="酒店早餐或当地早点", description="当地特色早餐", estimated_cost=30),
                        Meal(type="lunch", name="当地口碑餐厅", description="建议查看点评 App", estimated_cost=60),
                        Meal(type="dinner", name="特色美食街", description="品尝当地特色", estimated_cost=80),
                    ],
                )
            )
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions="规划服务暂时不可用，此为兜底行程，请结合地图 App 自行核实景点与营业信息。",
            source="fallback",  # 兜底不入缓存、不持久化（调用方按此标记跳过）
        )


# 全局单例
_trip_planner: Optional[TripPlannerService] = None


def get_trip_planner_agent() -> TripPlannerService:
    """获取旅行规划服务实例（单例）"""
    global _trip_planner
    if _trip_planner is None:
        _trip_planner = TripPlannerService()
    return _trip_planner
