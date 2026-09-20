"""高层巡检：Skill 应调用的 analyze_dashboard / query_datasource / render_report。"""

import asyncio
import json
import time
from types import SimpleNamespace

from grafana_lens.errors import LensError
from grafana_lens.inspection import InspectionService
from grafana_lens.mcp_server import build_server
from grafana_lens.models import Finding, Observation, PrintedValue
from grafana_lens.report import ReportRenderer
from grafana_lens.resume import RunStore


def _config():
    return SimpleNamespace(analyze_concurrency=2, max_panels_per_run=40, model="test-model")


def _finding(panel_id, *, status="warning", datasource="prometheus"):
    kwargs = dict(
        panel_id=panel_id,
        panel_title=f"P{panel_id}",
        panel_type="stat",
        datasource_type=datasource,
        status=status,
        observations=[Observation(
            kind="持续上涨" if status != "normal" else "稳定", detail="d", confidence=0.8)],
        printed_values=[PrintedValue(label="up", value="90%", source="stat")],
        severity="P1" if status != "normal" else "NONE",
    )
    if status != "normal":
        kwargs["risk"] = "风险"
        kwargs["suggestion"] = "建议"
        kwargs["risk_level"] = "medium"
    return Finding(**kwargs)


class FakePlugin:
    def __init__(self, *, fail_capture=None, no_data=None, timeline=None):
        self.calls = []
        self.fail_capture = fail_capture
        self.no_data = set(no_data or [])
        self.timeline = timeline if timeline is not None else []

    async def call(self, type_, payload, timeout):
        self.calls.append((type_, payload))
        if type_ == "get_meta":
            return {"uid": "dash", "title": "Dash", "from": "now-1h", "to": "now"}
        if type_ == "list_panels":
            return [
                {
                    "panelId": 1,
                    "title": "CPU",
                    "type": "stat",
                    "datasourceType": "prometheus",
                    "from": "now-1h",
                    "to": "now",
                    "targets": [{"refId": "A", "expr": "up", "datasource": {"uid": "prom", "type": "prometheus"}}],
                },
                {
                    "panelId": 2,
                    "title": "Logs",
                    "type": "logs",
                    "datasourceType": "elasticsearch",
                    "targets": [],
                },
            ]
        if type_ == "capture_panel":
            self.timeline.append("capture")
            if payload.get("panelId") in self.no_data:
                return {
                    "skipped": True,
                    "noData": True,
                    "reason": "no_data",
                    "panelMeta": {
                        "panelId": payload.get("panelId"),
                        "title": "CPU" if payload.get("panelId") == 1 else "Logs",
                        "type": "stat" if payload.get("panelId") == 1 else "logs",
                        "datasourceType": "prometheus" if payload.get("panelId") == 1 else "elasticsearch",
                        "viewPath": "spa",
                    },
                }
            if self.fail_capture == payload.get("panelId"):
                raise LensError("PLUGIN_OFFLINE")
            return {
                "imageBase64": "aGVsbG8=",
                "mimeType": "image/jpeg",
                "width": 10,
                "height": 10,
                "sizeBytes": 5,
                "panelMeta": {"panelId": payload["panelId"], "viewPath": "spa"},
            }
        if type_ == "query_datasource":
            return {
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {
                                    "fields": [
                                        {"name": "Time", "type": "time"},
                                        {"name": "Value", "type": "number",
                                            "labels": {}},
                                    ]
                                },
                                "data": {"values": [[1000, 2000], [0.5, 0.9]]},
                            }
                        ]
                    }
                }
            }
        raise AssertionError(type_)


class FakeArk:
    def __init__(self, timeline=None):
        self.vision_calls = 0
        self.batch_sizes = []
        self.timeline = timeline if timeline is not None else []

    def _finding_for(self, panel_meta):
        panel_id = int(panel_meta.get("panelId")
                       or panel_meta.get("panel_id") or 0)
        datasource = panel_meta.get("datasourceType") or "prometheus"
        status = "warning" if datasource == "prometheus" else "normal"
        return _finding(panel_id, status=status, datasource=datasource if datasource in {"prometheus", "elasticsearch"} else "other")

    async def vision_inspect(self, image_b64, panel_meta):
        self.vision_calls += 1
        self.batch_sizes.append(1)
        self.timeline.append("ark")
        return self._finding_for(panel_meta)

    async def vision_inspect_batch(self, items):
        self.vision_calls += 1
        self.batch_sizes.append(len(items))
        self.timeline.append("ark")
        return [self._finding_for(panel_meta) for _image, panel_meta in items]


class FakeSynthArk(FakeArk):
    async def synthesize(self, briefing):
        from grafana_lens.models import ReportSynthesis

        ids = [
            item["panel_id"]
            for item in briefing.get("findings") or []
            if item.get("status") in {"warning", "abnormal"}
        ]
        layers = briefing.get("layers") or ["未分组"]
        return ReportSynthesis(
            environment_score=64,
            summary="CPU 面板偏高，日志面板正常。",
            risk_calls=[{"panel_id": panel_id, "risk_level": "high"}
                        for panel_id in ids],
            layers=[
                {"name": name, "analysis": "本层需关注容量。", "no_risk": False}
                for name in layers
            ],
            actions=[
                {
                    "title": "核对 CPU limit",
                    "detail": "对照图印后扩容",
                    "panel_ids": ids,
                }
            ],
        )


async def test_analyze_panel_creates_run_without_image_bytes(tmp_path):
    plugin = FakePlugin()
    store = RunStore(tmp_path)
    service = InspectionService(plugin, FakeArk(), _config(), run_store=store)
    result = await service.analyze_panel(1)
    assert "imageBase64" not in result
    assert result["runId"]
    assert result["panel_id"] == 1
    saved = store.load(result["runId"])
    assert list(saved["panels"]) == ["1"]
    assert saved["panels"]["1"]["state"] == "done"
    assert "imageBase64" not in (saved["panels"]["1"].get("finding") or {})
    assert (tmp_path / result["runId"] / "artifacts" / "panel-1.jpg").exists()


async def test_analyze_dashboard_does_not_create_extra_runs(tmp_path):
    plugin = FakePlugin()
    store = RunStore(tmp_path)
    service = InspectionService(plugin, FakeArk(), _config(), run_store=store)
    result = await service.analyze_dashboard()
    run_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    assert result["runId"] == run_dirs[0].name
    for finding in result["findings"]:
        assert "imageBase64" not in finding


def test_synthesis_briefing_lists_unique_row_titles_in_dashboard_order():
    state = {
        "dashboardUid": "dash",
        "dashboard": {"title": "Dash"},
        "from": "now-1h",
        "to": "now",
        "panels": {
            "1": {
                "panel": {"panelId": 1, "title": "CPU", "rowTitle": "入口层"},
                "finding": {"panel_id": 1, "panel_title": "CPU", "status": "normal"},
            },
            "2": {
                "panel": {"panelId": 2, "title": "MEM", "rowTitle": "入口层"},
                "finding": {"panel_id": 2, "panel_title": "MEM", "status": "warning"},
            },
            "3": {
                "panel": {"panelId": 3, "title": "DB", "rowTitle": "应用层"},
                "state": "failed",
                "error": {"code": "ARK_ERROR"},
            },
        },
    }
    briefing = InspectionService._synthesis_briefing(state, "")
    assert briefing["layers"] == ["入口层", "应用层"]
    assert "系统使用信息" not in briefing["layers"]


async def test_analyze_dashboard_captures_analyzes_and_evidences(tmp_path):
    plugin = FakePlugin()
    service = InspectionService(
        plugin, FakeArk(), _config(), run_store=RunStore(tmp_path))
    result = await service.analyze_dashboard()
    assert result["interrupted"] is False
    assert result["summary"]["done"] == 2
    cpu = next(item for item in result["findings"] if item["panel_id"] == 1)
    logs = next(item for item in result["findings"] if item["panel_id"] == 2)
    assert cpu["evidence_status"] == "ok"
    assert logs["evidence_status"] == "unsupported"
    assert any(type_ == "query_datasource" for type_, _ in plugin.calls)
    assert sum(type_ == "capture_panel" for type_, _ in plugin.calls) == 2


async def test_analyze_dashboard_skips_no_data_panel_without_ark_or_retry(tmp_path):
    """Grafana 显示 No data 的面板必须立刻跳过，不能空等截图超时，也不能调方舟。"""
    plugin = FakePlugin(no_data={1})
    ark = FakeArk()
    service = InspectionService(
        plugin, ark, _config(), run_store=RunStore(tmp_path))
    result = await service.analyze_dashboard()
    cpu = next(item for item in result["findings"] if item["panel_id"] == 1)
    logs = next(item for item in result["findings"] if item["panel_id"] == 2)
    assert cpu["status"] == "no_data"
    assert cpu["severity"] == "NONE"
    assert logs["status"] == "normal"
    assert ark.vision_calls == 1
    captured = [payload.get("panelId")
                for type_, payload in plugin.calls if type_ == "capture_panel"]
    assert captured.count(1) == 1
    assert result["summary"]["done"] == 2
    assert result["remaining"] == 0


async def test_analyze_dashboard_sends_two_panels_in_one_ark_call(tmp_path):
    plugin = FakePlugin()
    ark = FakeArk()
    cfg = SimpleNamespace(
        analyze_concurrency=2,
        vision_batch_size=4,
        max_panels_per_run=40,
        model="test-model",
    )
    service = InspectionService(plugin, ark, cfg, run_store=RunStore(tmp_path))
    result = await service.analyze_dashboard()
    assert result["summary"]["done"] == 2
    assert ark.vision_calls == 1
    assert ark.batch_sizes == [2]


async def test_analyze_dashboard_captures_all_before_ark(tmp_path):
    """整看板必须先截完再读图，避免方舟等待期间反复 captureVisibleTab 抢焦点。"""
    timeline = []
    plugin = FakePlugin(timeline=timeline)
    ark = FakeArk(timeline=timeline)
    cfg = SimpleNamespace(
        analyze_concurrency=1,
        vision_batch_size=1,
        max_panels_per_run=40,
        model="test-model",
    )
    service = InspectionService(plugin, ark, cfg, run_store=RunStore(tmp_path))
    result = await service.analyze_dashboard()
    assert result["summary"]["done"] == 2
    assert timeline.count("capture") == 2
    assert timeline.count("ark") == 2
    last_capture = max(i for i, event in enumerate(
        timeline) if event == "capture")
    first_ark = timeline.index("ark")
    assert last_capture < first_ark


async def test_analyze_dashboard_resume_analyzes_captured_without_recapture(tmp_path):
    store = RunStore(tmp_path)
    plugin = FakePlugin()
    ark = FakeArk()
    service = InspectionService(plugin, ark, _config(), run_store=store)
    meta = await plugin.call("get_meta", {}, 1)
    panels = await plugin.call("list_panels", {}, 1)
    state = store.create(meta, panels[:1], meta.get("from"), meta.get("to"))
    store.panel_started(state, 1)
    store.panel_captured(
        state,
        1,
        {
            "imageBase64": "aGVsbG8=",
            "mimeType": "image/jpeg",
            "width": 10,
            "height": 10,
            "sizeBytes": 5,
            "panelMeta": {"panelId": 1, "viewPath": "spa"},
        },
    )
    plugin.calls.clear()
    result = await service.analyze_dashboard(resume_run_id=state["runId"])
    captured = [
        payload.get("panelId")
        for type_, payload in plugin.calls
        if type_ == "capture_panel"
    ]
    assert captured == []
    assert result["summary"]["done"] == 1
    assert ark.vision_calls == 1


async def test_analyze_dashboard_resume_skips_done_panels(tmp_path):
    plugin = FakePlugin()
    store = RunStore(tmp_path)
    service = InspectionService(plugin, FakeArk(), _config(), run_store=store)
    first = await service.analyze_dashboard()
    plugin.calls.clear()
    second = await service.analyze_dashboard(resume_run_id=first["runId"])
    assert second["runId"] == first["runId"]
    assert not any(type_ == "capture_panel" for type_, _ in plugin.calls)


class SlowArk(FakeArk):
    def __init__(self, delay: float = 0.25):
        super().__init__()
        self.delay = delay

    async def vision_inspect(self, image_b64, panel_meta):
        await asyncio.sleep(self.delay)
        return await FakeArk.vision_inspect(self, image_b64, panel_meta)

    async def vision_inspect_batch(self, items):
        await asyncio.sleep(self.delay)
        return await FakeArk.vision_inspect_batch(self, items)


async def test_analyze_dashboard_returns_within_budget_while_worker_continues(tmp_path):
    """豆包 MCP 超时 60s：工具必须先回包，分析在后台继续，不能把方舟 60s 等进这次调用。"""
    plugin = FakePlugin()
    cfg = SimpleNamespace(
        analyze_concurrency=1,
        max_panels_per_run=40,
        max_panels_per_call=0,
        mcp_call_budget_seconds=0.08,
        model="test-model",
    )
    store = RunStore(tmp_path)
    service = InspectionService(plugin, SlowArk(0.25), cfg, run_store=store)
    started = time.monotonic()
    first = await service.analyze_dashboard()
    elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert first["remaining"] >= 1
    assert first["status"] == "running"
    await asyncio.sleep(0.8)
    second = await service.analyze_dashboard()
    assert second["runId"] == first["runId"]
    assert second["remaining"] == 0
    assert second["summary"]["done"] == 2


async def test_analyze_dashboard_auto_resumes_unfinished_run_without_resume_id(tmp_path):
    """豆包经常不传 resumeRunId，缺省必须续跑同一看板未完成 run，不能从头截第一行。"""
    plugin = FakePlugin()
    cfg = SimpleNamespace(
        analyze_concurrency=1,
        max_panels_per_run=40,
        max_panels_per_call=0,
        mcp_call_budget_seconds=0.08,
        model="test-model",
    )
    store = RunStore(tmp_path)
    service = InspectionService(plugin, SlowArk(0.25), cfg, run_store=store)
    first = await service.analyze_dashboard()
    assert first["remaining"] >= 1
    plugin.calls.clear()
    await asyncio.sleep(0.8)
    second = await service.analyze_dashboard()
    assert second["runId"] == first["runId"]
    assert second["summary"]["done"] == 2
    assert second["remaining"] == 0
    assert len([path for path in tmp_path.iterdir() if path.is_dir()]) == 1


async def test_analyze_dashboard_poll_does_not_call_plugin_while_run_active(tmp_path):
    """豆包轮询时若再打 get_meta，会被正在进行的截图堵住 15s，表面成连接器超时。"""
    plugin = FakePlugin()
    cfg = SimpleNamespace(
        analyze_concurrency=1,
        max_panels_per_run=40,
        max_panels_per_call=0,
        mcp_call_budget_seconds=0.08,
        model="test-model",
    )
    store = RunStore(tmp_path)
    service = InspectionService(plugin, SlowArk(0.25), cfg, run_store=store)
    first = await service.analyze_dashboard()
    assert first["remaining"] >= 1
    plugin.calls.clear()
    original = plugin.call

    async def gated(type_, payload, timeout):
        if type_ in {"get_meta", "list_panels"}:
            raise AssertionError("active run 轮询不得再调用 get_meta/list_panels")
        return await original(type_, payload, timeout)

    plugin.call = gated  # type: ignore[method-assign]
    second = await service.analyze_dashboard()
    assert second["runId"] == first["runId"]
    assert second["remaining"] >= 0
    assert not any(type_ in {"get_meta", "list_panels"}
                   for type_, _ in plugin.calls)


async def test_analyze_dashboard_force_new_starts_fresh_run(tmp_path):
    plugin = FakePlugin()
    cfg = SimpleNamespace(
        analyze_concurrency=1,
        max_panels_per_run=40,
        max_panels_per_call=0,
        mcp_call_budget_seconds=0.08,
        model="test-model",
    )
    store = RunStore(tmp_path)
    service = InspectionService(plugin, SlowArk(0.25), cfg, run_store=store)
    first = await service.analyze_dashboard()
    second = await service.analyze_dashboard(force_new=True)
    assert second["runId"] != first["runId"]
    assert len([path for path in tmp_path.iterdir() if path.is_dir()]) == 2


async def test_analyze_dashboard_retries_failed_panel_without_recapturing_done(tmp_path):
    plugin = FakePlugin(fail_capture=2)
    cfg = SimpleNamespace(
        analyze_concurrency=1,
        max_panels_per_run=40,
        max_panels_per_call=0,
        mcp_call_budget_seconds=20,
        model="test-model",
    )
    store = RunStore(tmp_path)
    service = InspectionService(plugin, FakeArk(), cfg, run_store=store)
    first = await service.analyze_dashboard()
    assert first["summary"]["done"] == 1
    assert first["summary"]["failed"] == 1
    plugin.fail_capture = None
    plugin.calls.clear()
    second = await service.analyze_dashboard()
    assert second["runId"] == first["runId"]
    captured = [payload.get("panelId") for type_,
                payload in plugin.calls if type_ == "capture_panel"]
    assert 1 not in captured
    if captured:
        assert captured == [2]
    assert second["summary"]["done"] + second["summary"]["failed"] == 2


async def test_analyze_dashboard_marks_interrupted_on_plugin_offline(tmp_path):
    plugin = FakePlugin(fail_capture=1)
    service = InspectionService(
        plugin, FakeArk(), _config(), run_store=RunStore(tmp_path))
    result = await service.analyze_dashboard()
    assert result["interrupted"] is True
    assert result["summary"]["failed"] >= 1


async def test_analyze_dashboard_synthesizes_risk_levels(tmp_path):
    plugin = FakePlugin()
    store = RunStore(tmp_path)
    service = InspectionService(
        plugin, FakeSynthArk(), _config(), run_store=store)
    result = await service.analyze_dashboard()
    assert result["summary"]["high"] == 1
    assert result["synthesis"]["environment_score"] == 64
    saved = store.load(result["runId"])
    assert saved["panels"]["1"]["finding"]["risk_level"] == "high"
    assert saved["finishedAt"]


async def test_query_datasource_rejects_elasticsearch(tmp_path):
    service = InspectionService(
        FakePlugin(), FakeArk(), _config(), run_store=RunStore(tmp_path))
    try:
        await service.query_datasource(2)
        raise AssertionError("ES 面板应拒绝取证")
    except LensError as error:
        assert error.code == "EVIDENCE_UNSUPPORTED"


async def test_mcp_skill_tools_run_and_render(tmp_path):
    plugin = FakePlugin()
    store = RunStore(tmp_path / "runs")
    service = InspectionService(plugin, FakeArk(), _config(), run_store=store)
    reporter = ReportRenderer(
        _config(), run_store=store, output_root=tmp_path / "reports")
    reporter.template_root = __import__("pathlib").Path(
        __file__).resolve().parents[2] / "report"
    server = build_server(plugin=plugin, inspection=service, reporter=reporter)

    analyzed = await server.call_tool("analyze_dashboard", {"maxPanels": 2})
    body = json.loads(analyzed.content[0].text)
    assert analyzed.is_error is False
    assert body["summary"]["done"] == 2

    queried = await server.call_tool("query_datasource", {"panelId": 1})
    assert queried.is_error is False

    report = await server.call_tool("render_report", {"runId": body["runId"], "openReport": False})
    rendered = json.loads(report.content[0].text)
    assert report.is_error is False
    html = __import__("pathlib").Path(
        rendered["path"]).read_text(encoding="utf-8")
    assert "巡检报告" in html
