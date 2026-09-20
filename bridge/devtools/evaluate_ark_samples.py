import asyncio
import base64
import json
from pathlib import Path

import httpx

from grafana_lens.analyzer import ArkClient
from grafana_lens.config import LensConfig

SAMPLES = [
    ("panel-19-timeseries.jpg", {"panelId": 19, "title": "网络占用", "type": "timeseries"}),
    ("panel-111-graph.jpg", {"panelId": 111, "title": "磁盘IO读写延迟趋势", "type": "graph"}),
    ("panel-90-table.jpg", {"panelId": 90, "title": "请求详情表", "type": "table"}),
    ("panel-23-stat.jpg", {"panelId": 23, "title": "操作系统类型", "type": "stat"}),
    ("panel-17-piechart.jpg", {"panelId": 17, "title": "状态码分布", "type": "piechart"}),
]


async def main():
    config = LensConfig.load()
    output = []
    async with httpx.AsyncClient(timeout=30) as http:
        ark = ArkClient(config, client=http)
        for filename, meta in SAMPLES:
            image = Path("/tmp/lens-echo") / filename
            if not image.exists():
                output.append({"file": filename, "ok": False, "error": "missing_sample"})
                continue
            try:
                finding = await asyncio.wait_for(
                    ark.vision_inspect(base64.b64encode(image.read_bytes()).decode(), meta),
                    timeout=35,
                )
                output.append({"file": filename, "ok": True, "finding": finding.model_dump()})
            except Exception as error:
                output.append({"file": filename, "ok": False, "error": type(error).__name__})
    target = Path("/tmp/lens-ark-evaluation.json")
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evaluated": len(output), "success": sum(x["ok"] for x in output), "result": str(target)}, ensure_ascii=False))


asyncio.run(main())
