"""Grafana Lens 高层巡检服务：Agent 只通过本层调用完整业务流程。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from typing import Any

from .errors import LensError
from .evidence import enrich_with_evidence
from .models import Finding, ReportSynthesis, display_risk_level
from .plugin import PluginTimeoutError
from .queue import InspectionQueue
from .resume import RunStore


class InspectionService:
    def __init__(self, plugin, ark, config, *, run_store: RunStore | None = None) -> None:
        self.plugin = plugin
        self.ark = ark
        self.config = config
        self.queue = InspectionQueue(config.analyze_concurrency)
        self.runs = run_store or RunStore()
        self._worker_task: asyncio.Task | None = None
        self._worker_run_id: str | None = None
        self._worker_interrupted = False

    async def panel_by_id(self, panel_id: int) -> dict:
        panels = await self.plugin.call("list_panels", {}, 15)
        panel = next(
            (item for item in panels if isinstance(item, dict)
             and item.get("panelId") == panel_id),
            None,
        )
        if panel is None:
            raise LensError("PANEL_NOT_FOUND", str(panel_id))
        return panel

    async def query_datasource(
        self,
        panel_id: int,
        *,
        from_: str | None = None,
        to: str | None = None,
        mode: str = "range",
    ) -> dict:
        panel = await self.panel_by_id(panel_id)
        if panel.get("datasourceType") == "elasticsearch":
            raise LensError("EVIDENCE_UNSUPPORTED", "Elasticsearch 面板不做数值取证")
        return await self.plugin.call(
            "query_datasource",
            {
                "panel": panel,
                "from": from_ or panel.get("from") or "now-24h",
                "to": to or panel.get("to") or "now",
                "mode": mode,
                "maxDataPoints": 1200,
            },
            30,
        )

    async def analyze_panel(
        self,
        panel_id: int,
        *,
        from_: str | None = None,
        to: str | None = None,
        panel: dict | None = None,
        run_state: dict | None = None,
    ) -> dict:
        panel = dict(panel or await self.panel_by_id(panel_id))
        if from_ is not None:
            panel["from"] = from_
        if to is not None:
            panel["to"] = to
        capture_payload: dict[str, Any] = {"panelId": panel_id}
        if panel.get("from"):
            capture_payload["from"] = panel["from"]
        if panel.get("to"):
            capture_payload["to"] = panel["to"]

        owns_run = run_state is None
        if owns_run:
            try:
                dashboard = await self.plugin.call("get_meta", {}, 15)
            except (LensError, PluginTimeoutError):
                dashboard = {"uid": "panel", "title": str(
                    panel.get("title") or "panel")}
            from_ = from_ or panel.get("from") or dashboard.get("from")
            to = to or panel.get("to") or dashboard.get("to")
            run_state = self.runs.create(dashboard, [panel], from_, to)
            self.runs.panel_started(run_state, panel_id)

        try:
            capture = await self.queue.capture(self.plugin, capture_payload)
            finding = await self._finding_for_capture(panel, capture)
            self._persist_finding(run_state, panel, capture, finding)
            if owns_run:
                self.runs.finish(run_state, "done")
                await self.synthesize_run(run_state["runId"])
                run_state.update(self.runs.load(run_state["runId"]))
        except Exception as error:
            if owns_run:
                if isinstance(error, LensError):
                    body = error.to_body()
                elif isinstance(error, PluginTimeoutError):
                    body = LensError("RENDER_TIMEOUT", str(error)).to_body()
                else:
                    body = LensError("ARK_ERROR", type(
                        error).__name__).to_body()
                self.runs.panel_failed(run_state, panel_id, body)
                self.runs.finish(run_state, "failed")
            raise

        result = dict(run_state["panels"][str(panel_id)].get("finding") or {})
        result.pop("imageBase64", None)
        result["runId"] = run_state["runId"]
        return result

    async def analyze_dashboard(
        self,
        *,
        from_: str | None = None,
        to: str | None = None,
        max_panels: int | None = None,
        resume_run_id: str | None = None,
        force_new: bool = False,
    ) -> dict:
        if resume_run_id:
            state = self.runs.load(resume_run_id)
            dashboard = state["dashboard"]
            panels = [item["panel"] for item in state["panels"].values()]
            from_ = state.get("from")
            to = state.get("to")
        elif not force_new and (existing := self.runs.latest_active()):
            state = self.runs.load(existing["runId"])
            dashboard = state["dashboard"]
            panels = [item["panel"] for item in state["panels"].values()]
            from_ = state.get("from")
            to = state.get("to")
        else:
            dashboard = await self.plugin.call("get_meta", {}, 15)
            existing = None if force_new else self.runs.latest_for_dashboard(
                dashboard.get("uid"))
            if existing:
                state = self.runs.load(existing["runId"])
                dashboard = state["dashboard"]
                panels = [item["panel"] for item in state["panels"].values()]
                from_ = state.get("from")
                to = state.get("to")
            else:
                panels = await self.plugin.call("list_panels", {}, 15)
                limit = min(max_panels or self.config.max_panels_per_run,
                            self.config.max_panels_per_run)
                panels = panels[:limit]
                from_ = from_ or dashboard.get("from")
                to = to or dashboard.get("to")
                state = self.runs.create(dashboard, panels, from_, to)

        self._ensure_worker(state["runId"])
        wait = float(getattr(self.config, "mcp_call_budget_seconds", 20) or 0)
        deadline = time.monotonic() + wait
        await asyncio.sleep(0)
        while True:
            state = self.runs.load(state["runId"])
            remaining = self._remaining(state)
            timed_out = wait <= 0 or time.monotonic() >= deadline
            finished = bool(state.get("finishedAt"))
            if remaining == 0 and (finished or timed_out):
                break
            worker = self._worker_task
            worker_idle = (
                worker is None
                or worker.done()
                or self._worker_run_id != state["runId"]
            )
            if remaining > 0 and (timed_out or worker_idle):
                break
            slice_ = min(0.2, max(0.02, wait / 4 if wait else 0.02))
            await asyncio.sleep(slice_)
        return self._dashboard_result(
            state,
            self._worker_interrupted if self._worker_run_id == state["runId"] else False,
        )

    def _ensure_worker(self, run_id: str) -> None:
        task = self._worker_task
        if task is not None and not task.done() and self._worker_run_id == run_id:
            return
        if task is not None and not task.done():
            task.cancel()
        self._worker_interrupted = False
        self._worker_run_id = run_id
        self._worker_task = asyncio.create_task(
            self._worker_loop(run_id), name=f"lens-run-{run_id}")

    async def _worker_loop(self, run_id: str) -> None:
        try:
            while True:
                state = self.runs.load(run_id)
                from_ = state.get("from")
                to = state.get("to")
                panels = [item["panel"] for item in state["panels"].values()]
                to_capture = [
                    panel for panel in panels
                    if self._needs_capture(state["panels"][str(panel["panelId"])])
                ]
                to_analyze = [
                    panel for panel in panels
                    if self._needs_analyze(state["panels"][str(panel["panelId"])])
                ]
                if not to_capture and not to_analyze:
                    if state.get("status") == "running":
                        self.runs.finish(
                            state, "interrupted" if self._worker_interrupted else "done")
                        await self.synthesize_run(run_id)
                    return
                if to_capture and not self._worker_interrupted:
                    if await self._capture_all(state, to_capture, from_, to):
                        self._worker_interrupted = True
                    continue
                if to_analyze:
                    batch_size = self._vision_batch_size()
                    if await self._analyze_chunk(state, to_analyze[:batch_size]):
                        self._worker_interrupted = True
                        return
                    continue
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._worker_interrupted = True

    def _vision_batch_size(self) -> int:
        raw = int(getattr(self.config, "vision_batch_size", 8) or 8)
        return max(1, min(8, raw))

    def _needs_capture(self, item: dict) -> bool:
        if not self.runs.panel_unfinished(item):
            return False
        if item.get("state") == "captured":
            return False
        return not item.get("imagePath")

    def _needs_analyze(self, item: dict) -> bool:
        if not self.runs.panel_unfinished(item):
            return False
        return item.get("state") == "captured" or bool(item.get("imagePath"))

    def _capture_payload(self, panel: dict, from_, to) -> dict[str, Any]:
        payload: dict[str, Any] = {"panelId": int(panel["panelId"])}
        if from_ is not None:
            payload["from"] = from_
        elif panel.get("from"):
            payload["from"] = panel["from"]
        if to is not None:
            payload["to"] = to
        elif panel.get("to"):
            payload["to"] = panel["to"]
        return payload

    @staticmethod
    def _image_b64(item: dict) -> str:
        path = item.get("imagePath")
        if not path:
            return ""
        return base64.b64encode(Path(path).read_bytes()).decode()

    async def _capture_all(self, state: dict, panels: list[dict], from_, to) -> bool:
        """先把所有截图做完并落盘。返回 True 表示应中断后续截图。"""
        interrupt = False
        for panel in panels:
            panel_id = int(panel["panelId"])
            item = state["panels"][str(panel_id)]
            if not self._needs_capture(item):
                continue
            self.runs.panel_started(state, panel_id)
            try:
                capture = await self.queue.capture(
                    self.plugin, self._capture_payload(panel, from_, to))
            except LensError as error:
                self.runs.panel_failed(state, panel_id, error.to_body())
                if error.code == "PLUGIN_OFFLINE":
                    interrupt = True
                    break
                continue
            except PluginTimeoutError as error:
                self.runs.panel_failed(
                    state,
                    panel_id,
                    LensError("RUN_INTERRUPTED", str(error)).to_body(),
                )
                interrupt = True
                break
            except Exception as error:
                self.runs.panel_failed(
                    state,
                    panel_id,
                    LensError("ARK_ERROR", type(error).__name__).to_body(),
                )
                continue
            if self._is_no_data_capture(capture):
                finding = await self._finding_for_capture(panel, capture)
                self._persist_finding(state, panel, capture, finding)
            else:
                self.runs.panel_captured(state, panel_id, capture)
        return interrupt

    async def _analyze_chunk(self, state: dict, panels: list[dict]) -> bool:
        """只读已落盘截图调方舟，不再抢浏览器焦点。"""
        pending: list[tuple[dict, dict]] = []
        for panel in panels:
            panel_id = int(panel["panelId"])
            item = state["panels"][str(panel_id)]
            if not self._needs_analyze(item):
                continue
            self.runs.panel_started(state, panel_id)
            try:
                capture = {
                    **(item.get("capture") or {}),
                    "imageBase64": self._image_b64(item),
                }
            except OSError as error:
                self.runs.panel_failed(
                    state,
                    panel_id,
                    LensError("ARK_ERROR", type(error).__name__).to_body(),
                )
                continue
            pending.append((panel, capture))
        if not pending:
            return False
        items = [
            (
                capture["imageBase64"],
                {**panel, **(capture.get("panelMeta") or {})},
            )
            for panel, capture in pending
        ]
        try:
            findings = await self.queue.inspect_batch(self.ark, items)
        except LensError as error:
            for panel, _capture in pending:
                self.runs.panel_failed(
                    state, int(panel["panelId"]), error.to_body())
            return error.code == "PLUGIN_OFFLINE"
        except PluginTimeoutError as error:
            body = LensError("RUN_INTERRUPTED", str(error)).to_body()
            for panel, _capture in pending:
                self.runs.panel_failed(state, int(panel["panelId"]), body)
            return True
        except Exception as error:
            body = LensError("ARK_ERROR", type(error).__name__).to_body()
            for panel, _capture in pending:
                self.runs.panel_failed(state, int(panel["panelId"]), body)
            return False

        by_id = {int(finding.panel_id): finding for finding in findings}
        interrupt = False
        for (panel, capture), fallback in zip(pending, findings):
            panel_id = int(panel["panelId"])
            finding = by_id.get(panel_id, fallback)
            try:
                finding = await self._finding_for_capture(panel, capture, finding)
                self._persist_finding(state, panel, capture, finding)
            except LensError as error:
                self.runs.panel_failed(state, panel_id, error.to_body())
                if error.code == "PLUGIN_OFFLINE":
                    interrupt = True
            except Exception as error:
                self.runs.panel_failed(
                    state,
                    panel_id,
                    LensError("ARK_ERROR", type(error).__name__).to_body(),
                )
        return interrupt

    def _remaining(self, state: dict) -> int:
        return sum(
            1 for item in state["panels"].values()
            if self.runs.panel_unfinished(item)
        )

    def _dashboard_result(self, state: dict, interrupted: bool) -> dict:
        findings = [
            item["finding"]
            for item in state["panels"].values()
            if item.get("state") == "done" and item.get("finding")
        ]
        errors = [
            {"panelId": int(panel_id), "error": item.get("error")}
            for panel_id, item in state["panels"].items()
            if item.get("state") == "failed"
        ]
        remaining = self._remaining(state)
        risk_counts = {
            "high": sum(display_risk_level(item) == "high" for item in findings),
            "medium": sum(display_risk_level(item) == "medium" for item in findings),
            "low": sum(display_risk_level(item) == "low" for item in findings),
        }
        summary = {
            "total": len(state["panels"]),
            "done": len(findings),
            "failed": len(errors),
            "p0": sum(item.get("severity") == "P0" for item in findings),
            "p1": sum(item.get("severity") == "P1" for item in findings),
            "p2": sum(item.get("severity") == "P2" for item in findings),
            "normal": sum(item.get("status") == "normal" for item in findings),
            "needsHumanConfirm": sum(bool(item.get("needs_human_confirm")) for item in findings),
            **risk_counts,
        }
        result = {
            "runId": state["runId"],
            "dashboard": state.get("dashboard"),
            "findings": findings,
            "errors": errors,
            "summary": summary,
            "interrupted": interrupted,
            "synthesis": state.get("synthesis"),
            "status": state.get("status") or "running",
            "remaining": remaining,
        }
        if remaining:
            result["nextAction"] = (
                "截图阶段会占用 Chrome 焦点；截完后只读本地 JPEG 调方舟，不再抢窗口。"
                "再次调用 analyze_dashboard（不必传 resumeRunId）直到 remaining=0 再 render_report。"
                "不要 forceNew。"
            )
        return result

    async def synthesize_run(
        self,
        run_id: str,
        *,
        exec_hint: str = "",
        force: bool = False,
    ) -> dict | None:
        fn = getattr(self.ark, "synthesize", None)
        if not callable(fn):
            return None
        try:
            state = self.runs.load(run_id)
        except FileNotFoundError:
            return None
        if state.get("synthesis") and not force:
            return state["synthesis"]
        if not state.get("finishedAt"):
            inferred = None
            events = self.runs.root / \
                re.sub(r"[^a-zA-Z0-9._-]+", "-",
                       run_id).strip("-") / "events.jsonl"
            if events.exists():
                for line in events.read_text(encoding="utf-8").splitlines():
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("event") in {"run_done", "run_interrupted"}:
                        inferred = payload.get("at")
            if inferred:
                state["finishedAt"] = inferred
        briefing = self._synthesis_briefing(state, exec_hint)
        try:
            synthesis = await fn(briefing)
        except Exception:
            return None
        if not isinstance(synthesis, ReportSynthesis):
            try:
                synthesis = ReportSynthesis.model_validate(synthesis)
            except Exception:
                return None
        self._apply_synthesis(state, synthesis)
        state["synthesis"] = synthesis.model_dump()
        self.runs._write_state(state)
        return state["synthesis"]

    @staticmethod
    def _synthesis_briefing(state: dict, exec_hint: str) -> dict[str, Any]:
        layers: list[str] = []
        seen: set[str] = set()
        briefs: list[dict[str, Any]] = []
        for panel_id, item in (state.get("panels") or {}).items():
            panel = item.get("panel") or {}
            finding = item.get("finding")
            row = str(panel.get("rowTitle") or (
                finding or {}).get("row_title") or "未分组")
            if row not in seen:
                seen.add(row)
                layers.append(row)
            if finding:
                briefs.append(
                    {
                        "panel_id": finding.get("panel_id"),
                        "panel_title": finding.get("panel_title"),
                        "row_title": row,
                        "status": finding.get("status"),
                        "severity": finding.get("severity"),
                        "risk_level": finding.get("risk_level") or "none",
                        "risk": finding.get("risk"),
                        "suggestion": finding.get("suggestion"),
                        "observations": [
                            {"kind": obs.get("kind"),
                             "detail": obs.get("detail")}
                            for obs in (finding.get("observations") or [])[:6]
                            if isinstance(obs, dict)
                        ],
                        "printed_values": (finding.get("printed_values") or [])[:8],
                        "needs_human_confirm": finding.get("needs_human_confirm"),
                    }
                )
            elif item.get("state") == "failed":
                briefs.append(
                    {
                        "panel_id": int(panel_id),
                        "panel_title": panel.get("title"),
                        "row_title": row,
                        "status": "failed",
                        "error": item.get("error"),
                    }
                )
        return {
            "dashboard": {
                "uid": state.get("dashboardUid"),
                "title": (state.get("dashboard") or {}).get("title"),
            },
            "from": state.get("from"),
            "to": state.get("to"),
            "layers": layers,
            "findings": briefs,
            "operator_hint": (exec_hint or "")[:500],
        }

    @staticmethod
    def _apply_synthesis(state: dict, synthesis: ReportSynthesis) -> None:
        panels = state.get("panels") or {}
        for call in synthesis.risk_calls:
            item = panels.get(str(call.panel_id))
            finding = item.get("finding") if item else None
            if not isinstance(finding, dict):
                continue
            if finding.get("status") not in {"warning", "abnormal"}:
                continue
            finding["risk_level"] = call.risk_level

    async def _finding_for_capture(
        self,
        panel: dict,
        capture: dict,
        finding: Finding | None = None,
    ) -> Finding:
        panel_meta = {**panel, **(capture.get("panelMeta") or {})}
        if self._is_no_data_capture(capture):
            return self._skipped_no_data_finding(panel_meta)
        if finding is None:
            finding = await self.queue.inspect(
                self.ark, capture["imageBase64"], panel_meta)
        finding = self._enforce_panel_identity(finding, panel_meta)
        return await enrich_with_evidence(self.plugin, finding, panel_meta)

    def _persist_finding(
        self,
        run_state: dict,
        panel: dict,
        capture: dict,
        finding: Finding,
    ) -> None:
        persist = finding.model_dump()
        persist["row_title"] = str(panel.get("rowTitle") or "未分组")
        if self._is_no_data_capture(capture):
            self.runs.panel_done(run_state, int(panel["panelId"]), persist)
            return
        persist["capture"] = {
            key: capture.get(key)
            for key in ("mimeType", "width", "height", "sizeBytes", "panelMeta")
        }
        item = run_state["panels"][str(panel["panelId"])]
        if item.get("imagePath"):
            persist["imagePath"] = item["imagePath"]
        elif capture.get("imageBase64"):
            persist["imageBase64"] = capture.get("imageBase64")
        self.runs.panel_done(run_state, int(panel["panelId"]), persist)

    @staticmethod
    def _is_no_data_capture(capture: dict | None) -> bool:
        if not isinstance(capture, dict):
            return False
        if capture.get("skipped") or capture.get("noData"):
            return True
        return capture.get("reason") == "no_data"

    @staticmethod
    def _skipped_no_data_finding(panel: dict) -> Finding:
        datasource = str(
            panel.get("datasourceType") or panel.get("datasource_type") or "other")
        if datasource not in {"prometheus", "elasticsearch", "other"}:
            datasource = "other"
        return Finding(
            panel_id=int(panel.get("panelId") or panel.get("panel_id") or 0),
            panel_title=str(panel.get("title")
                            or panel.get("panel_title") or ""),
            panel_type=str(panel.get("type") or panel.get(
                "panel_type") or "other"),
            datasource_type=datasource,
            status="no_data",
            observations=[
                {
                    "kind": "no_data",
                    "detail": "面板显示 No data，已跳过截图与分析",
                    "confidence": 1.0,
                }
            ],
            printed_values=[],
            evidence_status="empty",
            severity="NONE",
            risk_level="none",
            confidence=1.0,
        )

    @staticmethod
    def _enforce_panel_identity(finding: Finding, panel: dict) -> Finding:
        datasource = panel.get("datasourceType") or "other"
        if datasource not in {"prometheus", "elasticsearch", "other"}:
            datasource = "other"
        data = finding.model_dump()
        data.update(
            panel_id=int(panel.get("panelId", finding.panel_id)),
            panel_title=str(panel.get("title", finding.panel_title)),
            panel_type=str(panel.get("type", finding.panel_type)),
            datasource_type=datasource,
        )
        if datasource == "elasticsearch":
            data["numeric_evidence"] = []
            data["evidence_status"] = "unsupported"
            data["evidence_error"] = "Elasticsearch 不做数值取证"
        return Finding.model_validate(data)
