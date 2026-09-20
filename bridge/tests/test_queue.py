"""截图互斥串行，方舟分析并发上限。"""

import asyncio

from grafana_lens.queue import InspectionQueue


class SlowPlugin:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.order = []

    async def call(self, type_, payload, timeout):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.order.append(("start", payload["panelId"]))
        await asyncio.sleep(0.03)
        self.order.append(("end", payload["panelId"]))
        self.active -= 1
        return {"panelId": payload["panelId"]}


class SlowArk:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def vision_inspect(self, image_base64, panel_meta):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.04)
        self.active -= 1
        return panel_meta


async def test_captures_never_overlap():
    plugin = SlowPlugin()
    queue = InspectionQueue(analyze_concurrency=2)
    await asyncio.gather(
        queue.capture(plugin, {"panelId": 1}),
        queue.capture(plugin, {"panelId": 2}),
        queue.capture(plugin, {"panelId": 3}),
    )
    assert plugin.max_active == 1
    starts = [panel_id for kind, panel_id in plugin.order if kind == "start"]
    ends = [panel_id for kind, panel_id in plugin.order if kind == "end"]
    assert starts[0] == ends[0]


async def test_inspect_respects_analyze_concurrency():
    ark = SlowArk()
    queue = InspectionQueue(analyze_concurrency=2)
    await asyncio.gather(
        queue.inspect(ark, "img", {"panelId": 1}),
        queue.inspect(ark, "img", {"panelId": 2}),
        queue.inspect(ark, "img", {"panelId": 3}),
    )
    assert ark.max_active == 2


async def test_inspect_batch_uses_one_semaphore_slot():
    ark = SlowArk()
    queue = InspectionQueue(analyze_concurrency=1)
    items = [("img", {"panelId": 1}), ("img", {"panelId": 2})]
    await queue.inspect_batch(ark, items)
    assert ark.max_active == 1
