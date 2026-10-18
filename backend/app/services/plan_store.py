# -*- coding: utf-8 -*-
"""行程持久化：用户历史行程（大纲 + 各方案详情）落 MySQL，前端可回放。

- trip_records：一次大纲生成 = 一条记录（owner_key 归属、请求快照、4 套大纲 payload）
- trip_details：某记录下某方案的完整详情（record_id + plan_id 唯一）
- 无鉴权阶段的轻量归属：前端 localStorage 生成 client_id，随 X-Owner-Key 头携带；
  读写都按 owner_key 过滤（防串号，不是安全边界，鉴权上线后替换为 user_id）
- MySQL 连不上自动降级内存存储；任何写入失败都不允许影响主流程
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trip.plans")

RECORDS_TABLE = "trip_records"
DETAILS_TABLE = "trip_details"

_CREATE_RECORDS_SQL = f"""
CREATE TABLE IF NOT EXISTS {RECORDS_TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  owner_key VARCHAR(64) NOT NULL,
  city VARCHAR(64) NOT NULL DEFAULT '',
  travel_days INT NOT NULL DEFAULT 0,
  start_date VARCHAR(10) NOT NULL DEFAULT '',
  end_date VARCHAR(10) NOT NULL DEFAULT '',
  transportation VARCHAR(32) NOT NULL DEFAULT '',
  accommodation VARCHAR(32) NOT NULL DEFAULT '',
  prefs VARCHAR(255) NOT NULL DEFAULT '',
  free_text VARCHAR(500) NOT NULL DEFAULT '',
  styles VARCHAR(255) NOT NULL DEFAULT '',
  source VARCHAR(16) NOT NULL DEFAULT '',
  request_json TEXT NOT NULL,
  outlines_json MEDIUMTEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_owner_created (owner_key, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_DETAILS_SQL = f"""
CREATE TABLE IF NOT EXISTS {DETAILS_TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  record_id BIGINT NOT NULL,
  plan_id INT NOT NULL,
  plan_style VARCHAR(64) NOT NULL DEFAULT '',
  payload MEDIUMTEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_record_plan (record_id, plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _summary_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """列表页摘要（不带 payload 大字段）"""
    return {
        "id": row["id"], "city": row["city"], "travel_days": row["travel_days"],
        "start_date": row["start_date"], "end_date": row["end_date"],
        "prefs": row["prefs"], "styles": row["styles"], "source": row["source"],
        "created_at": str(row["created_at"]),
    }


class MemoryPlanStore:
    """内存存储：单测与 MySQL 降级用"""

    backend = "memory"

    def __init__(self):
        self._records: List[Dict[str, Any]] = []
        self._details: Dict[tuple, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save_record(self, owner_key: str, request: Dict[str, Any],
                    outlines: Dict[str, Any]) -> int:
        with self._lock:
            rid = len(self._records) + 1
            self._records.append({
                "id": rid, "owner_key": owner_key,
                "city": request.get("city", ""),
                "travel_days": request.get("travel_days", 0),
                "start_date": request.get("start_date", ""),
                "end_date": request.get("end_date", ""),
                "transportation": request.get("transportation", ""),
                "accommodation": request.get("accommodation", ""),
                "prefs": "、".join(request.get("preferences") or []),
                "free_text": (request.get("free_text_input") or "")[:500],
                "styles": "、".join(p.get("style", "") for p in outlines.get("plans", [])),
                "source": outlines.get("source", "") or "llm",
                "request_json": json.dumps(request, ensure_ascii=False),
                "outlines_json": json.dumps(outlines, ensure_ascii=False),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            return rid

    def list_records(self, owner_key: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [r for r in reversed(self._records) if r["owner_key"] == owner_key]
        return [_summary_of(r) for r in rows[:limit]]

    def get_record(self, owner_key: str, record_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = next((r for r in self._records
                        if r["id"] == record_id and r["owner_key"] == owner_key), None)
        if row is None:
            return None
        result = _summary_of(row)
        result["request"] = json.loads(row["request_json"])
        result["outlines"] = json.loads(row["outlines_json"])
        return result

    def delete_record(self, owner_key: str, record_id: int) -> bool:
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records
                             if not (r["id"] == record_id and r["owner_key"] == owner_key)]
            removed = len(self._records) < before
            if removed:
                self._details = {k: v for k, v in self._details.items() if k[0] != record_id}
            return removed

    def save_detail(self, record_id: int, plan_id: int, plan_style: str,
                    payload: Dict[str, Any]) -> None:
        with self._lock:
            self._details[(record_id, plan_id)] = {
                "record_id": record_id, "plan_id": plan_id, "plan_style": plan_style,
                "payload": dict(payload),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

    def get_detail(self, record_id: int, plan_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._details.get((record_id, plan_id))
        return dict(row["payload"]) if row else None


class MySQLPlanStore:
    """MySQL 存储：自动建表，连接断开自动重连一次"""

    backend = "mysql"

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        import pymysql  # 延迟导入：未装 pymysql 时降级内存存储
        return pymysql.connect(
            host=self._cfg.get("host", "127.0.0.1"),
            port=int(self._cfg.get("port", 3306)),
            user=self._cfg.get("user", "root"),
            password=self._cfg.get("password", ""),
            database=self._cfg["database"],
            charset="utf8mb4", autocommit=True,
            connect_timeout=5, read_timeout=10, write_timeout=10,
        )

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_CREATE_RECORDS_SQL)
            cur.execute(_CREATE_DETAILS_SQL)
        logger.info("🗂️  行程表就绪：MySQL %s.%s（%s/%s）",
                    self._cfg.get("host"), self._cfg["database"],
                    RECORDS_TABLE, DETAILS_TABLE)

    def _execute(self, sql: str, params: Any = None, retry: bool = True):
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
            except Exception:  # noqa: BLE001 - 断连重试一次
                if not retry:
                    raise
                logger.warning("🗂️  MySQL 操作失败，重连重试一次", exc_info=True)
                return self._execute(sql, params, retry=False)

    def _execute_modify(self, sql: str, params: Any = None, retry: bool = True) -> int:
        """写操作，返回影响行数（rowcount 必须在同一游标上取，不能跨连接 SELECT ROW_COUNT()）"""
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.rowcount
            except Exception:  # noqa: BLE001
                if not retry:
                    raise
                logger.warning("🗂️  MySQL 写操作失败，重连重试一次", exc_info=True)
                return self._execute_modify(sql, params, retry=False)

    def _execute_insert(self, sql: str, params: Any = None, retry: bool = True) -> int:
        """插入并返回自增 ID（lastrowid 必须在同一游标上取，跨连接 SELECT LAST_INSERT_ID() 恒为 0）"""
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(sql, params)
                    return int(cur.lastrowid)
            except Exception:  # noqa: BLE001
                if not retry:
                    raise
                logger.warning("🗂️  MySQL 插入失败，重连重试一次", exc_info=True)
                return self._execute_insert(sql, params, retry=False)

    _RECORD_COLS = ("id, owner_key, city, travel_days, start_date, end_date,"
                    " transportation, accommodation, prefs, free_text, styles,"
                    " source, request_json, outlines_json, created_at")

    @staticmethod
    def _row_to_dict(r) -> Dict[str, Any]:
        keys = ("id", "owner_key", "city", "travel_days", "start_date", "end_date",
                "transportation", "accommodation", "prefs", "free_text", "styles",
                "source", "request_json", "outlines_json", "created_at")
        return dict(zip(keys, r))

    def save_record(self, owner_key: str, request: Dict[str, Any],
                    outlines: Dict[str, Any]) -> int:
        return self._execute_insert(
            f"INSERT INTO {RECORDS_TABLE} (owner_key, city, travel_days, start_date,"
            " end_date, transportation, accommodation, prefs, free_text, styles,"
            " source, request_json, outlines_json)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (owner_key, request.get("city", ""), request.get("travel_days", 0),
             request.get("start_date", ""), request.get("end_date", ""),
             request.get("transportation", ""), request.get("accommodation", ""),
             "、".join(request.get("preferences") or [])[:255],
             (request.get("free_text_input") or "")[:500],
             "、".join(p.get("style", "") for p in outlines.get("plans", []))[:255],
             (outlines.get("source") or "llm")[:16],
             json.dumps(request, ensure_ascii=False),
             json.dumps(outlines, ensure_ascii=False)))

    def list_records(self, owner_key: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._execute(
            f"SELECT {self._RECORD_COLS} FROM {RECORDS_TABLE}"
            " WHERE owner_key = %s ORDER BY id DESC LIMIT %s", (owner_key, limit))
        return [_summary_of(self._row_to_dict(r)) for r in rows]

    def get_record(self, owner_key: str, record_id: int) -> Optional[Dict[str, Any]]:
        rows = self._execute(
            f"SELECT {self._RECORD_COLS} FROM {RECORDS_TABLE}"
            " WHERE id = %s AND owner_key = %s", (record_id, owner_key))
        if not rows:
            return None
        row = self._row_to_dict(rows[0])
        result = _summary_of(row)
        result["request"] = json.loads(row["request_json"])
        result["outlines"] = json.loads(row["outlines_json"])
        return result

    def delete_record(self, owner_key: str, record_id: int) -> bool:
        affected = self._execute_modify(
            f"DELETE FROM {RECORDS_TABLE} WHERE id = %s AND owner_key = %s",
            (record_id, owner_key))
        if affected:
            self._execute_modify(f"DELETE FROM {DETAILS_TABLE} WHERE record_id = %s",
                                 (record_id,))
        return bool(affected)

    def save_detail(self, record_id: int, plan_id: int, plan_style: str,
                    payload: Dict[str, Any]) -> None:
        self._execute(
            f"INSERT INTO {DETAILS_TABLE} (record_id, plan_id, plan_style, payload)"
            " VALUES (%s,%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE plan_style = VALUES(plan_style),"
            " payload = VALUES(payload), created_at = CURRENT_TIMESTAMP",
            (record_id, plan_id, plan_style[:64],
             json.dumps(payload, ensure_ascii=False)))

    def get_detail(self, record_id: int, plan_id: int) -> Optional[Dict[str, Any]]:
        rows = self._execute(
            f"SELECT payload FROM {DETAILS_TABLE}"
            " WHERE record_id = %s AND plan_id = %s", (record_id, plan_id))
        return json.loads(rows[0][0]) if rows else None


# ---------------- 单例与降级 ----------------

_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_plan_store():
    """行程存储单例：MySQL 优先，连不上降级内存（与监控同库）"""
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
            _store = MySQLPlanStore(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("🗂️  MySQL 行程存储不可用（%s），降级内存存储（重启丢数据）", exc)
            _store = MemoryPlanStore()
        return _store


def reset_plan_store() -> None:
    """重置单例（测试用）"""
    global _store
    with _store_lock:
        _store = None
