# -*- coding: utf-8 -*-
"""LLM 调用监控：耗时 + token 消耗落 MySQL，供压测页实时查看。

- 每次 LLM 调用（大纲/详情/修复重试，含 mock）写一行 request_metrics
- MySQL 连不上自动降级内存存储（服务不受影响，日志告警）
- 测试用 MemoryMetricsStore，不依赖任何外部服务
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .tracing import get_trace_id

logger = logging.getLogger("trip.metrics")

TABLE = "request_metrics"

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  request_id VARCHAR(36) NOT NULL,
  ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  endpoint VARCHAR(32) NOT NULL DEFAULT '',
  phase VARCHAR(24) NOT NULL,
  mock TINYINT NOT NULL DEFAULT 0,
  city VARCHAR(64) DEFAULT '',
  travel_days INT DEFAULT 0,
  plan_id INT DEFAULT NULL,
  plan_style VARCHAR(64) DEFAULT '',
  status VARCHAR(16) NOT NULL,
  latency_ms INT NOT NULL,
  prompt_chars INT DEFAULT 0,
  output_chars INT DEFAULT 0,
  prompt_tokens INT DEFAULT NULL,
  completion_tokens INT DEFAULT NULL,
  reasoning_tokens INT DEFAULT NULL,
  total_tokens INT DEFAULT NULL,
  model VARCHAR(64) DEFAULT '',
  client_tag VARCHAR(64) DEFAULT '',
  error TEXT DEFAULT NULL,
  finish_reason VARCHAR(16) NOT NULL DEFAULT '',
  max_tokens INT DEFAULT NULL,
  attempt INT NOT NULL DEFAULT 1,
  queue_ms INT NOT NULL DEFAULT 0,
  prefs VARCHAR(255) NOT NULL DEFAULT '',
  prompt_excerpt VARCHAR(512) NOT NULL DEFAULT '',
  INDEX idx_ts (ts),
  INDEX idx_client_tag (client_tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 2026-10-18 调用历史扩容：老表启动时按 information_schema 缺啥补啥
# （MySQL 8 不支持 ADD COLUMN IF NOT EXISTS）
_MIGRATION_COLUMNS = (
    ("finish_reason", "VARCHAR(16) NOT NULL DEFAULT ''"),
    ("max_tokens", "INT DEFAULT NULL"),
    ("attempt", "INT NOT NULL DEFAULT 1"),
    ("queue_ms", "INT NOT NULL DEFAULT 0"),
    ("prefs", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("prompt_excerpt", "VARCHAR(512) NOT NULL DEFAULT ''"),
    ("trace_id", "VARCHAR(32) NOT NULL DEFAULT ''"),
)

_INSERT_SQL = (
    f"INSERT INTO {TABLE} (request_id, endpoint, phase, mock, city, travel_days,"
    " plan_id, plan_style, status, latency_ms, prompt_chars, output_chars,"
    " prompt_tokens, completion_tokens, reasoning_tokens, total_tokens,"
    " model, client_tag, error, finish_reason, max_tokens, attempt,"
    " queue_ms, prefs, prompt_excerpt, trace_id)"
    " VALUES (%(request_id)s, %(endpoint)s, %(phase)s, %(mock)s, %(city)s,"
    " %(travel_days)s, %(plan_id)s, %(plan_style)s, %(status)s, %(latency_ms)s,"
    " %(prompt_chars)s, %(output_chars)s, %(prompt_tokens)s,"
    " %(completion_tokens)s, %(reasoning_tokens)s, %(total_tokens)s,"
    " %(model)s, %(client_tag)s, %(error)s, %(finish_reason)s, %(max_tokens)s,"
    " %(attempt)s, %(queue_ms)s, %(prefs)s, %(prompt_excerpt)s, %(trace_id)s)"
)

# 查询列（与表一致，memory 存储直接复用 dataclass 字段名）
_COLUMNS = [
    "id", "request_id", "ts", "endpoint", "phase", "mock", "city", "travel_days",
    "plan_id", "plan_style", "status", "latency_ms", "prompt_chars",
    "output_chars", "prompt_tokens", "completion_tokens", "reasoning_tokens",
    "total_tokens", "model", "client_tag", "error", "finish_reason",
    "max_tokens", "attempt", "queue_ms", "prefs", "prompt_excerpt", "trace_id",
]


def missing_migration_columns(existing: set) -> list:
    """老表缺的迁移列（纯函数，便于单测）"""
    return [(name, ddl) for name, ddl in _MIGRATION_COLUMNS if name not in existing]


@dataclass
class MetricsRecord:
    """一次 LLM 调用的完整监控记录"""
    endpoint: str                 # /api/trip/plan | /api/trip/detail
    phase: str                    # outline / detail / outline_repair / detail_repair
    status: str                   # success / retry_success / fallback / error
    latency_ms: int
    mock: bool = False
    city: str = ""
    travel_days: int = 0
    plan_id: Optional[int] = None
    plan_style: str = ""
    prompt_chars: int = 0
    output_chars: int = 0
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    model: str = ""
    client_tag: str = ""
    error: Optional[str] = None
    finish_reason: str = ""           # stop / length（截断分析）
    max_tokens: Optional[int] = None  # 本次调用的输出预算
    attempt: int = 1                  # 第几次尝试：1=首次 2=修复重试
    queue_ms: int = 0                 # LLM 并发闸门排队耗时（区分"排队等"与"模型慢"）
    prefs: str = ""                   # 用户偏好+自由文本（分析哪类请求慢/失败）
    prompt_excerpt: str = ""          # prompt 前 240 字（调试定位，截断入库）
    # 全链路 ID：默认取当前请求上下文（中间件/worker 设置），把监控行与日志行串起来
    trace_id: str = field(default_factory=lambda: get_trace_id())
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_row(self) -> Dict[str, Any]:
        d = asdict(self)
        d["mock"] = 1 if self.mock else 0
        return d


def summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把一批记录聚合成摘要（两种存储后端共用，便于单测）"""
    latencies = sorted(r["latency_ms"] for r in rows)

    def pct(p: float) -> int:
        if not latencies:
            return 0
        idx = min(len(latencies) - 1, max(0, round(p * (len(latencies) - 1))))
        return latencies[idx]

    def token_sum(key: str) -> int:
        return sum(r[key] or 0 for r in rows)

    ok = sum(1 for r in rows if r["status"] in ("success", "retry_success", "cache_hit"))
    # 分类聚合（2026-10-18）：by_phase 细化为次数/成败/四类 token/耗时/推理占比，
    # 另加 by_status 结局分布与 by_city 城市对比——压测多城市、看 LLM 调用分类用
    by_phase: Dict[str, Dict[str, Any]] = {}
    by_status: Dict[str, int] = {}
    by_city: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        success = r["status"] in ("success", "retry_success", "cache_hit")
        bucket = by_phase.setdefault(r["phase"], {
            "count": 0, "success": 0, "tokens": 0, "latency_sum": 0,
            "latency_max": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "truncated": 0,
        })
        bucket["count"] += 1
        bucket["success"] += 1 if success else 0
        bucket["tokens"] += r["total_tokens"] or 0
        bucket["prompt_tokens"] += r["prompt_tokens"] or 0
        bucket["completion_tokens"] += r["completion_tokens"] or 0
        bucket["reasoning_tokens"] += r["reasoning_tokens"] or 0
        bucket["latency_sum"] += r["latency_ms"]
        bucket["latency_max"] = max(bucket["latency_max"], r["latency_ms"])
        bucket["truncated"] += 1 if r["status"] == "truncated" else 0

        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

        city = r.get("city") or ""
        if city:
            cb = by_city.setdefault(city, {"count": 0, "success": 0,
                                           "tokens": 0, "latency_sum": 0})
            cb["count"] += 1
            cb["success"] += 1 if success else 0
            cb["tokens"] += r["total_tokens"] or 0
            cb["latency_sum"] += r["latency_ms"]

    # 派生字段：平均耗时/成功率/推理占比，前端直接拿来渲染
    for b in by_phase.values():
        b["latency_avg_ms"] = round(b["latency_sum"] / b["count"])
        b["success_rate"] = round(b["success"] / b["count"], 4)
        b["reasoning_share"] = (round(b["reasoning_tokens"] / b["completion_tokens"], 4)
                                if b["completion_tokens"] else 0.0)
        del b["latency_sum"]
    for b in by_city.values():
        b["latency_avg_ms"] = round(b["latency_sum"] / b["count"])
        b["success_rate"] = round(b["success"] / b["count"], 4)
        del b["latency_sum"]

    completion_total = token_sum("completion_tokens")
    reasoning_total = token_sum("reasoning_tokens")
    return {
        "count": len(rows),
        "success": ok,
        "success_rate": round(ok / len(rows), 4) if rows else 0.0,
        "latency_avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "latency_p50_ms": pct(0.50),
        "latency_p95_ms": pct(0.95),
        "latency_max_ms": latencies[-1] if latencies else 0,
        "prompt_tokens": token_sum("prompt_tokens"),
        "completion_tokens": completion_total,
        "reasoning_tokens": reasoning_total,
        "reasoning_share": round(reasoning_total / completion_total, 4)
        if completion_total else 0.0,
        "total_tokens": token_sum("total_tokens"),
        "by_phase": by_phase,
        "by_status": by_status,
        "by_city": by_city,
    }


class MemoryMetricsStore:
    """内存存储：单测与 MySQL 降级用"""

    def __init__(self):
        self._rows: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    backend = "memory"

    def record(self, rec: MetricsRecord) -> None:
        row = rec.to_row()
        row["id"] = len(self._rows) + 1
        row["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._rows.append(row)

    def recent(self, limit: int = 50, client_tag: str = "", mock: str = "all") -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(reversed(self._rows))
        rows = [r for r in rows if _match(r, client_tag, mock)]
        return rows[:limit]

    def summary(self, client_tag: str = "", mock: str = "all") -> Dict[str, Any]:
        with self._lock:
            rows = [r for r in self._rows if _match(r, client_tag, mock)]
        return summarize_rows(rows)

    def batches(self, limit: int = 20) -> List[Dict[str, Any]]:
        # 压测页把用户序号拼进 tag（batch|u3），批次聚合时剥掉用户后缀
        counts: Dict[str, int] = {}
        with self._lock:
            for r in self._rows:
                if r["client_tag"]:
                    key = r["client_tag"].split("|")[0]
                    counts[key] = counts.get(key, 0) + 1
        return [{"client_tag": k, "calls": v} for k, v in list(counts.items())[-limit:]]

    def tokens_today(self) -> int:
        """今日真实（非 mock）LLM 调用消耗的 total_tokens 合计——成本熔断依据"""
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            return sum(r["total_tokens"] or 0 for r in self._rows
                       if not r["mock"] and str(r["ts"]).startswith(today))


class MySQLMetricsStore:
    """MySQL 存储：自动建库建表，连接断开自动重连一次"""

    backend = "mysql"

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._ensure_schema()

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
            # 老表列迁移：information_schema 查现有列，缺啥补啥
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (db, TABLE))
            existing = {r[0] for r in cur.fetchall()}
            for name, ddl in missing_migration_columns(existing):
                cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}")
                logger.info("📊 监控表迁移：补列 %s", name)
        logger.info("📊 监控表就绪：MySQL %s.%s", self._cfg.get("host"), db)

    def _execute(self, sql: str, params: Any = None, retry: bool = True):
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
            except Exception:  # noqa: BLE001 - 断连重试一次
                if not retry:
                    raise
                logger.warning("📊 MySQL 操作失败，重连重试一次", exc_info=True)
                return self._execute(sql, params, retry=False)

    def record(self, rec: MetricsRecord) -> None:
        self._execute(_INSERT_SQL, rec.to_row())

    def recent(self, limit: int = 50, client_tag: str = "", mock: str = "all") -> List[Dict[str, Any]]:
        where, params = _where(client_tag, mock)
        rows = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {TABLE}{where}"
            " ORDER BY id DESC LIMIT %s", (*params, limit))
        return [dict(zip(_COLUMNS, r)) for r in rows]

    def summary(self, client_tag: str = "", mock: str = "all") -> Dict[str, Any]:
        where, params = _where(client_tag, mock)
        rows = self._execute(
            "SELECT phase, status, latency_ms, prompt_tokens, completion_tokens,"
            f" reasoning_tokens, total_tokens, city FROM {TABLE}{where}", params)
        dicts = [
            dict(zip(("phase", "status", "latency_ms", "prompt_tokens",
                      "completion_tokens", "reasoning_tokens", "total_tokens",
                      "city"), r))
            for r in rows
        ]
        return summarize_rows(dicts)

    def batches(self, limit: int = 20) -> List[Dict[str, Any]]:
        # SUBSTRING_INDEX 剥掉压测页拼的用户后缀（batch|u3 → batch）
        rows = self._execute(
            "SELECT SUBSTRING_INDEX(client_tag, '|', 1) AS batch, COUNT(*) AS calls,"
            f" MAX(ts) AS last_ts FROM {TABLE} WHERE client_tag != ''"
            " GROUP BY batch ORDER BY last_ts DESC LIMIT %s", (limit,))
        return [{"client_tag": t, "calls": c, "last_ts": str(ts)} for t, c, ts in rows]

    def tokens_today(self) -> int:
        """今日真实（非 mock）LLM 调用消耗的 total_tokens 合计——成本熔断依据"""
        (total,) = self._execute(
            f"SELECT COALESCE(SUM(total_tokens), 0) FROM {TABLE}"
            " WHERE mock = 0 AND ts >= CURDATE()")[0]
        return int(total)


def _match(row: Dict[str, Any], client_tag: str, mock: str) -> bool:
    # tag 过滤按批次前缀匹配：batch 命中 batch 本身和 batch|uN 的用户行
    if client_tag:
        row_tag = row["client_tag"]
        if row_tag != client_tag and not row_tag.startswith(client_tag + "|"):
            return False
    if mock == "mock" and not row["mock"]:
        return False
    if mock == "real" and row["mock"]:
        return False
    return True


def _where(client_tag: str, mock: str) -> tuple:
    clauses, params = [], []
    if client_tag:
        clauses.append("(client_tag = %s OR client_tag LIKE %s)")
        params.extend([client_tag, client_tag + "|%"])
    if mock == "mock":
        clauses.append("mock = 1")
    elif mock == "real":
        clauses.append("mock = 0")
    return (" WHERE " + " AND ".join(clauses) if clauses else "", tuple(params))


# ---------------- 单例与降级 ----------------

_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_metrics_store():
    """监控存储单例：MySQL 优先，连不上降级内存（只告警不阻断服务）"""
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
            _store = MySQLMetricsStore(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("📊 MySQL 监控不可用（%s），降级内存存储（重启丢数据）", exc)
            _store = MemoryMetricsStore()
        return _store


def record_metric(rec: MetricsRecord) -> None:
    """记录一条监控 + 打详细日志；任何失败都不允许影响主流程"""
    try:
        get_metrics_store().record(rec)
        logger.info(
            "📊 [METRICS] %s phase=%s status=%s 耗时=%.1fs%s tokens=%s+%s%s=%s"
            " chars=%d→%d mock=%d plan=%s「%s」tag=%s fr=%s #%d%s",
            rec.endpoint, rec.phase, rec.status, rec.latency_ms / 1000,
            f"(排队{rec.queue_ms / 1000:.1f}s)" if rec.queue_ms else "",
            rec.prompt_tokens if rec.prompt_tokens is not None else "?",
            rec.completion_tokens if rec.completion_tokens is not None else "?",
            f"(推理{rec.reasoning_tokens})" if rec.reasoning_tokens else "",
            rec.total_tokens if rec.total_tokens is not None else "?",
            rec.prompt_chars, rec.output_chars, int(rec.mock),
            rec.plan_id if rec.plan_id is not None else "-", rec.plan_style or "-",
            rec.client_tag or "-", rec.finish_reason or "-", rec.attempt,
            f" error={rec.error}" if rec.error else "",
        )
    except Exception:  # noqa: BLE001
        logger.warning("📊 监控写入失败（不影响主流程）", exc_info=True)


def reset_metrics_store() -> None:
    """重置单例（测试用）"""
    global _store
    with _store_lock:
        _store = None
