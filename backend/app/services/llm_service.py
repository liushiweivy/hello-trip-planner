"""LLM 服务模块

HelloAgentsLLM 自动读取 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL / LM_TIMEOUT
环境变量（OpenAI 兼容协议），本项目默认指向火山方舟 Coding Plan（与 Claude Code 同模型）。

token 用量监控：HelloAgentsLLM.invoke 丢弃了 response.usage，这里给实例打补丁——
invoke 在同一线程内同步调用，用量写线程本地存储，调用方（同一执行线程）随取随用。

企业级增强（2026-10-18）：
- 多供应商 failover：主供应商（LLM_*）连续失败或熔断中自动切备用（LLM_BACKUP_*），
  usage 里记 provider=primary/backup，监控 model 列以 @backup 后缀区分
- 断路器：主供应商连续 N 次失败（默认 3）熔断 300s，期间请求直发备用；
  冷却结束放行半开探测，成功即闭合、失败重新计时
"""

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from hello_agents import HelloAgentsLLM

from ..config import get_settings

logger = logging.getLogger("trip.llm")

_llm_instance: HelloAgentsLLM | None = None
_backup_llm_instance: Optional[HelloAgentsLLM] = None
_backup_checked = False

# invoke 与 planner.run 在同一执行线程内同步完成，线程本地即可安全传递 usage
_usage_local = threading.local()


def get_thread_last_usage() -> Optional[Dict[str, Any]]:
    """取本线程最近一次 LLM 调用的用量（含 finish_reason/provider，无则 None）"""
    return getattr(_usage_local, "last", None)


class CircuitBreaker:
    """主供应商断路器：连续失败达阈值熔断，冷却后半开探测"""

    def __init__(self, threshold: int = 3, cooldown_seconds: int = 300):
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._failures = 0
        self._opened_at = 0.0  # 0 = 闭合
        self._lock = threading.Lock()

    def can_try_primary(self) -> bool:
        with self._lock:
            if not self._opened_at:
                return True
            # 熔断中：冷却期过后放行一次半开探测
            return time.monotonic() - self._opened_at >= self._cooldown

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = 0.0

    def record_failure(self) -> None:
        with self._lock:
            if self._opened_at:
                # 半开探测失败：重新计时熔断
                self._opened_at = time.monotonic()
                logger.warning("🔌 主 LLM 半开探测失败，重新熔断 %ds", self._cooldown)
                return
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = time.monotonic()
                logger.error("🔌 主 LLM 连续失败 %d 次，熔断 %ds（请求直发备用）",
                             self._failures, self._cooldown)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if not self._opened_at:
                return {"state": "closed", "consecutive_failures": self._failures,
                        "open_remaining_s": 0}
            remaining = max(0, int(self._cooldown - (time.monotonic() - self._opened_at)))
            return {"state": "open" if remaining else "half_open",
                    "consecutive_failures": self._failures,
                    "open_remaining_s": remaining}


_breaker: Optional[CircuitBreaker] = None


def _get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        settings = get_settings()
        _breaker = CircuitBreaker(settings.breaker_fail_threshold,
                                  settings.breaker_cooldown_seconds)
    return _breaker


def breaker_status() -> Dict[str, Any]:
    """断路器状态（供监控端点展示）"""
    return _get_breaker().status()


def _extract_usage(usage: Any) -> Optional[Dict[str, Any]]:
    """从 OpenAI 兼容 usage 对象/字典提取 token 字段（含推理 tokens）"""
    if usage is None:
        return None
    getter = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
    reasoning = None
    details = getter("completion_tokens_details")
    if details is not None:
        dget = details.get if isinstance(details, dict) else lambda k, d=None: getattr(details, k, d)
        reasoning = dget("reasoning_tokens")
    return {
        "prompt_tokens": getter("prompt_tokens"),
        "completion_tokens": getter("completion_tokens"),
        "total_tokens": getter("total_tokens"),
        "reasoning_tokens": reasoning,
    }


def _raw_invoke(llm: HelloAgentsLLM, messages, **kwargs):
    """底层 OpenAI 调用，返回 (content, usage_dict)"""
    client = llm._client  # HelloAgentsLLM 未暴露 usage，只能到底层 OpenAI client 取
    response = client.chat.completions.create(
        model=llm.model,
        messages=messages,
        temperature=kwargs.get("temperature", llm.temperature),
        max_tokens=kwargs.get("max_tokens", llm.max_tokens),
        **{k: v for k, v in kwargs.items() if k not in ["temperature", "max_tokens"]},
    )
    usage_dict = _extract_usage(response.usage) or {}
    # finish_reason="length" 即输出被 max_tokens 截断：调用方据此跳过
    # 注定再截断的同预算重试，直接降信息量/分块
    usage_dict["finish_reason"] = getattr(response.choices[0], "finish_reason", None)
    return response.choices[0].message.content, usage_dict


def _patch_invoke_with_usage(llm: HelloAgentsLLM) -> None:
    """给 invoke 打补丁：usage 监控 + 主备 failover + 断路器。
    补丁失败只丢监控，不影响调用本身。"""
    backup = get_backup_llm()
    breaker = _get_breaker()

    def invoke_with_usage(messages, **kwargs):
        if breaker.can_try_primary():
            try:
                content, usage_dict = _raw_invoke(llm, messages, **kwargs)
                breaker.record_success()
                usage_dict["provider"] = "primary"
                _usage_local.last = usage_dict
                return content
            except Exception as exc:  # noqa: BLE001 - 任何主侧失败都计入断路器
                breaker.record_failure()
                if backup is None:
                    raise
                logger.warning("🔀 主 LLM 调用失败（%s），切换到备用供应商", exc)
        else:
            if backup is None:
                raise RuntimeError("主 LLM 熔断中且未配置备用供应商（LLM_BACKUP_*）")
            logger.info("🔀 主 LLM 熔断中，请求直发备用供应商")
        content, usage_dict = _raw_invoke(backup, messages, **kwargs)
        usage_dict["provider"] = "backup"
        _usage_local.last = usage_dict
        return content

    llm.invoke = invoke_with_usage


def _new_llm(model: str, api_key: str, base_url: str, timeout: int) -> HelloAgentsLLM:
    return HelloAgentsLLM(
        model=model,
        api_key=api_key,
        base_url=base_url,
        provider="custom",
        temperature=0.3,  # 旅行计划需要稳定的结构化 JSON 输出
        # 推理模型的思维链也计入 completion tokens：实测大纲任务思维链能吃掉
        # 13K+，16K 预算会把 JSON 正文截断（触发修复重试，延迟翻倍），给到 24K
        max_tokens=24576,
        timeout=timeout,
    )


def get_llm() -> HelloAgentsLLM:
    """获取 LLM 实例（单例）"""
    global _llm_instance

    if _llm_instance is None:
        settings = get_settings()
        _llm_instance = _new_llm(
            settings.llm_model_id,
            settings.llm_api_key or os.getenv("LLM_API_KEY"),
            settings.llm_base_url,
            settings.llm_timeout,
        )
        try:
            _patch_invoke_with_usage(_llm_instance)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  invoke usage 补丁失败（仅影响 token 监控）: {exc}")
        backup_note = "，备用已配置" if get_backup_llm() is not None else "，无备用"
        print(
            f"✅ LLM 服务初始化成功: provider={_llm_instance.provider} "
            f"model={_llm_instance.model} base_url={_llm_instance.base_url}{backup_note}"
        )

    return _llm_instance


def get_backup_llm() -> Optional[HelloAgentsLLM]:
    """备用 LLM 实例（单例；未配置 LLM_BACKUP_* 时返回 None）"""
    global _backup_llm_instance, _backup_checked
    if _backup_checked:
        return _backup_llm_instance
    _backup_checked = True
    settings = get_settings()
    if not (settings.llm_backup_api_key and settings.llm_backup_base_url):
        return None
    _backup_llm_instance = _new_llm(
        settings.llm_backup_model_id or settings.llm_model_id,
        settings.llm_backup_api_key,
        settings.llm_backup_base_url,
        settings.llm_timeout,
    )
    logger.info("✅ 备用 LLM 已配置：%s @ %s",
                _backup_llm_instance.model, _backup_llm_instance.base_url)
    return _backup_llm_instance


def reset_llm() -> None:
    """重置实例与断路器（测试用）"""
    global _llm_instance, _backup_llm_instance, _backup_checked, _breaker
    _llm_instance = None
    _backup_llm_instance = None
    _backup_checked = False
    _breaker = None
