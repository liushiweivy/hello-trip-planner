"""FastAPI 主应用"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from ..config import print_config, settings, validate_config
from ..logging_setup import setup_logging
from ..services import job_service
from ..services.tracing import TraceIdMiddleware
from .routes import cache as cache_routes
from .routes import cost as cost_routes
from .routes import jobs as jobs_routes
from .routes import logs as logs_routes
from .routes import map as map_routes
from .routes import metrics as metrics_routes
from .routes import plans as plans_routes
from .routes import poi, trip

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static")

# 子进程（--reload worker）import 本模块时即完成日志配置（幂等）
setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时打印并校验配置"""
    print("\n" + "=" * 60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("=" * 60)
    print_config()
    validate_config()  # 仅缺 LLM Key 时抛错；缺高德 Key 只告警
    print("=" * 60)
    print("📚 API 文档: http://localhost:8000/docs")
    print("📜 后端日志: http://localhost:8000/api/logs （5 秒自刷）")
    print("🧪 压测监控: http://localhost:8000/loadtest")
    print("⚡ 缓存状态: http://localhost:8000/api/cache/stats")
    print("🧵 异步任务: POST /api/jobs 提交，GET /api/jobs/{id} 轮询")
    print("=" * 60 + "\n")
    # 异步任务：先恢复上次中断的任务（running→queued），再启动 worker
    job_service.recover_interrupted()
    job_service.start_worker()
    yield
    await job_service.stop_worker()
    print("\n👋 应用关闭\n")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="HelloAgents 智能旅行规划助手 API（高德 REST + 火山方舟 ark-code-latest）",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# trace_id：每请求一个 ID，贯穿日志行 / 监控行 / X-Request-ID 响应头
app.add_middleware(TraceIdMiddleware)

app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")
app.include_router(logs_routes.router, prefix="/api")
app.include_router(metrics_routes.router, prefix="/api")
app.include_router(cache_routes.router, prefix="/api")
app.include_router(cost_routes.router, prefix="/api")
app.include_router(plans_routes.router, prefix="/api")
app.include_router(jobs_routes.router, prefix="/api")


@app.get("/loadtest", include_in_schema=False)
async def loadtest_page():
    """压测与监控台（单文件静态页）"""
    return FileResponse(os.path.join(_STATIC_DIR, "loadtest.html"))


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "amap_enabled": settings.amap_enabled,
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": settings.app_name, "version": settings.app_version}
