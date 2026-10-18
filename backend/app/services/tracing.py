# -*- coding: utf-8 -*-
"""全链路 trace_id：每个请求一个 ID，贯穿日志行 / request_metrics 行 / 响应头。

- 中间件从 X-Request-ID 请求头接力（没有则生成），响应头回写同一个
- ContextVar 传递：agent 的 _record 在协程内调用（不在执行线程），能拿到；
  任务 worker 处理每个 job 时也要显式 set
"""
from __future__ import annotations

import contextvars
import logging
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    return _trace_id.get()


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


class TraceIdLogFilter(logging.Filter):
    """给每条日志记录注入 trace_id（无上下文时为 -）"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id() or "-"
        return True


class TraceIdMiddleware:
    """纯 ASGI 中间件（不依赖 BaseHTTPMiddleware，流式友好）"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode(errors="ignore")[:32]
        tid = incoming or uuid.uuid4().hex[:12]
        token = _trace_id.set(tid)

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", tid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            _trace_id.reset(token)
