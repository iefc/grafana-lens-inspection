"""巡检调度原语：截图串行、模型分析有限并发。"""

from __future__ import annotations

import asyncio


class InspectionQueue:
    def __init__(self, analyze_concurrency: int = 2) -> None:
        self.screenshot_lock = asyncio.Lock()
        self.analyze_semaphore = asyncio.Semaphore(max(1, analyze_concurrency))

    async def capture(self, plugin, payload: dict) -> dict:
        async with self.screenshot_lock:
            return await plugin.call("capture_panel", payload, 120)

    async def inspect(self, ark, image_base64: str, panel_meta: dict):
        async with self.analyze_semaphore:
            return await ark.vision_inspect(image_base64, panel_meta)

    async def inspect_batch(self, ark, items: list[tuple[str, dict]]):
        if not items:
            return []
        async with self.analyze_semaphore:
            batch = getattr(ark, "vision_inspect_batch", None)
            if callable(batch):
                return await batch(items)
            return [
                await ark.vision_inspect(image_base64, panel_meta)
                for image_base64, panel_meta in items
            ]
