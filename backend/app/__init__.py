"""HelloAgents智能旅行助手 - 后端应用"""

import sys

# Windows 控制台默认 GBK，项目日志含 emoji，统一 UTF-8 输出避免 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

__version__ = "2.0.0"
