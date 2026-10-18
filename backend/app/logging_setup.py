"""日志体系：控制台 + 滚动文件 + 内存尾部缓冲（/api/logs 用）

- 文件：backend/logs/backend.log（5MB × 3 滚动，UTF-8），服务重启不丢
- 缓冲：内存保留最近 500 行，GET /api/logs 直接吐给浏览器
- uvicorn 自己的 access/error 日志也并入同一文件，一份日志看全链路
- 幂等：uvicorn --reload 父子进程都会触发本模块，重复调用不叠加 handler
"""

import logging
import os
from collections import deque
from logging.handlers import RotatingFileHandler

from .services.tracing import TraceIdLogFilter

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "backend.log")

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(trace_id)s | %(name)s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"

TAIL_CAPACITY = 500
_tail_lines: deque[str] = deque(maxlen=TAIL_CAPACITY)


class TailBufferHandler(logging.Handler):
    """把格式化后的日志行存进内存环形缓冲，供 /api/logs 读取"""

    def __init__(self):
        super().__init__(logging.INFO)
        self.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _tail_lines.append(self.format(record))
        except Exception:  # 日志 handler 绝不拖垮业务
            pass


def tail_lines(n: int = 200) -> list[str]:
    """最近 n 行日志（新的在最后）"""
    if n <= 0:
        return []
    return list(_tail_lines)[-n:]


_configured = False


def setup_logging(level: str = "INFO") -> None:
    """配置全局日志（幂等）。root + uvicorn 三 logger 全部接到控制台/文件/缓冲。"""
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    tail = TailBufferHandler()

    handlers = [console, file_handler, tail]
    trace_filter = TraceIdLogFilter()
    for h in handlers:
        h.addFilter(trace_filter)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in handlers:
        root.addHandler(h)

    # uvicorn 默认只打控制台且 access logger 不向上传播，逐一把 handler 挂上；
    # 挂上后关掉 propagate，否则同一条会被 uvicorn 自己的 handler 和 root 各写一遍
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        for h in handlers:
            lg.addHandler(h)
        lg.propagate = False

    logging.getLogger(__name__).info("日志系统就绪，文件：%s", LOG_FILE)
