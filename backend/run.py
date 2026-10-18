"""启动脚本"""

import uvicorn
from app.config import get_settings
from app.logging_setup import setup_logging

if __name__ == "__main__":
    settings = get_settings()
    setup_logging(settings.log_level)  # 父进程（reloader）也落日志；子进程在 app.api.main import 时配置

    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        # reload=True 在 Windows 上换 worker 会卡死（旧进程不死新进程不起，两次实测），
        # 本地使用稳定性优先，改代码后手动重启（start.bat）
        reload=False,
        log_level=settings.log_level.lower()
    )

