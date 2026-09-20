"""从持久化 run 渲染单文件自包含 HTML 巡检报告。"""

from __future__ import annotations

import base64
import html as html_lib
import json
import math
import re
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .models import display_risk_level
from .resume import RunStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}
RISK_LABEL = {"high": "高", "medium": "中", "low": "低", "none": "无"}
RISK_COLOR = {
    "high": "#F4584B",
    "medium": "#FB8A3C",
    "low": "#F2B93B",
    "none": "#5D6B87",
}
STATUS_LABEL = {
    "normal": "正常",
    "warning": "告警",
    "abnormal": "异常",
    "no_data": "无数据",
}
STATUS_COLOR = {
    "abnormal": "#F4584B",
    "warning": "#FB8A3C",
    "normal": "#35D0A4",
    "no_data": "#5D6B87",
}
SEV_COLOR = {"P0": "#F4584B", "P1": "#FB8A3C", "P2": "#F2B93B"}
ACTION_GROUPS = (
    ("high", "必须修复", "高优先级，本轮就要处理"),
    ("medium", "优先跟进", "中优先级，尽快安排窗口"),
    ("low", "改进措施", "低优先级，纳入后续迭代"),
)


class ReportRenderer:
    def __init__(
        self,
        config,
        *,
        run_store: RunStore | None = None,
        project_root: Path | None = None,
        output_root: Path | None = None,
    ) -> None:
        self.config = config
        self.runs = run_store or RunStore()
        self.project_root = project_root or Path(__file__).resolve().parents[3]
        self.output_root = output_root or self.project_root / "reports"
        self.template_root = self.project_root / "report"

    def render(self, run_id: str, *, exec_summary: str = "", open_browser: bool = False) -> dict:
        state = self.runs.load(run_id)
        findings = []
        errors = []
        for panel_id, item in state.get("panels", {}).items():
            if item.get("finding"):
                finding = dict(item["finding"])
                image_data = self._image_data(finding.pop("imagePath", None))
                finding = self._redact_data(finding)
                finding["image_data"] = image_data
                finding["row_title"] = finding.get("row_title") or (
                    (item.get("panel") or {}).get("rowTitle") or "未分组"
                )
                finding["risk_level"] = display_risk_level(finding)
                finding["status_label"] = STATUS_LABEL.get(
                    str(finding.get("status") or ""), "未知")
                findings.append(finding)
            elif item.get("error"):
                errors.append(
                    {
                        "panelId": int(panel_id),
                        "error": item["error"],
                        "row_title": (item.get("panel") or {}).get("rowTitle") or "未分组",
                        "title": (item.get("panel") or {}).get("title") or f"panel {panel_id}",
                    }
                )
        issues = sorted(
            [item for item in findings if item.get("risk_level") in {
                "high", "medium", "low"}],
            key=lambda item: (
                RISK_ORDER.get(item.get("risk_level"), 9),
                {"P0": 0, "P1": 1, "P2": 2, "NONE": 3}.get(
                    item.get("severity"), 4),
            ),
        )
        synthesis = state.get("synthesis") or {}
        if not isinstance(synthesis, dict):
            synthesis = {}
        summary_text = self._redact(
            str(synthesis.get("summary") or "").strip())
        if not summary_text:
            summary_text = self._redact(
                exec_summary.strip()) if exec_summary else ""
        # 方舟综述仍可能是 200 字；Agent 3–5 句 exec_summary 不能再按 200 裁。
        if len(summary_text) > 800:
            summary_text = summary_text[:800].rstrip("，。;；、 ") + "…"
        classified = bool(synthesis.get("risk_calls")
                          or synthesis.get("summary"))
        high = sum(item.get("risk_level") == "high" for item in findings)
        medium = sum(item.get("risk_level") == "medium" for item in findings)
        low = sum(item.get("risk_level") == "low" for item in findings)
        total = len(state.get("panels", {}))
        done = len(findings)
        failed = len(errors)
        score, score_source = self._resolve_score(
            synthesis.get("environment_score"),
            high=high,
            medium=medium,
            low=low,
            done=done,
        )
        success_rate, success_pct = self._success_rate(done, total)
        inspected_at = self._format_when(state.get("createdAt"))
        ended_at, duration_hint = self._end_time(state)
        duration = self._duration_text(state.get("createdAt"), ended_at)
        layers = self._layers(state, findings, errors, synthesis)
        actions = self._actions(synthesis, issues)
        action_groups = self._action_groups(actions)
        layer_bars = [
            (layer["name"], layer["high"], layer["medium"], layer["low"])
            for layer in layers
        ]
        status_counts = {
            "abnormal": sum(item.get("status") == "abnormal" for item in findings),
            "warning": sum(item.get("status") == "warning" for item in findings),
            "normal": sum(item.get("status") == "normal" for item in findings),
            "no_data": sum(item.get("status") == "no_data" for item in findings),
        }
        p0 = sum(item.get("severity") == "P0" for item in findings)
        p1 = sum(item.get("severity") == "P1" for item in findings)
        p2 = sum(item.get("severity") == "P2" for item in findings)
        summary = {
            "total": total,
            "done": done,
            "failed": failed,
            "high": high,
            "medium": medium,
            "low": low,
            "score": score,
            "score_source": score_source,
            "inspected_at": inspected_at,
            "duration": duration,
            "duration_hint": duration_hint,
            "success_rate": success_rate,
            "success_pct": success_pct,
            "classified": classified,
            "p0": p0,
            "p1": p1,
            "p2": p2,
            "abnormal": status_counts["abnormal"],
            "warning": status_counts["warning"],
            "normal": status_counts["normal"],
            "no_data": status_counts["no_data"],
        }
        tone = "high" if high else "medium" if medium else "low" if low else "ok"
        env = Environment(
            loader=FileSystemLoader(self.template_root),
            autoescape=select_autoescape(("html", "xml")),
        )
        template = env.get_template("template.html")
        css = (self.template_root / "report.css").read_text(encoding="utf-8")
        dashboard = dict(state.get("dashboard") or {})
        dashboard["title"] = self._redact(
            str(dashboard.get("title") or "Grafana Dashboard"))
        html = template.render(
            css=css,
            run_id=run_id,
            dashboard=dashboard,
            from_time=state.get("from"),
            to_time=state.get("to"),
            summary=summary,
            summary_text=summary_text,
            tone=tone,
            issues=issues,
            layers=layers,
            actions=actions,
            action_groups=action_groups,
            score_svg=self._score_svg(score),
            pie_svg=self._pie_svg(high, medium, low),
            bar_svg=self._bar_svg(layer_bars),
            status_svg=self._status_svg(status_counts),
            severity_svg=self._severity_svg(p0, p1, p2),
            coverage_svg=self._coverage_svg(done, failed, total),
            model=self.config.model,
            findings_json=json.dumps(
                [
                    {key: value for key, value in item.items() if key !=
                     "image_data"}
                    for item in findings
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        uid = re.sub(r"[^a-zA-Z0-9._-]+", "-",
                     str(state.get("dashboardUid") or "dashboard"))
        target = self.output_root / uid / stamp / "report.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        result = {"path": str(target), "url": target.as_uri(),
                  "sizeBytes": target.stat().st_size}
        if open_browser:
            webbrowser.open(result["url"])
        return result

    def _layers(self, state: dict, findings: list[dict], errors: list[dict], synthesis: dict) -> list[dict]:
        briefs = {
            str(item.get("name") or ""): item
            for item in (synthesis.get("layers") or [])
            if isinstance(item, dict)
        }
        by_id = {item.get("panel_id"): item for item in findings}
        failed_by_row: dict[str, list[dict]] = {}
        order: list[str] = []
        grouped: dict[str, list[dict]] = {}
        for item in (state.get("panels") or {}).values():
            panel = item.get("panel") or {}
            row = str(
                panel.get("rowTitle")
                or (item.get("finding") or {}).get("row_title")
                or "未分组"
            )
            if row not in grouped:
                grouped[row] = []
                order.append(row)
                failed_by_row[row] = []
            finding = item.get("finding")
            if finding:
                packed = by_id.get(finding.get("panel_id"))
                if packed:
                    grouped[row].append(packed)
            elif item.get("error"):
                failed_by_row[row].append(
                    {
                        "panelId": int(panel.get("panelId") or 0),
                        "title": panel.get("title") or "",
                        "error": item.get("error"),
                    }
                )
        layers = []
        for name in order:
            members = grouped.get(name) or []
            risks = [item for item in members if item.get(
                "risk_level") in {"high", "medium", "low"}]
            failed = failed_by_row.get(name) or []
            brief = briefs.get(name) or {}
            analysis = self._redact(str(brief.get("analysis") or "").strip())
            no_risk = bool(brief.get("no_risk")) if brief else not risks
            if not analysis:
                analysis = "本层未识别到风险。" if not risks else "尚未生成本层影响分析。"
            layers.append(
                {
                    "name": name,
                    "risks": risks,
                    "failed": failed,
                    "analysis": analysis,
                    "no_risk": no_risk and not risks,
                    "high": sum(item.get("risk_level") == "high" for item in risks),
                    "medium": sum(item.get("risk_level") == "medium" for item in risks),
                    "low": sum(item.get("risk_level") == "low" for item in risks),
                    "anchor": f"layer-{self._slug(name)}",
                }
            )
        return layers

    def _actions(self, synthesis: dict, issues: list[dict]) -> list[dict]:
        raw = synthesis.get("actions") or []
        actions = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                title = self._redact(str(item.get("title") or "").strip())
                detail = self._redact(str(item.get("detail") or "").strip())
                if not title or not detail:
                    continue
                panel_ids = [int(pid) for pid in item.get(
                    "panel_ids") or [] if str(pid).lstrip("-").isdigit()]
                actions.append(
                    {"title": title, "detail": detail, "panel_ids": panel_ids})
        if not actions:
            seen: set[str] = set()
            for item in issues:
                suggestion = str(item.get("suggestion") or "").strip()
                if not suggestion or suggestion in seen:
                    continue
                seen.add(suggestion)
                actions.append(
                    {
                        "title": self._redact(str(item.get("panel_title") or "处置")),
                        "detail": self._redact(suggestion),
                        "panel_ids": [item.get("panel_id")] if item.get("panel_id") is not None else [],
                    }
                )
        return self._annotate_actions(actions[:12], issues)

    def _annotate_actions(self, actions: list[dict], issues: list[dict]) -> list[dict]:
        by_id = {item.get("panel_id"): item for item in issues}
        out = []
        for action in actions:
            ranks = []
            for pid in action.get("panel_ids") or []:
                related = by_id.get(pid)
                if related:
                    ranks.append(RISK_ORDER.get(related.get("risk_level"), 9))
            if ranks:
                best = min(ranks)
                priority = {0: "high", 1: "medium",
                            2: "low"}.get(best, "medium")
            else:
                priority = "medium"
            labels = dict((key, title) for key, title, _hint in ACTION_GROUPS)
            out.append(
                {
                    **action,
                    "priority": priority,
                    "must_fix": priority == "high",
                    "priority_label": labels.get(priority, "优先跟进"),
                }
            )
        return out

    def _action_groups(self, actions: list[dict]) -> list[dict]:
        buckets: dict[str, list[dict]] = {key: []
                                          for key, _title, _hint in ACTION_GROUPS}
        for action in actions:
            buckets.setdefault(action.get("priority")
                               or "medium", []).append(action)
        groups = []
        for key, title, hint in ACTION_GROUPS:
            items = buckets.get(key) or []
            if not items:
                continue
            groups.append(
                {
                    "key": key,
                    "title": title,
                    "hint": hint,
                    "count": len(items),
                    "actions": items,
                }
            )
        return groups

    def _end_time(self, state: dict) -> tuple[str | None, str]:
        if state.get("finishedAt"):
            return str(state["finishedAt"]), "创建至结束"
        run_id = str(state.get("runId") or "")
        last_done = None
        last_any = None
        if run_id:
            path = self.runs.root / \
                re.sub(r"[^a-zA-Z0-9._-]+", "-",
                       run_id).strip("-") / "events.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stamp = event.get("at")
                    if stamp:
                        last_any = stamp
                    if event.get("event") in {"run_done", "run_interrupted"}:
                        last_done = stamp
        if last_done:
            return str(last_done), "创建至结束"
        if last_any:
            return str(last_any), "创建至最近活动"
        if state.get("updatedAt"):
            return str(state["updatedAt"]), "创建至最近活动"
        return None, "创建至结束"

    @staticmethod
    def _as_score(value) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            score = int(value)
        except (TypeError, ValueError):
            return None
        if 0 <= score <= 100:
            return score
        return None

    @classmethod
    def _resolve_score(
        cls,
        raw,
        *,
        high: int,
        medium: int,
        low: int,
        done: int,
    ) -> tuple[int | None, str]:
        score = cls._as_score(raw)
        if score is not None:
            return score, "synthesis"
        if done <= 0:
            return None, "none"
        fallback = max(0, min(100, 100 - high * 8 - medium * 4 - low * 2))
        return fallback, "findings"

    @staticmethod
    def _success_rate(done: int, total: int) -> tuple[str, int | None]:
        if total <= 0:
            return "—", None
        pct = round(100.0 * done / total)
        return f"{pct}%", pct

    @staticmethod
    def _format_when(value: str | None) -> str:
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "—"
        return parsed.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _duration_text(start: str | None, end: str | None) -> str:
        if not start or not end:
            return "—"
        try:
            begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
            finish = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            return "—"
        seconds = int((finish - begin).total_seconds())
        if seconds < 0:
            return "—"
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours} 小时 {minutes} 分"
        if minutes:
            return f"{minutes} 分 {secs} 秒"
        return f"{secs} 秒"

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-")
        return slug or "layer"

    @classmethod
    def _score_svg(cls, score: int | None) -> Markup:
        radius = 38
        circ = 2 * math.pi * radius
        if score is None:
            dash = 0.0
            color = "#5D6B87"
            label = "—"
        else:
            dash = circ * score / 100
            color = "#35D0A4" if score >= 80 else "#F2B93B" if score >= 60 else "#F4584B"
            label = str(score)
        svg = (
            f'<svg viewBox="0 0 108 108" width="108" height="108" aria-hidden="true">'
            f'<circle cx="54" cy="54" r="{radius}" fill="none" stroke="#232E48" stroke-width="8"/>'
            f'<circle cx="54" cy="54" r="{radius}" fill="none" stroke="{color}" stroke-width="8" '
            f'stroke-linecap="round" stroke-dasharray="{dash:.2f} {circ:.2f}" transform="rotate(-90 54 54)"/>'
            f'<text x="54" y="62" text-anchor="middle" font-size="28" font-weight="650" '
            f'font-family="ui-monospace, SF Mono, Menlo, monospace" fill="#E8EEFC">'
            f"{html_lib.escape(label)}</text></svg>"
        )
        return Markup(svg)

    @classmethod
    def _pie_svg(cls, high: int, medium: int, low: int) -> Markup:
        return cls._donut_svg(
            [
                ("高", high, RISK_COLOR["high"]),
                ("中", medium, RISK_COLOR["medium"]),
                ("低", low, RISK_COLOR["low"]),
            ],
            empty="无风险",
            aria=f"风险分布 高 {high} 中 {medium} 低 {low}",
            unit="项",
        )

    @classmethod
    def _status_svg(cls, counts: dict[str, int]) -> Markup:
        return cls._donut_svg(
            [
                ("异常", counts.get("abnormal", 0), STATUS_COLOR["abnormal"]),
                ("告警", counts.get("warning", 0), STATUS_COLOR["warning"]),
                ("正常", counts.get("normal", 0), STATUS_COLOR["normal"]),
                ("无数据", counts.get("no_data", 0), STATUS_COLOR["no_data"]),
            ],
            empty="无面板",
            aria=(
                f"面板状态 异常 {counts.get('abnormal', 0)} "
                f"告警 {counts.get('warning', 0)} "
                f"正常 {counts.get('normal', 0)} "
                f"无数据 {counts.get('no_data', 0)}"
            ),
            unit="块",
        )

    @classmethod
    def _donut_svg(
        cls,
        parts: list[tuple[str, int, str]],
        *,
        empty: str,
        aria: str,
        unit: str,
    ) -> Markup:
        total = sum(count for _label, count, _color in parts)
        cx, cy, outer, inner = 80, 80, 62, 36
        font = "ui-sans-serif, system-ui, PingFang SC, sans-serif"
        if total <= 0:
            svg = (
                f'<svg viewBox="0 0 160 160" width="160" height="160" role="img" aria-label="{html_lib.escape(empty)}">'
                f'<circle cx="{cx}" cy="{cy}" r="{outer}" fill="#121A2E" stroke="#232E48" stroke-width="1.5"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{inner}" fill="#18223A"/>'
                f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" font-size="14" fill="#8190AD" '
                f'font-family="{font}">{html_lib.escape(empty)}</text></svg>'
            )
            return Markup(svg)
        paths = []
        cursor = -90.0
        for label, count, color in parts:
            if count <= 0:
                continue
            sweep = 360.0 * count / total
            paths.append(cls._donut_slice(cx, cy, inner, outer,
                         cursor, cursor + sweep, color, f"{label} {count}"))
            cursor += sweep
        svg = (
            f'<svg viewBox="0 0 160 160" width="160" height="160" role="img" aria-label="{html_lib.escape(aria)}">'
            + "".join(paths)
            + f'<circle cx="{cx}" cy="{cy}" r="{inner - 1}" fill="#121A2E"/>'
            f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="24" font-weight="700" '
            f'font-family="ui-monospace, SF Mono, Menlo, monospace" fill="#E8EEFC">{total}</text>'
            f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" font-size="12" fill="#8190AD" '
            f'font-family="{font}">{html_lib.escape(unit)}</text></svg>'
        )
        return Markup(svg)

    @classmethod
    def _bar_svg(cls, rows: list[tuple[str, int, int, int]]) -> Markup:
        if not rows:
            return Markup('<p class="muted">暂无分层数据。</p>')
        width, plot_h = 640, 168
        top, right, bottom, left = 16, 10, 78, 34
        height = top + plot_h + bottom
        plot_w = width - left - right
        max_total = max((high + medium + low)
                        for _name, high, medium, low in rows) or 1
        n = len(rows)
        slot = plot_w / n
        bar_w = min(32.0, max(7.0, slot * 0.62))
        font = "ui-sans-serif, system-ui, PingFang SC, sans-serif"
        mono = "ui-monospace, SF Mono, Menlo, monospace"
        shapes = [
            f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
            f'stroke="#232E48" stroke-width="1"/>'
        ]
        ticks = []
        for tick in (0, max(1, round(max_total / 2)), max_total):
            if tick not in ticks:
                ticks.append(tick)
        for tick in ticks:
            y = top + plot_h - plot_h * tick / max_total
            shapes.append(
                f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
                f'stroke="#232E48" stroke-width="1" stroke-dasharray="3 5"/>'
            )
            shapes.append(
                f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
                f'fill="#8190AD" font-family="{mono}">{tick}</text>'
            )
        for index, (name, high, medium, low) in enumerate(rows):
            cx = left + slot * (index + 0.5)
            x = cx - bar_w / 2
            total = high + medium + low
            shapes.append(
                f'<rect x="{x:.1f}" y="{top + plot_h - 2}" width="{bar_w:.1f}" height="2" '
                f'rx="1" fill="#232E48"/>'
            )
            cursor = top + plot_h
            for count, color in (
                (high, RISK_COLOR["high"]),
                (medium, RISK_COLOR["medium"]),
                (low, RISK_COLOR["low"]),
            ):
                if count <= 0:
                    continue
                h = plot_h * count / max_total
                cursor -= h
                shapes.append(
                    f'<rect x="{x:.1f}" y="{cursor:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                    f'fill="{color}"/>'
                )
                if h >= 14:
                    shapes.append(
                        f'<text x="{cx:.1f}" y="{cursor + h / 2 + 4:.1f}" text-anchor="middle" '
                        f'font-size="11" font-weight="700" fill="#0B1020" font-family="{mono}">{count}</text>'
                    )
            if total:
                shapes.append(
                    f'<text x="{cx:.1f}" y="{cursor - 6:.1f}" text-anchor="middle" font-size="12" '
                    f'font-weight="700" fill="#E8EEFC" font-family="{mono}">{total}</text>'
                )
            label = name if len(name) <= 8 else name[:7] + "…"
            shapes.append(
                f'<text transform="rotate(-42 {cx:.1f} {top + plot_h + 10})" x="{cx:.1f}" '
                f'y="{top + plot_h + 14}" text-anchor="end" font-size="12" fill="#C2CDE3" '
                f'font-family="{font}"><title>{html_lib.escape(name)}</title>'
                f'{html_lib.escape(label)}</text>'
            )
        svg = (
            f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMidYMax meet" '
            f'role="img" aria-label="各层风险竖形条形图">'
            + "".join(shapes)
            + "</svg>"
        )
        return Markup(svg)

    @classmethod
    def _severity_svg(cls, p0: int, p1: int, p2: int) -> Markup:
        return cls._count_bars(
            [("P0", p0, SEV_COLOR["P0"]), ("P1", p1, SEV_COLOR["P1"]),
             ("P2", p2, SEV_COLOR["P2"])],
            aria=f"严重度 P0 {p0} P1 {p1} P2 {p2}",
            empty="暂无严重度分级。",
        )

    @classmethod
    def _coverage_svg(cls, done: int, failed: int, total: int) -> Markup:
        pending = max(0, total - done - failed)
        return cls._count_bars(
            [
                ("已完成", done, "#35D0A4"),
                ("失败", failed, RISK_COLOR["high"]),
                ("未完成", pending, "#5D6B87"),
            ],
            aria=f"巡检覆盖 已完成 {done} 失败 {failed} 未完成 {pending}",
            empty="没有检查项。",
        )

    @classmethod
    def _count_bars(cls, rows: list[tuple[str, int, str]], *, aria: str, empty: str) -> Markup:
        if not rows or all(count <= 0 for _label, count, _color in rows):
            return Markup(f'<p class="muted">{html_lib.escape(empty)}</p>')
        width = 480
        row_h = 58
        left = 92
        right_pad = 52
        chart_w = width - left - right_pad
        height = row_h * len(rows)
        peak = max(count for _label, count, _color in rows) or 1
        font = "ui-sans-serif, system-ui, PingFang SC, sans-serif"
        shapes = []
        for index, (label, count, color) in enumerate(rows):
            y = index * row_h
            w = 0 if count <= 0 else max(10, chart_w * count / peak)
            shapes.append(
                f'<text x="0" y="{y + 34}" font-size="20" font-weight="700" fill="#E8EEFC" '
                f'font-family="{font}">{html_lib.escape(label)}</text>'
            )
            shapes.append(
                f'<rect x="{left}" y="{y + 16}" width="{chart_w}" height="28" rx="5" fill="#1B2440"/>'
            )
            if count > 0:
                shapes.append(
                    f'<rect x="{left}" y="{y + 16}" width="{w:.1f}" height="28" rx="5" fill="{color}"/>'
                )
            shapes.append(
                f'<text x="{left + chart_w + 10}" y="{y + 36}" font-size="20" font-weight="700" fill="#E8EEFC" '
                f'font-family="ui-monospace, SF Mono, Menlo, monospace">{count}</text>'
            )
        svg = (
            f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" '
            f'aria-label="{html_lib.escape(aria)}">'
            + "".join(shapes)
            + "</svg>"
        )
        return Markup(svg)

    @staticmethod
    def _donut_slice(cx: float, cy: float, inner: float, outer: float, start: float, end: float, color: str, title: str) -> str:
        if end - start >= 359.99:
            return (
                f'<circle cx="{cx}" cy="{cy}" r="{(inner + outer) / 2:.2f}" fill="none" '
                f'stroke="{color}" stroke-width="{outer - inner:.2f}"><title>{html_lib.escape(title)}</title></circle>'
            )

        def point(radius: float, angle: float) -> tuple[float, float]:
            rad = math.radians(angle)
            return cx + radius * math.cos(rad), cy + radius * math.sin(rad)

        large = 1 if (end - start) > 180 else 0
        osx, osy = point(outer, start)
        oex, oey = point(outer, end)
        iex, iey = point(inner, end)
        isx, isy = point(inner, start)
        return (
            f'<path fill="{color}" d="M {osx:.2f} {osy:.2f} A {outer:.2f} {outer:.2f} 0 {large} 1 {oex:.2f} {oey:.2f} '
            f'L {iex:.2f} {iey:.2f} A {inner:.2f} {inner:.2f} 0 {large} 0 {isx:.2f} {isy:.2f} Z">'
            f"<title>{html_lib.escape(title)}</title></path>"
        )

    @staticmethod
    def _image_data(path: str | None) -> str | None:
        if not path:
            return None
        image = Path(path)
        if not image.exists():
            return None
        return "data:image/jpeg;base64," + base64.b64encode(image.read_bytes()).decode()

    @classmethod
    def _redact_data(cls, value):
        if isinstance(value, dict):
            return {key: cls._redact_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_data(item) for item in value]
        if isinstance(value, str):
            return cls._redact(value)
        return value

    @staticmethod
    def _redact(value: str) -> str:
        value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "x.x.x.x", value)
        return re.sub(r"https?://([^/?#]+)", "https://***", value)
