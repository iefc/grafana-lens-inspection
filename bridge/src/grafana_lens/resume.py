"""巡检 run 的原子状态、事件流与截图 artifact 存储。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import lens_home


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "dashboard"


class RunStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or lens_home() / "runs"

    def create(self, dashboard: dict, panels: list[dict], from_: str | None, to: str | None) -> dict:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        run_id = f"run-{stamp}-{_safe(str(dashboard.get('uid') or 'dashboard'))}"
        state = {
            "runId": run_id,
            "dashboard": dashboard,
            "dashboardUid": dashboard.get("uid"),
            "from": from_,
            "to": to,
            "status": "running",
            "panels": {
                str(panel["panelId"]): {"state": "pending", "panel": panel, "attempts": 0}
                for panel in panels
            },
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        self._write_state(state)
        self.event(run_id, "run_started", {"panelCount": len(panels)})
        return state

    def load(self, run_id: str) -> dict:
        path = self.root / _safe(run_id) / "state.json"
        if not path.exists():
            raise FileNotFoundError(run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def panel_started(self, state: dict, panel_id: int) -> None:
        item = state["panels"][str(panel_id)]
        item["state"] = "running"
        item["attempts"] = int(item.get("attempts", 0)) + 1
        self._write_state(state)
        self.event(state["runId"], "panel_started", {
                   "panelId": panel_id, "attempt": item["attempts"]})

    def panel_done(self, state: dict, panel_id: int, result: dict) -> None:
        path = self._store_jpeg(state["runId"], panel_id, result.pop("imageBase64", None))
        if path:
            result["imagePath"] = path
        elif not result.get("imagePath"):
            existing = state["panels"][str(panel_id)].get("imagePath")
            if existing:
                result["imagePath"] = existing
        state["panels"][str(panel_id)].update(
            state="done", finding=result, error=None)
        self._write_state(state)
        self.event(state["runId"], "panel_done", {"panelId": panel_id})

    def panel_captured(self, state: dict, panel_id: int, capture: dict) -> None:
        """截图已落盘，尚未读图。分析阶段不再占用浏览器。"""
        path = self._store_jpeg(state["runId"], panel_id, capture.get("imageBase64"))
        item = state["panels"][str(panel_id)]
        item.update(
            state="captured",
            attempts=0,
            error=None,
            imagePath=path,
            capture={
                key: capture.get(key)
                for key in ("mimeType", "width", "height", "sizeBytes", "panelMeta")
            },
        )
        self._write_state(state)
        self.event(state["runId"], "panel_captured", {"panelId": panel_id})

    def _store_jpeg(self, run_id: str, panel_id: int, image: str | None) -> str | None:
        if not image:
            return None
        import base64

        artifact = self.root / _safe(run_id) / "artifacts" / f"panel-{panel_id}.jpg"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(base64.b64decode(image))
        os.chmod(artifact, 0o600)
        return str(artifact)

    def panel_failed(self, state: dict, panel_id: int, error: dict) -> None:
        state["panels"][str(panel_id)].update(state="failed", error=error)
        self._write_state(state)
        self.event(state["runId"], "panel_failed", {
                   "panelId": panel_id, "code": error.get("code")})

    def finish(self, state: dict, status: str = "done") -> None:
        state["status"] = status
        state["finishedAt"] = _now()
        self._write_state(state)
        self.event(state["runId"], "run_done" if status ==
                   "done" else "run_interrupted", {})

    @staticmethod
    def panel_unfinished(item: dict) -> bool:
        if item.get("state") == "done":
            return False
        if item.get("state") == "failed" and int(item.get("attempts", 0)) >= 2:
            return False
        return True

    def latest_unfinished(self, dashboard_uid: str | None) -> dict | None:
        """同一看板未完成 run：优先进度最多，其次最近更新。"""
        if not dashboard_uid or not self.root.exists():
            return None
        best: dict | None = None
        best_key: tuple[int, str] = (-1, "")
        for path in self.root.iterdir():
            state_path = path / "state.json"
            if not path.is_dir() or not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if str(state.get("dashboardUid") or "") != str(dashboard_uid):
                continue
            if state.get("status") not in {"running", "interrupted"}:
                continue
            panels = state.get("panels") or {}
            if not any(self.panel_unfinished(item) for item in panels.values()):
                continue
            done = sum(1 for item in panels.values()
                       if item.get("state") == "done")
            stamp = str(state.get("updatedAt")
                        or state.get("createdAt") or path.name)
            key = (done, stamp)
            if key >= best_key:
                best_key = key
                best = state
        return best

    def latest_active(self) -> dict | None:
        """任意看板正在跑/中断的未完成 run，按 updatedAt 取最近一条。轮询不得再打插件。"""
        if not self.root.exists():
            return None
        best: dict | None = None
        best_stamp = ""
        for path in self.root.iterdir():
            state_path = path / "state.json"
            if not path.is_dir() or not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if state.get("status") not in {"running", "interrupted"}:
                continue
            panels = state.get("panels") or {}
            if not any(self.panel_unfinished(item) for item in panels.values()):
                continue
            stamp = str(state.get("updatedAt")
                        or state.get("createdAt") or path.name)
            if stamp >= best_stamp:
                best_stamp = stamp
                best = state
        return best

    def latest_for_dashboard(
        self,
        dashboard_uid: str | None,
        *,
        attach_done_within_seconds: int = 7200,
    ) -> dict | None:
        """优先未完成 run；否则复用同一看板 2 小时内刚完成的 run（豆包超时后轮询，禁止新开）。"""
        unfinished = self.latest_unfinished(dashboard_uid)
        if unfinished:
            return unfinished
        if not dashboard_uid or not self.root.exists():
            return None
        best: dict | None = None
        best_stamp = ""
        for path in self.root.iterdir():
            state_path = path / "state.json"
            if not path.is_dir() or not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if str(state.get("dashboardUid") or "") != str(dashboard_uid):
                continue
            stamp = str(
                state.get("updatedAt")
                or state.get("finishedAt")
                or state.get("createdAt")
                or path.name
            )
            if stamp >= best_stamp:
                best_stamp = stamp
                best = state
        if best is None:
            return None
        stamp = best.get("updatedAt") or best.get(
            "finishedAt") or best.get("createdAt")
        parsed = _parse_iso(stamp)
        if parsed is None:
            return best
        age = (datetime.now(timezone.utc) -
               parsed.astimezone(timezone.utc)).total_seconds()
        if age <= attach_done_within_seconds:
            return best
        return None

    def event(self, run_id: str, event: str, payload: dict) -> None:
        run_dir = self.root / _safe(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(
                {"at": _now(), "event": event, **payload}, ensure_ascii=False) + "\n")

    def _write_state(self, state: dict) -> None:
        state["updatedAt"] = _now()
        run_dir = self.root / _safe(state["runId"])
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / "state.json"
        temporary = run_dir / ".state.json.tmp"
        temporary.write_text(json.dumps(
            state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
