# -*- coding: utf-8 -*-
"""异步任务：规划请求任务化（jobs 表 + 进程内 asyncio worker）。

企业级动机：
- 同步 HTTP 长连接扛 145-300s 的推理调用，网关/代理随时可能砍连接；
  任务化后客户端只经历「提交(快) → 轮询(短连接)」
- idempotency_key 幂等：双击/断网重试/刷新重提不产生第二次 LLM 调用
- 启动恢复：进程重启时 running→queued 重新排队，任务不丢

存储：MySQL 优先、内存降级（与监控/行程同库同模式）。
执行：worker 与同步路由共用 services.trip_execution 执行器。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .tracing import new_trace_id, set_trace_id

logger = logging.getLogger("trip.jobs")

TABLE = "jobs"
WORKER_CONCURRENCY = 2   # worker 同时执行的任务数（LLM 并发另有 agent 闸门管）
WORKER_POLL_SECONDS = 1.0

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id VARCHAR(36) PRIMARY KEY,
  kind VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued',
  idempotency_key VARCHAR(80) NOT NULL DEFAULT '',
  owner_key VARCHAR(64) NOT NULL DEFAULT '',
  payload MEDIUMTEXT NOT NULL,
  result MEDIUMTEXT DEFAULT NULL,
  error TEXT DEFAULT NULL,
  trace_id VARCHAR(32) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at DATETIME DEFAULT NULL,
  finished_at DATETIME DEFAULT NULL,
  UNIQUE KEY uk_owner_idem (owner_key, idempotency_key),
  INDEX idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_COLS = ("id", "kind", "status", "idempotency_key", "owner_key", "payload",
         "result", "error", "trace_id", "created_at", "started_at", "finished_at")


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    """对外的任务视图（payload 不回传，结果/错误按需解析）"""
    return {
        "job_id": row["id"], "kind": row["kind"], "status": row["status"],
        "result": json.loads(row["result"]) if row.get("result") else None,
        "error": row.get("error"), "trace_id": row.get("trace_id", ""),
        "created_at": str(row["created_at"]),
        "finished_at": str(row["finished_at"]) if row.get("finished_at") else None,
    }


class MemoryJobStore:
    """内存任务存储：单测与 MySQL 降级用"""

    backend = "memory"

    def __init__(self):
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []  # 插入序（队列入队顺序）
        self._lock = threading.Lock()

    def create(self, kind: str, owner_key: str, payload: Dict[str, Any],
               idempotency_key: str) -> Tuple[str, bool]:
        with self._lock:
            for jid in self._order:
                r = self._rows[jid]
                if (r["owner_key"] == owner_key
                        and r["idempotency_key"] == idempotency_key):
                    return jid, False  # 幂等命中：返回已存在的任务
            jid = uuid.uuid4().hex
            self._rows[jid] = {
                "id": jid, "kind": kind, "status": "queued",
                "idempotency_key": idempotency_key, "owner_key": owner_key,
                "payload": json.dumps(payload, ensure_ascii=False),
                "result": None, "error": None, "trace_id": "",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "started_at": None, "finished_at": None,
            }
            self._order.append(jid)
            return jid, True

    def get(self, job_id: str, owner_key: str = "") -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._rows.get(job_id)
            if row is None or (owner_key and row["owner_key"] != owner_key):
                return None
            return dict(row)

    def next_queued(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            for jid in self._order:
                if self._rows[jid]["status"] == "queued":
                    return dict(self._rows[jid])
            return None

    def mark(self, job_id: str, status: str, result: Optional[str] = None,
             error: Optional[str] = None, trace_id: str = "") -> None:
        with self._lock:
            row = self._rows.get(job_id)
            if row is None:
                return
            row["status"] = status
            if status == "running":
                row["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                row["trace_id"] = trace_id
            if status in ("succeeded", "failed"):
                row["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                row["result"] = result
                row["error"] = error

    def requeue_interrupted(self) -> int:
        with self._lock:
            n = 0
            for row in self._rows.values():
                if row["status"] == "running":
                    row["status"] = "queued"
                    n += 1
            return n


class MySQLJobStore:
    """MySQL 任务存储：自动建表，断连重试一次"""

    backend = "mysql"

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self._lock = threading.Lock()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        logger.info("🧵 任务表就绪：MySQL %s.%s", self._cfg.get("host"), cfg["database"])

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

    def _execute(self, sql: str, params: Any = None, retry: bool = True):
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
            except Exception:  # noqa: BLE001 - 断连重试一次
                if not retry:
                    raise
                logger.warning("🧵 MySQL 操作失败，重连重试一次", exc_info=True)
                return self._execute(sql, params, retry=False)

    def create(self, kind: str, owner_key: str, payload: Dict[str, Any],
               idempotency_key: str) -> Tuple[str, bool]:
        jid = uuid.uuid4().hex
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO {TABLE} (id, kind, status, idempotency_key,"
                        " owner_key, payload) VALUES (%s,%s,'queued',%s,%s,%s)",
                        (jid, kind, idempotency_key, owner_key,
                         json.dumps(payload, ensure_ascii=False)))
                    return jid, True
            except Exception as exc:  # noqa: BLE001
                if "1062" not in str(exc):  # 非唯一键冲突才值得重连重试
                    raise
        # 唯一键冲突 = 同 owner 同幂等键的任务已存在，取出返回（不重试语义）
        rows = self._execute(
            f"SELECT id FROM {TABLE} WHERE owner_key = %s AND idempotency_key = %s",
            (owner_key, idempotency_key))
        return (rows[0][0], False) if rows else (jid, False)

    def get(self, job_id: str, owner_key: str = "") -> Optional[Dict[str, Any]]:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE} WHERE id = %s"
        params: List[Any] = [job_id]
        if owner_key:
            sql += " AND owner_key = %s"
            params.append(owner_key)
        rows = self._execute(sql, tuple(params))
        return dict(zip(_COLS, rows[0])) if rows else None

    def next_queued(self) -> Optional[Dict[str, Any]]:
        rows = self._execute(
            f"SELECT {', '.join(_COLS)} FROM {TABLE}"
            " WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1")
        return dict(zip(_COLS, rows[0])) if rows else None

    def mark(self, job_id: str, status: str, result: Optional[str] = None,
             error: Optional[str] = None, trace_id: str = "") -> None:
        if status == "running":
            self._execute(
                f"UPDATE {TABLE} SET status='running', started_at=NOW(3),"
                " trace_id=%s WHERE id=%s", (trace_id, job_id))
        else:
            self._execute(
                f"UPDATE {TABLE} SET status=%s, finished_at=NOW(3), result=%s,"
                " error=%s WHERE id=%s", (status, result, error, job_id))

    def requeue_interrupted(self) -> int:
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE {TABLE} SET status='queued' WHERE status='running'")
                    return cur.rowcount
            except Exception:  # noqa: BLE001
                logger.warning("🧵 中断任务恢复失败", exc_info=True)
                return 0


# ---------------- 单例与降级 ----------------

_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_job_store():
    """任务存储单例：MySQL 优先，连不上降级内存"""
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
            _store = MySQLJobStore(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("🧵 MySQL 任务存储不可用（%s），降级内存存储（重启丢任务）", exc)
            _store = MemoryJobStore()
        return _store


def reset_job_store() -> None:
    """重置单例（测试用）"""
    global _store
    with _store_lock:
        _store = None


# ---------------- 任务 API ----------------

VALID_KINDS = ("outline", "detail")


def create_job(kind: str, owner_key: str, payload: Dict[str, Any],
               idempotency_key: str) -> Tuple[str, bool]:
    """建任务（幂等）：同 owner 同幂等键直接返回已有任务 ID"""
    if kind not in VALID_KINDS:
        raise ValueError(f"未知任务类型: {kind}")
    return get_job_store().create(kind, owner_key, payload, idempotency_key)


def get_job(job_id: str, owner_key: str = "") -> Optional[Dict[str, Any]]:
    row = get_job_store().get(job_id, owner_key)
    return _public(row) if row else None


# ---------------- worker ----------------

_worker_task: Optional[asyncio.Task] = None


async def _run_one(job: Dict[str, Any]) -> None:
    """执行单个任务：trace 贯穿日志/监控行，结果落库"""
    from ..models.schemas import TripDetailRequest, TripRequest
    from .trip_execution import execute_detail, execute_outline

    store = get_job_store()
    trace = new_trace_id()
    set_trace_id(trace)  # 任务在自己的 asyncio.Task 上下文里跑，无需 reset
    try:
        store.mark(job["id"], "running", trace_id=trace)
        logger.info("🧵 任务开始：%s kind=%s（%s）", job["id"][:8], job["kind"],
                    job["idempotency_key"][:24])
        payload = json.loads(job["payload"])
        if job["kind"] == "outline":
            data = await execute_outline(TripRequest(**payload), job["owner_key"])
        else:
            data = await execute_detail(TripDetailRequest(**payload), job["owner_key"])
        store.mark(job["id"], "succeeded",
                   result=data.model_dump_json())
        logger.info("🧵 任务完成：%s kind=%s", job["id"][:8], job["kind"])
    except Exception as exc:  # noqa: BLE001 - 任务失败落库，不拖垮 worker
        logger.error("🧵 任务失败：%s kind=%s: %s", job["id"][:8], job["kind"], exc)
        try:
            store.mark(job["id"], "failed", error=str(exc)[:2000])
        except Exception:  # noqa: BLE001
            logger.warning("🧵 失败状态落库失败", exc_info=True)


async def _worker_loop() -> None:
    """轮询 queued 任务并发执行（并发上限 WORKER_CONCURRENCY）"""
    sem = asyncio.Semaphore(WORKER_CONCURRENCY)
    running: set[asyncio.Task] = set()
    logger.info("🧵 任务 worker 启动（并发 %d，轮询 %.1fs）",
                WORKER_CONCURRENCY, WORKER_POLL_SECONDS)
    while True:
        try:
            while len(running) < WORKER_CONCURRENCY:
                job = await asyncio.get_running_loop().run_in_executor(
                    None, get_job_store().next_queued)
                if job is None:
                    break
                await sem.acquire()
                task = asyncio.create_task(_run_one(job))
                running.add(task)
                task.add_done_callback(lambda t: (running.discard(t), sem.release()))
            await asyncio.sleep(WORKER_POLL_SECONDS)
        except asyncio.CancelledError:
            for t in running:
                t.cancel()
            raise
        except Exception:  # noqa: BLE001 - worker 永不退出
            logger.warning("🧵 worker 轮询异常（继续运行）", exc_info=True)
            await asyncio.sleep(WORKER_POLL_SECONDS)


def recover_interrupted() -> None:
    """启动恢复：上次进程退出时 running 的任务重新排队（任务不丢）"""
    try:
        n = get_job_store().requeue_interrupted()
        if n:
            logger.warning("🧵 启动恢复：%d 个中断任务重新排队", n)
    except Exception:  # noqa: BLE001
        logger.warning("🧵 启动恢复失败（不影响服务）", exc_info=True)


def start_worker() -> None:
    """在事件循环内启动 worker（lifespan 调用，幂等）"""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    """关停 worker（lifespan 关闭时调用）"""
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
        logger.info("🧵 任务 worker 已停止")
