"""火山方舟视觉分析客户端与 Finding Schema 校验。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any

import httpx
from pydantic import ValidationError

from .config import lens_home
from .errors import LensError
from .models import Finding, ReportSynthesis


def usage_path():
    return lens_home() / "usage.json"


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def consume_daily_slot(limit: int) -> None:
    """在第一次 await 之前同步占槽，避免 gather 打穿日限额。"""
    if limit <= 0:
        return
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    today = _utc_today()
    data = {"date": today, "count": 0}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("date") == today:
                data = {"date": today, "count": int(loaded.get("count") or 0)}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if data["count"] >= limit:
        raise LensError("ARK_ERROR", f"今日方舟调用已达上限 {limit}")
    data["count"] += 1
    temporary = path.with_name(".usage.json.tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


ARK_HTTP_TIMEOUT = httpx.Timeout(120.0)


class ArkClient:
    """唯一的模型出网通道；负责 prompt、重试和 Finding 强校验。"""

    def __init__(self, config, *, client: httpx.AsyncClient | None = None):
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=ARK_HTTP_TIMEOUT)
        package = files("grafana_lens")
        self.system_prompt = package.joinpath(
            "prompts/inspect_v1.txt").read_text(encoding="utf-8")
        self.batch_prompt = package.joinpath(
            "prompts/inspect_batch_v1.txt").read_text(encoding="utf-8")
        self.synthesize_prompt = package.joinpath(
            "prompts/synthesize_v1.txt").read_text(encoding="utf-8")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def vision_inspect(self, image_b64: str, panel_meta: dict) -> Finding:
        model = self.config.endpoint_id or self.config.model
        if not model or (self._owns_client and not self.config.api_key):
            raise LensError("ARK_ERROR", "缺少 api_key 或 model/endpoint_id 配置")
        consume_daily_slot(
            int(getattr(self.config, "daily_call_limit", 0) or 0))

        validation_feedback = ""
        last_schema_error: Exception | None = None
        for attempt in range(3):
            request_body = self._request_body(
                model=model,
                image_b64=image_b64,
                panel_meta=panel_meta,
                validation_feedback=validation_feedback,
            )
            try:
                response = await self.client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json=request_body,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                retryable = error.response.status_code == 429 or error.response.status_code >= 500
                if retryable and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise LensError(
                    "ARK_ERROR", f"HTTP {error.response.status_code}") from error
            except httpx.HTTPError as error:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise LensError("ARK_ERROR", type(error).__name__) from error

            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                payload = json.loads(content)
                finding = Finding.validate_vlm_payload(payload)
                usage = body.get("usage") or {}
                finding.tokens_used = int(usage.get("total_tokens") or 0)
                return finding
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError, ValueError) as error:
                last_schema_error = error
                validation_feedback = self._schema_feedback(error)

        return self._degraded_finding(panel_meta, last_schema_error)

    async def vision_inspect_batch(
        self,
        items: list[tuple[str, dict]],
    ) -> list[Finding]:
        """一次请求携带多张截图，只占一个日限额槽。"""
        if not items:
            return []
        if len(items) == 1:
            return [await self.vision_inspect(items[0][0], items[0][1])]
        model = self.config.endpoint_id or self.config.model
        if not model or (self._owns_client and not self.config.api_key):
            raise LensError("ARK_ERROR", "缺少 api_key 或 model/endpoint_id 配置")
        consume_daily_slot(
            int(getattr(self.config, "daily_call_limit", 0) or 0))

        validation_feedback = ""
        last_schema_error: Exception | None = None
        timeout = ARK_HTTP_TIMEOUT
        for attempt in range(3):
            request_body = self._batch_request_body(
                model=model,
                items=items,
                validation_feedback=validation_feedback,
            )
            try:
                response = await self.client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json=request_body,
                    timeout=timeout,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                retryable = error.response.status_code == 429 or error.response.status_code >= 500
                if retryable and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise LensError(
                    "ARK_ERROR", f"HTTP {error.response.status_code}") from error
            except httpx.HTTPError as error:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise LensError("ARK_ERROR", type(error).__name__) from error

            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                payload = json.loads(content)
                findings = self._parse_batch_findings(payload, items)
                usage = body.get("usage") or {}
                tokens = int(usage.get("total_tokens") or 0)
                share = tokens // max(1, len(findings))
                remainder = tokens - share * len(findings)
                for index, finding in enumerate(findings):
                    finding.tokens_used = share + \
                        (remainder if index == 0 else 0)
                return findings
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError, ValueError) as error:
                last_schema_error = error
                validation_feedback = self._schema_feedback(error)

        return [
            self._degraded_finding(meta, last_schema_error)
            for _image, meta in items
        ]

    async def synthesize(self, briefing: dict) -> ReportSynthesis:
        model = self.config.endpoint_id or self.config.model
        if not model or (self._owns_client and not self.config.api_key):
            raise LensError("ARK_ERROR", "缺少 api_key 或 model/endpoint_id 配置")
        consume_daily_slot(
            int(getattr(self.config, "daily_call_limit", 0) or 0))

        schema_text = json.dumps(
            ReportSynthesis.model_json_schema(), ensure_ascii=False)
        validation_feedback = ""
        last_error: Exception | None = None
        for attempt in range(3):
            user_text = (
                "巡检 findings：\n"
                + json.dumps(briefing, ensure_ascii=False)
                + "\n必须严格符合以下 JSON Schema：\n"
                + schema_text
            )
            if validation_feedback:
                user_text += "\n上一次返回不合规，请修正：" + validation_feedback
            request_body = {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": self.synthesize_prompt},
                    {"role": "user", "content": user_text},
                ],
            }
            try:
                response = await self.client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json=request_body,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                retryable = error.response.status_code == 429 or error.response.status_code >= 500
                if retryable and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise LensError(
                    "ARK_ERROR", f"HTTP {error.response.status_code}") from error
            except httpx.HTTPError as error:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise LensError("ARK_ERROR", type(error).__name__) from error

            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                payload = json.loads(content)
                return ReportSynthesis.model_validate(payload)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError, ValueError) as error:
                last_error = error
                validation_feedback = self._schema_feedback(error)

        raise LensError("ARK_ERROR", f"综述结构不合规：{type(last_error).__name__}")

    def _request_body(
        self,
        *,
        model: str,
        image_b64: str,
        panel_meta: dict,
        validation_feedback: str,
    ) -> dict[str, Any]:
        schema_text = json.dumps(
            Finding.model_json_schema(), ensure_ascii=False)
        user_text = (
            "面板元信息：\n"
            + json.dumps(panel_meta, ensure_ascii=False)
            + "\n必须严格符合以下 JSON Schema：\n"
            + schema_text
        )
        if validation_feedback:
            user_text += "\n上一次返回不合规，请修正：" + validation_feedback
        return {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                },
            ],
        }

    def _batch_request_body(
        self,
        *,
        model: str,
        items: list[tuple[str, dict]],
        validation_feedback: str,
    ) -> dict[str, Any]:
        schema_text = json.dumps(
            Finding.model_json_schema(), ensure_ascii=False)
        panels = []
        content: list[dict[str, Any]] = []
        for index, (_image_b64, panel_meta) in enumerate(items, start=1):
            panels.append({"index": index, "panel": panel_meta})
        user_text = (
            f"共 {len(items)} 张面板截图，按顺序编号 1–{len(items)}。\n"
            "面板元信息：\n"
            + json.dumps(panels, ensure_ascii=False)
            + f"\n返回 {{\"findings\":[...]}} ，findings 长度必须为 {len(items)}，"
            "第 i 项对应第 i 张图，且符合以下 Finding JSON Schema：\n"
            + schema_text
        )
        if validation_feedback:
            user_text += "\n上一次返回不合规，请修正：" + validation_feedback
        content.append({"type": "text", "text": user_text})
        for image_b64, _panel_meta in items:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                }
            )
        return {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.batch_prompt},
                {"role": "user", "content": content},
            ],
        }

    @classmethod
    def _extract_finding_rows(cls, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            rows = payload.get("findings")
            if isinstance(rows, list):
                return rows
            if "panel_id" in payload:
                return [payload]
        raise ValueError("batch payload missing findings")

    def _parse_batch_findings(
        self,
        payload: Any,
        items: list[tuple[str, dict]],
    ) -> list[Finding]:
        rows = self._extract_finding_rows(payload)
        by_id: dict[int, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_id = row.get("panel_id")
            try:
                by_id.setdefault(int(raw_id), row)
            except (TypeError, ValueError):
                continue
        findings: list[Finding] = []
        problems: list[str] = []
        for index, (_image, panel_meta) in enumerate(items):
            try:
                panel_id = int(panel_meta.get("panelId")
                               or panel_meta.get("panel_id") or 0)
            except (TypeError, ValueError):
                panel_id = 0
            row = by_id.get(panel_id)
            if row is None and index < len(rows) and isinstance(rows[index], dict):
                row = rows[index]
            if not isinstance(row, dict):
                problems.append(f"panel {panel_id} missing")
                continue
            try:
                findings.append(Finding.validate_vlm_payload(row))
            except (ValidationError, ValueError, TypeError) as error:
                problems.append(
                    f"panel {panel_id}: {self._schema_feedback(error)}")
        if problems or len(findings) != len(items):
            raise ValueError("; ".join(problems) or "batch length mismatch")
        return findings

    @staticmethod
    def _schema_feedback(error: Exception) -> str:
        if isinstance(error, ValidationError):
            fields = sorted({".".join(str(part)
                            for part in item["loc"]) for item in error.errors()})
            return "字段校验失败：" + ", ".join(fields[:12])
        return type(error).__name__

    @staticmethod
    def _degraded_finding(panel_meta: dict, error: Exception | None) -> Finding:
        datasource = str(panel_meta.get("datasourceType", "other"))
        if datasource not in {"prometheus", "elasticsearch", "other"}:
            datasource = "other"
        detail = "方舟返回结构不合规，需人工确认"
        if error is not None:
            detail += f"（{type(error).__name__}）"
        return Finding(
            panel_id=int(panel_meta.get("panelId", 0)),
            panel_title=str(panel_meta.get("title", "")),
            panel_type=str(panel_meta.get("type", "other")),
            datasource_type=datasource,
            status="no_data",
            observations=[
                {"kind": "no_data", "detail": detail, "confidence": 0.0}],
            printed_values=[],
            severity="NONE",
            confidence=0.0,
            needs_human_confirm=True,
            conflict="schema_invalid",
            analysis_status="schema_invalid",
        )
