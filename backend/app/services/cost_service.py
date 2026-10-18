# -*- coding: utf-8 -*-
"""成本熔断：每日真实（非 mock）LLM token 预算闸门。

- 预算来自 config.daily_token_budget（DAILY_TOKEN_BUDGET，0 = 不限）
- 用量依据 metrics_service.tokens_today()（两种存储都已实现）
- 只在真正要发起 LLM 调用前检查：缓存/预制路线命中零 token，不拦截
- mock 压测请求不消耗 token，也不拦截
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from ..config import get_settings
from .llm_service import breaker_status, get_backup_llm
from .metrics_service import get_metrics_store

logger = logging.getLogger("trip.cost")


class BudgetExceeded(Exception):
    """当日 token 预算已用尽"""

    def __init__(self, used: int, budget: int):
        self.used = used
        self.budget = budget
        super().__init__(f"当日 LLM token 预算已用尽（{used}/{budget}），请明日再来或调高 DAILY_TOKEN_BUDGET")


def ensure_budget(mock: bool = False) -> None:
    """预算闸门：超限抛 BudgetExceeded（路由层转 429）。mock 请求直接放行。"""
    if mock:
        return
    budget = get_settings().daily_token_budget
    if budget <= 0:
        return
    used = get_metrics_store().tokens_today()
    if used >= budget:
        logger.warning("💰 成本熔断触发：今日已用 %d tokens ≥ 预算 %d", used, budget)
        raise BudgetExceeded(used, budget)


def cost_status() -> Dict[str, Any]:
    """成本与 LLM 供应商状态（监控端点/压测台预算卡用）"""
    settings = get_settings()
    budget = settings.daily_token_budget
    used = get_metrics_store().tokens_today()
    return {
        "daily_token_budget": budget,
        "tokens_used_today": used,
        "tokens_remaining": max(0, budget - used) if budget > 0 else None,
        "budget_limited": budget > 0 and used >= budget,
        "llm": {
            "model": settings.llm_model_id,
            "base_url": settings.llm_base_url,
            "backup_configured": get_backup_llm() is not None,
            "breaker": breaker_status(),
        },
    }
