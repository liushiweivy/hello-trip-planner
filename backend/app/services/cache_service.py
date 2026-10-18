# -*- coding: utf-8 -*-
"""规划结果缓存 + 热门城市预制路线（MySQL，连不上降级内存）。

- 缓存键：归一化请求（城市/天数/交通/住宿/排序偏好/自由文本）+ 类别 → sha256。
  **日期不进键**：行程结构不随出发日变化，serve 时按请求日期 + day_index 重排日期，
  天气也实时重查——缓存只缓存 LLM 的「创作成果」（选点+文案），不缓存易过期数据
- 详情键：额外混入大纲风格 + 每天景点名签名（同一天数不同大纲要分别缓存）
- 预制路线：seed_data.SEED_OUTLINES（人工编写，零 token），kind=outline、seed=1、
  永不过期，首次启动幂等 upsert；命中后坐标缺失的景点用高德反查补全并回写
- 命中记 request_metrics status=cache_hit，压测监控页可见
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .seed_data import SEED_OUTLINES

logger = logging.getLogger("trip.cache")

TABLE = "plan_cache"

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  cache_key VARCHAR(80) NOT NULL UNIQUE,
  kind VARCHAR(8) NOT NULL,
  seed TINYINT NOT NULL DEFAULT 0,
  city VARCHAR(64) DEFAULT '',
  travel_days INT DEFAULT 0,
  plan_id INT DEFAULT NULL,
  payload MEDIUMTEXT NOT NULL,
  hit_count INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_hit_at DATETIME NULL,
  expires_at DATETIME NULL,
  INDEX idx_city (city, kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


# ============ 缓存键归一化 ============

def _norm_request(request) -> Dict[str, Any]:
    """请求归一化：日期/client_tag/mock 不参与（日期 serve 时重排）"""
    return {
        "city": (request.city or "").strip(),
        "days": request.travel_days,
        "trans": (request.transportation or "").strip(),
        "acc": (request.accommodation or "").strip(),
        "pref": sorted(p.strip() for p in (request.preferences or []) if p.strip()),
        "free": " ".join((request.free_text_input or "").split()),
    }


def make_outline_key(request) -> str:
    norm = _norm_request(request)
    norm["kind"] = "outline"
    raw = json.dumps(norm, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_detail_key(request, outline) -> str:
    """详情键：同一请求下不同大纲（风格/选点）必须分开缓存。
    签名用 天序号+景点名（不含日期，日期 serve 时重排）。"""
    norm = _norm_request(request)
    norm["kind"] = "detail"
    norm["style"] = outline.style
    norm["sig"] = [[d.day_index, [s.name for s in d.spots]] for d in outline.days]
    raw = json.dumps(norm, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def seed_eligible(request) -> bool:
    """预制路线适用条件：无偏好标签、无自由文本（交通/住宿不限制，路线是通用的）"""
    return not [p for p in (request.preferences or []) if p.strip()] and \
        not (request.free_text_input or "").strip()


def _seed_key(city: str, travel_days: int) -> str:
    return f"seed:{city}:{travel_days}:outline"


# ============ 日期重排（serve 时把缓存日期对齐到请求日期） ============

def rebase_outline_payload(plans_data: List[Dict[str, Any]], start_date: str) -> List[Dict[str, Any]]:
    """大纲 payload 按新出发日重排每天日期（纯函数，不改入参）"""
    base = datetime.strptime(start_date, "%Y-%m-%d")
    out = []
    for plan in plans_data:
        new_plan = dict(plan)
        new_days = []
        for d in plan.get("days", []):
            nd = dict(d)
            nd["date"] = (base + timedelta(days=d["day_index"])).strftime("%Y-%m-%d")
            new_days.append(nd)
        new_plan["days"] = new_days
        out.append(new_plan)
    return out


def rebase_plan_payload(plan_data: Dict[str, Any], start_date: str, end_date: str) -> Dict[str, Any]:
    """详情 payload（TripPlan dict）重排日期 + 对齐起止日"""
    base = datetime.strptime(start_date, "%Y-%m-%d")
    out = dict(plan_data)
    out["start_date"] = start_date
    out["end_date"] = end_date
    new_days = []
    for d in plan_data.get("days", []):
        nd = dict(d)
        nd["date"] = (base + timedelta(days=d["day_index"])).strftime("%Y-%m-%d")
        new_days.append(nd)
    out["days"] = new_days
    return out


# ============ 存储 ============

def _row_to_entry(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """校验过期并解析 payload；过期返回 None"""
    exp = row.get("expires_at")
    if exp:
        exp_s = str(exp)
        if exp_s <= time.strftime("%Y-%m-%d %H:%M:%S"):
            return None
    try:
        row["payload"] = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        return None
    return row


class MemoryCacheStore:
    """内存存储：单测与 MySQL 降级用"""

    backend = "memory"

    def __init__(self):
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.ensure_seeds()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._rows.get(key)
            return _row_to_entry(dict(row)) if row else None

    def put(self, key: str, kind: str, city: str, travel_days: int,
            plan_id: Optional[int], payload: Dict[str, Any],
            seed: bool = False, ttl_days: Optional[int] = None) -> None:
        expires = None
        if ttl_days:
            expires = (datetime.now() + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            old = self._rows.get(key, {})
            self._rows[key] = {
                "cache_key": key, "kind": kind, "seed": 1 if seed else 0,
                "city": city, "travel_days": travel_days, "plan_id": plan_id,
                "payload": json.dumps(payload, ensure_ascii=False),
                "hit_count": old.get("hit_count", 0),
                "created_at": old.get("created_at", time.strftime("%Y-%m-%d %H:%M:%S")),
                "last_hit_at": old.get("last_hit_at"),
                "expires_at": None if seed else expires,
            }

    def touch(self, key: str) -> None:
        with self._lock:
            if key in self._rows:
                self._rows[key]["hit_count"] += 1
                self._rows[key]["last_hit_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def update_payload(self, key: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            if key in self._rows:
                self._rows[key]["payload"] = json.dumps(payload, ensure_ascii=False)

    def get_seed_outline(self, city: str, travel_days: int) -> Optional[Dict[str, Any]]:
        return self.get(_seed_key(city, travel_days))

    def ensure_seeds(self) -> int:
        """把预制路线灌进存储（幂等 upsert，seed 数据更新后重启即生效）"""
        count = 0
        for city, plans in SEED_OUTLINES.items():
            normalized = []
            for plan in plans:
                p = dict(plan)
                p["days"] = [dict(d, date="") for d in p["days"]]
                normalized.append(p)
            self.put(_seed_key(city, 3), "outline", city, 3, None,
                     {"plans": normalized}, seed=True)
            count += len(plans)
        logger.info("🌱 预制路线就绪：%d 城市 %d 套 3 天大纲", len(SEED_OUTLINES), count)
        return count

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            rows = [_row_to_entry(dict(r)) for r in self._rows.values()]
        rows = [r for r in rows if r]
        by_kind: Dict[str, int] = {}
        for r in rows:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        top = sorted(rows, key=lambda r: -r["hit_count"])[:10]
        return {
            "total": len(rows),
            "by_kind": by_kind,
            "seed_count": sum(1 for r in rows if r["seed"]),
            "total_hits": sum(r["hit_count"] for r in rows),
            "top_hits": [
                {"city": r["city"], "kind": r["kind"], "seed": r["seed"],
                 "travel_days": r["travel_days"], "hit_count": r["hit_count"],
                 "last_hit_at": r.get("last_hit_at")}
                for r in top
            ],
        }

    def clear(self, city: str = "", kind: str = "", include_seed: bool = False) -> int:
        with self._lock:
            keys = [
                k for k, r in self._rows.items()
                if (not city or r["city"] == city)
                and (not kind or r["kind"] == kind)
                and (include_seed or not r["seed"])
            ]
            for k in keys:
                del self._rows[k]
        return len(keys)


class MySQLCacheStore:
    """MySQL 存储：复用 trip_planner 库，自动建表，断连重试一次"""

    backend = "mysql"

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._ensure_schema()
        self.ensure_seeds()

    def _connect(self, with_db: bool = True):
        import pymysql  # 延迟导入：未装 pymysql 时降级内存存储
        kwargs = dict(
            host=self._cfg.get("host", "127.0.0.1"),
            port=int(self._cfg.get("port", 3306)),
            user=self._cfg.get("user", "root"),
            password=self._cfg.get("password", ""),
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
        )
        if with_db:
            kwargs["database"] = self._cfg["database"]
        return pymysql.connect(**kwargs)

    def _ensure_schema(self) -> None:
        db = self._cfg["database"]
        with self._connect(with_db=False) as conn, conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` DEFAULT CHARSET utf8mb4")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        logger.info("⚡ 缓存表就绪：MySQL %s.%s", self._cfg.get("host"), db)

    def _execute(self, sql: str, params: Any = None, retry: bool = True):
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
            except Exception:  # noqa: BLE001 - 断连重试一次
                if not retry:
                    raise
                logger.warning("⚡ 缓存 MySQL 操作失败，重连重试一次", exc_info=True)
                return self._execute(sql, params, retry=False)

    _COLS = ("cache_key", "kind", "seed", "city", "travel_days", "plan_id",
             "payload", "hit_count", "created_at", "last_hit_at", "expires_at")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        rows = self._execute(
            f"SELECT {', '.join(self._COLS)} FROM {TABLE} WHERE cache_key = %s LIMIT 1",
            (key,))
        if not rows:
            return None
        row = dict(zip(self._COLS, rows[0]))
        row["hit_count"] = int(row["hit_count"])
        row["seed"] = int(row["seed"])
        return _row_to_entry(row)

    def put(self, key: str, kind: str, city: str, travel_days: int,
            plan_id: Optional[int], payload: Dict[str, Any],
            seed: bool = False, ttl_days: Optional[int] = None) -> None:
        expires = None
        if ttl_days and not seed:
            expires = (datetime.now() + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        self._execute(
            f"INSERT INTO {TABLE} (cache_key, kind, seed, city, travel_days, plan_id,"
            " payload, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON DUPLICATE KEY UPDATE payload = VALUES(payload),"
            " expires_at = VALUES(expires_at)",
            (key, kind, 1 if seed else 0, city, travel_days, plan_id,
             json.dumps(payload, ensure_ascii=False), expires))

    def touch(self, key: str) -> None:
        self._execute(
            f"UPDATE {TABLE} SET hit_count = hit_count + 1, last_hit_at = NOW()"
            " WHERE cache_key = %s", (key,))

    def update_payload(self, key: str, payload: Dict[str, Any]) -> None:
        self._execute(
            f"UPDATE {TABLE} SET payload = %s WHERE cache_key = %s",
            (json.dumps(payload, ensure_ascii=False), key))

    def get_seed_outline(self, city: str, travel_days: int) -> Optional[Dict[str, Any]]:
        return self.get(_seed_key(city, travel_days))

    def ensure_seeds(self) -> int:
        count = 0
        for city, plans in SEED_OUTLINES.items():
            normalized = []
            for plan in plans:
                p = dict(plan)
                p["days"] = [dict(d, date="") for d in p["days"]]
                normalized.append(p)
            self.put(_seed_key(city, 3), "outline", city, 3, None,
                     {"plans": normalized}, seed=True)
            count += len(plans)
        logger.info("🌱 预制路线就绪：%d 城市 %d 套 3 天大纲", len(SEED_OUTLINES), count)
        return count

    def stats(self) -> Dict[str, Any]:
        (total, hits, seeds) = self._execute(
            f"SELECT COUNT(*), COALESCE(SUM(hit_count), 0), COALESCE(SUM(seed), 0)"
            f" FROM {TABLE}")[0]
        by_kind = {k: c for k, c in self._execute(
            f"SELECT kind, COUNT(*) FROM {TABLE} GROUP BY kind")}
        top = self._execute(
            f"SELECT city, kind, seed, travel_days, hit_count, last_hit_at"
            f" FROM {TABLE} ORDER BY hit_count DESC LIMIT 10")
        return {
            "total": int(total),
            "by_kind": by_kind,
            "seed_count": int(seeds),
            "total_hits": int(hits),
            "top_hits": [
                {"city": c, "kind": k, "seed": int(s), "travel_days": d,
                 "hit_count": int(h), "last_hit_at": str(t) if t else None}
                for c, k, s, d, h, t in top
            ],
        }

    def clear(self, city: str = "", kind: str = "", include_seed: bool = False) -> int:
        clauses, params = [], []
        if city:
            clauses.append("city = %s")
            params.append(city)
        if kind:
            clauses.append("kind = %s")
            params.append(kind)
        if not include_seed:
            clauses.append("seed = 0")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # MySQL rowCount 在 autocommit cursor 上可靠
        with self._lock:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {TABLE}{where}", tuple(params))
                return cur.rowcount


# ---------------- 单例与降级 ----------------

_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_cache_store():
    """缓存存储单例：MySQL 优先，连不上降级内存（与监控同一库）"""
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        from ..config import get_settings
        s = get_settings()
        cfg = {
            "host": s.metrics_mysql_host,
            "port": s.metrics_mysql_port,
            "user": s.metrics_mysql_user,
            "password": s.metrics_mysql_password,
            "database": s.metrics_mysql_database,
        }
        try:
            _store = MySQLCacheStore(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚡ MySQL 缓存不可用（%s），降级内存存储（重启丢缓存）", exc)
            _store = MemoryCacheStore()
        return _store


def reset_cache_store() -> None:
    """重置单例（测试用）"""
    global _store
    with _store_lock:
        _store = None
