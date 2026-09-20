"""config 加载与 init-config 模板（01 §7.4）。"""

import os
import stat

import pytest

from grafana_lens.config import (
    CONFIG_TEMPLATE,
    DEFAULT_EXTENSION_ORIGIN,
    LensConfig,
    ensure_token,
    write_config_template,
)


def test_default_values():
    cfg = LensConfig.load_from_file(None)
    assert cfg.ws_port == 9527
    assert cfg.analyze_concurrency == 2
    assert cfg.max_panels_per_run == 40
    assert cfg.mcp_call_budget_seconds == 20
    assert cfg.max_panels_per_call == 0
    assert cfg.vision_batch_size == 8
    assert cfg.model == "seed-2.1-pro-0915"
    assert cfg.extension_origin == DEFAULT_EXTENSION_ORIGIN
    assert cfg.api_key is None


def test_load_ini_overrides(tmp_path):
    ini = tmp_path / "config"
    ini.write_text(
        "[ark]\n"
        "api_key = sk-test\n"
        "endpoint_id = ep-1\n"
        "[server]\n"
        "ws_port = 9999\n"
        "[allowed]\n"
        f"extension_origin = {DEFAULT_EXTENSION_ORIGIN}\n",
        encoding="utf-8",
    )
    cfg = LensConfig.load_from_file(ini)
    assert cfg.api_key == "sk-test"
    assert cfg.endpoint_id == "ep-1"
    assert cfg.ws_port == 9999


def test_reject_non_fixed_origin(tmp_path):
    ini = tmp_path / "config"
    ini.write_text(
        "[allowed]\nextension_origin = https://evil.example\n", encoding="utf-8")
    with pytest.raises(ValueError):
        LensConfig.load_from_file(ini)


def test_write_template_creates_file_600(tmp_path):
    path = tmp_path / "config"
    write_config_template(path)
    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    text = path.read_text(encoding="utf-8")
    assert text.strip() == CONFIG_TEMPLATE.strip()
    assert "seed-2.1-pro-0915" in text
    assert DEFAULT_EXTENSION_ORIGIN in text


def test_write_template_refuses_overwrite(tmp_path):
    path = tmp_path / "config"
    path.write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_config_template(path)


def test_ensure_token_generates_600_hex(tmp_path):
    token_path = tmp_path / "token"
    token = ensure_token(token_path)
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600


def test_ensure_token_stable(tmp_path):
    token_path = tmp_path / "token"
    first = ensure_token(token_path)
    second = ensure_token(token_path)
    assert first == second
