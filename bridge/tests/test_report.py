"""单文件 HTML 报告：四段结构、截图内联、脱敏、转义。"""

from types import SimpleNamespace

from grafana_lens.report import ReportRenderer
from grafana_lens.resume import RunStore


def _config():
    return SimpleNamespace(model="seed-2.1-pro-0915")


def _prepare_run(tmp_path):
    store = RunStore(tmp_path / "runs")
    state = store.create(
        {"uid": "k8s-overview", "title": "prod 10.1.2.3 http://grafana.internal/d/x"},
        [
            {"panelId": 1, "title": "CPU", "rowTitle": "节点资源"},
            {"panelId": 2, "title": "OK", "rowTitle": "节点资源"},
            {"panelId": 3, "title": "Fail", "rowTitle": "节点资源"},
        ],
        "now-24h",
        "now",
    )
    store.panel_started(state, 1)
    store.panel_done(
        state,
        1,
        {
            "panel_id": 1,
            "panel_title": "CPU <script>",
            "status": "warning",
            "severity": "P1",
            "risk_level": "none",
            "confidence": 0.8,
            "observations": [{"kind": "持续上涨", "detail": "上升"}],
            "printed_values": [{"label": "cpu", "value": "89%", "source": "stat"}],
            "numeric_evidence": [{"agg": "latest", "value": 0.89, "at": "t"}],
            "risk": "OOM",
            "suggestion": "扩容",
            "needs_human_confirm": True,
            "conflict": "printed_vs_evidence",
            "imageBase64": "aGVsbG8=",
            "row_title": "节点资源",
        },
    )
    store.panel_started(state, 2)
    store.panel_done(
        state,
        2,
        {
            "panel_id": 2,
            "panel_title": "OK",
            "status": "normal",
            "severity": "NONE",
            "risk_level": "none",
            "confidence": 0.9,
            "observations": [{"kind": "稳定", "detail": "平稳"}],
            "printed_values": [{"label": "ok", "value": "1", "source": "stat"}],
            "numeric_evidence": [],
            "imageBase64": "aGVsbG8=",
            "row_title": "节点资源",
        },
    )
    store.panel_started(state, 3)
    store.panel_failed(
        state, 3, {"code": "RENDER_TIMEOUT", "message": "timeout"})
    store.finish(state, "done")
    state = store.load(state["runId"])
    state["synthesis"] = {
        "environment_score": 72,
        "summary": "CPU 持续上涨存在 OOM 风险，见 10.9.9.9。节点资源层需优先处理。",
        "risk_calls": [{"panel_id": 1, "risk_level": "high"}],
        "layers": [
            {"name": "节点资源", "analysis": "CPU 偏高可能引发节流与驱逐。", "no_risk": False}
        ],
        "actions": [
            {"title": "扩容 CPU", "detail": "核对 limit 后扩容", "panel_ids": [1]},
            {"title": "核对图例刻度", "detail": "下一窗口再观察", "panel_ids": []},
        ],
    }
    state["panels"]["1"]["finding"]["risk_level"] = "high"
    store._write_state(state)
    return store, state["runId"]


def test_render_is_self_contained_and_has_four_sections(tmp_path):
    store, run_id = _prepare_run(tmp_path)
    renderer = ReportRenderer(
        _config(),
        run_store=store,
        project_root=tmp_path,
        output_root=tmp_path / "reports",
    )
    renderer.template_root = __import__("pathlib").Path(
        __file__).resolve().parents[2] / "report"
    result = renderer.render(run_id, exec_summary="摘要 <b>raw</b> 见 10.8.8.8")
    html = __import__("pathlib").Path(
        result["path"]).read_text(encoding="utf-8")

    assert "1. 巡检概览" in html
    assert "2. 风险识别" in html
    assert "3. 巡检详情" in html
    assert "4. 行动建议" in html
    assert "巡检总结" in html
    assert "2. 问题清单" not in html
    assert "节点资源" in html
    assert "扩容 CPU" in html
    assert "CPU 偏高可能引发节流" in html
    assert "<svg" in html
    assert html.count("data:image/jpeg;base64,") >= 1
    assert "RENDER_TIMEOUT" in html
    assert "<link " not in html
    assert "x.x.x.x" in html
    assert "https://***" in html
    assert "<b>raw</b>" not in html
    assert "CPU &lt;script&gt;" in html
    assert "72" in html
    assert result["sizeBytes"] > 0
    assert "chart-board" in html
    assert "失败" in html
    assert "按风险回退" not in html
    assert "risk-card" in html
    assert "action-card" in html
    assert "必须修复" in html
    assert "优先跟进" in html
    assert "告警" in html
    assert "面板状态" in html
    assert "严重度" in html
    assert "巡检覆盖" in html
    visible, _appendix = html.split("原始 Findings JSON", 1)
    assert "warning" not in visible
    assert "abnormal" not in visible
    assert " · warning" not in html


def test_render_keeps_long_agent_exec_summary(tmp_path):
    store, run_id = _prepare_run(tmp_path)
    state = store.load(run_id)
    state.pop("synthesis", None)
    store._write_state(state)
    summary = (
        "本轮检查私有云日常巡检看板 93 块面板，完成 85、失败 8（方舟 ReadTimeout，均为资源用量相关图）。"
        "高风险 7、中风险 10、低风险 9；P1 8、P2 18；待人工确认 4。"
        "主要风险是集群内存实际使用率接近 90%、多台节点内存 93% 以上、CPU TOP 接近满载、磁盘 IO 饱和，"
        "并伴随近 1 天 112 次服务驱逐与告警量偏高；入口层平均请求时间约 5s、p99 最高约 8.4s。"
        "失败 8 项未出结论，不能当成资源正常。"
    )
    assert len(summary) > 200
    renderer = ReportRenderer(
        _config(),
        run_store=store,
        project_root=tmp_path,
        output_root=tmp_path / "reports",
    )
    renderer.template_root = __import__("pathlib").Path(
        __file__).resolve().parents[2] / "report"
    html = __import__("pathlib").Path(
        renderer.render(run_id, exec_summary=summary)["path"]
    ).read_text(encoding="utf-8")
    assert "失败 8 项未出结论，不能当成资源正常。" in html
    assert "max-width: 68ch" not in html


def test_duration_uses_finished_at_not_updated_at(tmp_path):
    store, run_id = _prepare_run(tmp_path)
    state = store.load(run_id)
    state["createdAt"] = "2026-09-19T01:47:15Z"
    state["finishedAt"] = "2026-09-19T01:53:58Z"
    state["updatedAt"] = "2026-09-19T08:46:51Z"
    store._write_state(state)
    renderer = ReportRenderer(
        _config(),
        run_store=store,
        project_root=tmp_path,
        output_root=tmp_path / "reports",
    )
    renderer.template_root = __import__("pathlib").Path(
        __file__).resolve().parents[2] / "report"
    html = __import__("pathlib").Path(
        renderer.render(run_id)["path"]).read_text(encoding="utf-8")
    assert "6 分 43 秒" in html
    assert "小时" not in html.split("巡检耗时", 1)[1][:200]


def test_score_and_duration_fallback_when_synthesis_missing(tmp_path):
    store, run_id = _prepare_run(tmp_path)
    state = store.load(run_id)
    state.pop("synthesis", None)
    state.pop("finishedAt", None)
    state["status"] = "running"
    state["createdAt"] = "2026-09-19T01:47:15Z"
    store._write_state(state)
    events = store.root / run_id / "events.jsonl"
    events.write_text(
        '{"event":"panel_done","at":"2026-09-19T01:51:00Z"}\n',
        encoding="utf-8",
    )
    renderer = ReportRenderer(
        _config(),
        run_store=store,
        project_root=tmp_path,
        output_root=tmp_path / "reports",
    )
    renderer.template_root = __import__("pathlib").Path(
        __file__).resolve().parents[2] / "report"
    html = __import__("pathlib").Path(
        renderer.render(run_id)["path"]).read_text(encoding="utf-8")
    assert "按风险回退" in html
    assert "3 分 45 秒" in html
    assert "创建至最近活动" in html
    assert "aria-label=\"环境评分 92\"" in html or "环境评分 92" in html


def test_bar_svg_is_vertical_with_tilted_layer_labels():
    rows = [(f"层级名称{index}", 2, 1, 0) for index in range(8)]
    svg = str(ReportRenderer._bar_svg(rows))
    assert 'rotate(-42' in svg
    assert 'preserveAspectRatio="xMidYMax meet"' in svg
    view = svg.split('viewBox="')[1].split('"')[0]
    _x, _y, width, height = view.split()
    assert width == "640"
    assert int(height) <= 280
    assert "各层风险竖形条形图" in svg
