"""探针：直接跑 _gather_context（不走 LLM，不烧额度），验证 POI 采集层。

用法：.venv/Scripts/python.exe probe_gather.py
预期：6 个关键词 + 酒店 + 天气全部命中，日志无「CUQPS」字样。
"""

import asyncio
import sys

from app.agents.trip_planner_agent import TripPlannerService
from app.logging_setup import setup_logging
from app.models.schemas import TripRequest


async def main() -> int:
    setup_logging("INFO")
    request = TripRequest(
        city="深圳",
        start_date="2026-10-20",
        end_date="2026-10-20",
        travel_days=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
        free_text_input="",
    )
    service = TripPlannerService()
    pairs, hotels, weather = await service._gather_context(request)

    failed = [kw for kw, group in pairs if not group]
    print("=" * 60)
    print(f"关键词组: {len(pairs)} 组，空组: {failed or '无'}")
    print(f"候选总数: {sum(len(g) for _, g in pairs)}，酒店: {len(hotels)}，天气: {len(weather)} 天")
    ok = not failed and hotels and weather
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
