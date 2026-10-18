# -*- coding: utf-8 -*-
"""Prompt 资源管理：prompt 从代码常量迁移为 app/prompts/*.md 资源文件，带版本号。

- 文件首行 `<!-- prompt_version: xxx -->` 即版本号，LLM 调用日志里带出版本，
  便于「哪版 prompt 引入了回归」的追溯与 A/B
- 文件缺失/读取失败回落到内嵌副本（与 v1 相同），服务永远可启动
"""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, Tuple

logger = logging.getLogger("trip.prompts")

_PROMPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_VERSION_RE = re.compile(r"<!--\s*prompt_version:\s*(\S+?)\s*-->")


def _load_fallback(name: str) -> str:
    """内嵌兜底：文件丢失时回落到 agent 模块里的内联常量（服务永远可启动）。
    函数内延迟 import 避免循环依赖（agent 顶层 import 本模块）。"""
    from ..agents.trip_planner_agent import (
        OUTLINE_SYSTEM_PROMPT,
        PLANNER_SYSTEM_PROMPT,
    )
    return {"outline": OUTLINE_SYSTEM_PROMPT,
            "planner": PLANNER_SYSTEM_PROMPT}[name]


def _strip_header(raw: str) -> str:
    return re.sub(r"^\s*<!--.*?-->\s*", "", raw, flags=re.DOTALL).strip()


_cache: Dict[str, Tuple[str, str]] = {}


def get_prompt(name: str) -> Tuple[str, str]:
    """取 prompt 文本与版本号：planner=详情扩写，outline=四风格大纲"""
    if name in _cache:
        return _cache[name]
    path = os.path.join(_PROMPT_DIR, f"{name}.md")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        m = _VERSION_RE.search(raw)
        version = m.group(1) if m else f"{name}.unversioned"
        text = _strip_header(raw)
    except OSError as exc:
        logger.warning("⚠️  prompt 文件 %s 读取失败（%s），使用内嵌兜底", path, exc)
        text, version = _load_fallback(name), f"{name}.fallback"
    _cache[name] = (text, version)
    logger.info("📝 prompt 加载：%s（版本 %s，%d 字符）", name, version, len(text))
    return _cache[name]


def reset_prompts() -> None:
    """清缓存（测试与热更新用）"""
    _cache.clear()
