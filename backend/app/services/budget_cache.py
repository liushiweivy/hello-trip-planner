# -*- coding: utf-8 -*-
"""预算短期缓存：预算不随行程入库，只活在 Redis（默认 24h TTL）。

- 缓存键是路线「账单内容」的 sha256：景点门票/酒店价/餐饮价/交通住宿，
  不含日期、天气、坐标、描述文本——同一路线换期出行命中同一份预算。
- Redis 优先（settings.redis_url，默认 db=1 与本机其他项目隔离）；
  未装 redis 包/服务不通时降级进程内 TTL dict（重启即失，正合「预算不持久化」）。
- 单例在首次使用时定型，Redis 中途故障不自动切回（与项目其他存储同约定：重启恢复）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("trip.budgetcache")

KEY_PREFIX = "trip:budget:"


def make_budget_key(plan: Any, request: Any = None) -> str:
    """路线账单内容的 sha256（TripPlan 或等价 dict 均可）。

    只取影响花费的字段；刻意排除日期/天气/坐标/描述（换期、换天气不重算预算）。
    请求级的交通/住宿偏好与自由文本也影响预算口径（公交 vs 打车差数倍、
    自由文本可能含往返大交通），有 request 时一并入键。
    """
    data = plan if isinstance(plan, dict) else plan.model_dump(mode="json")
    days = sorted(data["days"], key=lambda d: d["day_index"])
    norm_days = []
    for day in days:
        hotel = day.get("hotel")
        norm_days.append({
            "transport": day.get("transportation", ""),
            "hotel": ({"n": hotel.get("name", ""), "c": hotel.get("estimated_cost", 0)}
                      if hotel else None),
            "attractions": [{"n": a.get("name", ""), "c": a.get("ticket_price", 0),
                             "cat": a.get("category") or ""}
                            for a in day.get("attractions", [])],
            "meals": [{"tp": m.get("type", ""), "c": m.get("estimated_cost", 0)}
                      for m in day.get("meals", [])],
        })
    material: Dict[str, Any] = {"city": data.get("city", ""), "days": norm_days}
    if request is not None:
        req = request if isinstance(request, dict) else request.model_dump(mode="json")
        material["req"] = {
            "tr": req.get("transportation", ""),
            "acc": req.get("accommodation", ""),
            "ft": req.get("free_text_input") or "",
        }
    raw = json.dumps(material,
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def budget_idempotency_key(plan: Any, request: Any = None) -> str:
    """budget 任务的服务端幂等键：同一路线同一天内重复提交必然命中同一任务。

    跨天自然产生新键，但 worker 先查 Redis——24h TTL 未过仍零 LLM 返回。
    """
    return f"budget-{make_budget_key(plan, request)[:16]}-{time.strftime('%Y%m%d')}"


class MemoryBudgetCache:
    """进程内 TTL 缓存：单测与 Redis 降级用（不持久化）"""

    backend = "memory"

    def __init__(self):
        self._rows: Dict[str, tuple] = {}  # key -> (expire_ts, entry)
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._rows.get(KEY_PREFIX + key)
            if row is None:
                return None
            expire_ts, entry = row
            if expire_ts <= time.time():
                self._rows.pop(KEY_PREFIX + key, None)
                return None
            return entry

    def put(self, key: str, entry: Dict[str, Any], ttl_sec: int) -> None:
        with self._lock:
            self._rows[KEY_PREFIX + key] = (time.time() + max(1, ttl_sec), entry)


class RedisBudgetCache:
    """Redis TTL 缓存：短超时，任何操作失败由调用层降级，不拖垮主流程"""

    backend = "redis"

    def __init__(self, url: str):
        import redis  # 延迟导入：未安装时走内存降级
        self._client = redis.Redis.from_url(
            url, socket_connect_timeout=0.5, socket_timeout=0.5,
            decode_responses=True)
        self._client.ping()  # 构造即探活：失败就换内存实现

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self._client.get(KEY_PREFIX + key)
        return json.loads(raw) if raw else None

    def put(self, key: str, entry: Dict[str, Any], ttl_sec: int) -> None:
        self._client.set(KEY_PREFIX + key,
                         json.dumps(entry, ensure_ascii=False), ex=max(1, ttl_sec))


# ---------------- 单例与降级 ----------------

_cache: Optional[Any] = None
_cache_lock = threading.Lock()


def get_budget_cache():
    """预算缓存单例：Redis 优先，连不上降级内存"""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        from ..config import get_settings
        url = get_settings().redis_url
        try:
            _cache = RedisBudgetCache(url)
            logger.info("💰 预算缓存就绪：Redis %s", url)
        except Exception as exc:  # noqa: BLE001 - 未装包/服务不通都降级
            logger.warning("💰 Redis 预算缓存不可用（%s），降级进程内 TTL 缓存（重启即失）", exc)
            _cache = MemoryBudgetCache()
        return _cache


def reset_budget_cache() -> None:
    """重置单例（测试用）"""
    global _cache
    with _cache_lock:
        _cache = None
