"""配置管理模块

优化点：
- 仅从本项目 backend/.env 读取配置，不再依赖外部 HelloAgents 目录
- 高德 Key 改为可选（未配置时走纯 LLM 规划模式）
- 统一收口 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID
"""

import os
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# backend/.env 的绝对路径，保证从任意工作目录启动都能加载
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_ENV_PATH)


class Settings(BaseSettings):
    """应用配置"""

    # 应用基本配置
    app_name: str = "HelloAgents智能旅行助手"
    app_version: str = "2.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS 配置（逗号分隔）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 高德地图 Web服务 Key（可选；不填则不调用实时 POI/天气）
    amap_api_key: str = ""

    # Unsplash（可选）
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # LLM 配置（OpenAI 兼容接口；默认即火山方舟 Coding Plan）
    llm_api_key: str = ""
    llm_base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v1"
    llm_model_id: str = "ark-code-latest"
    llm_timeout: int = 600
    # 同时进行的 LLM 调用上限：4 套详情预生成正好打满；
    # 多用户并发时多余请求排队，避免把推理服务打到限流
    llm_max_concurrency: int = 4

    # 备用 LLM 供应商（主供应商熔断/失败时自动切换；留空 = 不启用）
    llm_backup_api_key: str = ""
    llm_backup_base_url: str = ""
    llm_backup_model_id: str = ""  # 留空 = 与主同模型
    # 断路器：主供应商连续失败 N 次熔断 M 秒，冷却后半开探测
    breaker_fail_threshold: int = 3
    breaker_cooldown_seconds: int = 300

    # 成本熔断：每日真实（非 mock）LLM token 预算，0 = 不限；超额后新 LLM 调用返回 429
    daily_token_budget: int = 0

    # 规划参数
    max_poi_per_keyword: int = 8
    request_timeout: int = 15

    # 规划缓存（与监控同库；连不上自动降级内存）
    cache_ttl_days: int = 7  # 运行时缓存有效期；预制路线（seed）永不过期

    # 预算缓存（预算不入库，只走 Redis 短期 TTL；默认 db=1 与本机其他项目隔离）
    redis_url: str = "redis://127.0.0.1:6379/1"
    budget_cache_ttl_hours: int = 24  # 近 1 天内生成过的预算直接复用，零 LLM 调用

    # 监控存储（MySQL；连不上自动降级内存）
    metrics_mysql_host: str = "127.0.0.1"
    metrics_mysql_port: int = 3306
    metrics_mysql_user: str = "root"
    metrics_mysql_password: str = ""
    metrics_mysql_database: str = "trip_planner"

    log_level: str = "INFO"

    class Config:
        env_file = _ENV_PATH
        case_sensitive = False
        extra = "ignore"

    def get_cors_origins_list(self) -> List[str]:
        """获取 CORS origins 列表"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def amap_enabled(self) -> bool:
        """是否启用高德实时数据"""
        return bool(self.amap_api_key)


# 全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


def validate_config() -> None:
    """校验必要配置；打印告警但不再因缺少高德 Key 而阻断启动"""
    if not settings.llm_api_key:
        raise ValueError(
            "LLM_API_KEY 未配置，请在 backend/.env 中填写火山方舟 API Key"
        )

    if not settings.amap_api_key:
        print("⚠️  AMAP_API_KEY 未配置：将使用纯 LLM 知识规划模式（POI/天气非实时）")


def print_config() -> None:
    """打印当前配置（隐藏敏感信息）"""
    print(f"应用名称: {settings.app_name} v{settings.app_version}")
    print(f"服务器:   {settings.host}:{settings.port}")
    print(f"LLM:      {settings.llm_model_id} @ {settings.llm_base_url}")
    print(f"LLM Key:  {'已配置' if settings.llm_api_key else '未配置'}")
    print(f"高德地图:  {'已配置（实时 POI/天气）' if settings.amap_enabled else '未配置（纯 LLM 模式）'}")
    print(f"Unsplash: {'已配置' if settings.unsplash_access_key else '未配置（无配图）'}")
    print(f"日志级别: {settings.log_level}")
