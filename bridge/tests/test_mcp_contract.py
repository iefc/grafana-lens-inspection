"""统一错误模型契约（01 文档 §4.2）。"""

import pytest

from grafana_lens.errors import ERROR_CATALOG, LensError, lens_error_payload


ALL_CODES = [
    "PANEL_NOT_FOUND",
    "RENDER_TIMEOUT",
    "NOT_ON_DASHBOARD",
    "TAB_NOT_ACTIVE",
    "AUTH_REQUIRED",
    "CAPTURE_FAILED",
    "BRIDGE_OFFLINE",
    "PLUGIN_OFFLINE",
    "EVIDENCE_UNSUPPORTED",
    "SCHEMA_INVALID_RETRIED",
    "ARK_ERROR",
    "RUN_INTERRUPTED",
]


class TestErrorCatalog:
    def test_all_twelve_codes_present(self):
        assert set(ERROR_CATALOG) == set(ALL_CODES)
        assert len(ERROR_CATALOG) == 12

    @pytest.mark.parametrize("code", ALL_CODES)
    def test_each_code_has_message_and_next_action(self, code):
        entry = ERROR_CATALOG[code]
        assert entry.message
        assert entry.nextAction  # 驼峰：进 JSON 与 MCP content 保持一致

    def test_lens_error_carries_code(self):
        err = LensError("PLUGIN_OFFLINE")
        assert err.code == "PLUGIN_OFFLINE"
        assert err.message == ERROR_CATALOG["PLUGIN_OFFLINE"].message

    def test_lens_error_accepts_detail(self):
        err = LensError("RENDER_TIMEOUT", detail="面板 12 渲染超时")
        assert "面板 12" in err.message

    def test_payload_shape_matches_mcp_error_envelope(self):
        payload = lens_error_payload(LensError("PANEL_NOT_FOUND", detail="面板 99"))
        assert payload["isError"] is True
        assert payload["content"][0]["type"] == "text"
        import json

        body = json.loads(payload["content"][0]["text"])
        assert set(body) == {"code", "message", "nextAction"}
        assert body["code"] == "PANEL_NOT_FOUND"
        assert body["nextAction"]
