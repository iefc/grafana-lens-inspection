"""~/.grafana-lens 配置读写（01 §7.4）。"""

from __future__ import annotations

import configparser
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXTENSION_ORIGIN = (
    "chrome-extension://ifpbbimoemkbibijemmkagpngaemojha"
)
DEFAULT_MODEL = "seed-2.1-pro-0915"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

CONFIG_TEMPLATE = f"""[ark]
api_key =
endpoint_id =
model = {DEFAULT_MODEL}
base_url = {DEFAULT_BASE_URL}

[server]
ws_host = 127.0.0.1
ws_port = 9527
max_panels_per_run = 40
daily_call_limit = 300
analyze_concurrency = 2
mcp_call_budget_seconds = 20
max_panels_per_call = 0
vision_batch_size = 8

[allowed]
extension_origin = {DEFAULT_EXTENSION_ORIGIN}
"""


def lens_home() -> Path:
    """配置目录；GRAFANA_LENS_HOME 仅供测试覆盖。"""
    override = os.environ.get("GRAFANA_LENS_HOME")
    return Path(override) if override else Path.home() / ".grafana-lens"


def config_path() -> Path:
    return lens_home() / "config"


def token_path() -> Path:
    return lens_home() / "token"


@dataclass(frozen=True)
class LensConfig:
    api_key: str | None
    endpoint_id: str | None
    model: str
    base_url: str
    ws_host: str
    ws_port: int
    max_panels_per_run: int
    daily_call_limit: int
    analyze_concurrency: int
    mcp_call_budget_seconds: int
    max_panels_per_call: int
    extension_origin: str
    vision_batch_size: int = 8

    @classmethod
    def load_from_file(cls, path: str | os.PathLike | None) -> "LensConfig":
        cp = configparser.ConfigParser()
        if path is not None:
            cp.read(path, encoding="utf-8")

        def g(section: str, key: str, default: str | None = None) -> str | None:
            if cp.has_option(section, key):
                value = cp.get(section, key).strip()
                return value or None
            return default

        origin = g("allowed", "extension_origin", DEFAULT_EXTENSION_ORIGIN)
        if origin != DEFAULT_EXTENSION_ORIGIN:
            raise ValueError(
                f"extension_origin 必须是固定扩展 ID {DEFAULT_EXTENSION_ORIGIN}，"
                f"当前为 {origin}"
            )

        host = g("server", "ws_host", "127.0.0.1") or "127.0.0.1"
        if host != "127.0.0.1":
            raise ValueError("ws_host 必须固定为 127.0.0.1，禁止暴露到非回环网卡")

        return cls(
            api_key=g("ark", "api_key"),
            endpoint_id=g("ark", "endpoint_id"),
            model=g("ark", "model", DEFAULT_MODEL) or DEFAULT_MODEL,
            base_url=g("ark", "base_url",
                       DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
            ws_host=host,
            ws_port=int(g("server", "ws_port", "9527")),
            max_panels_per_run=int(g("server", "max_panels_per_run", "40")),
            daily_call_limit=int(g("server", "daily_call_limit", "300")),
            analyze_concurrency=int(g("server", "analyze_concurrency", "2")),
            mcp_call_budget_seconds=int(
                g("server", "mcp_call_budget_seconds", "20")),
            max_panels_per_call=int(g("server", "max_panels_per_call", "0")),
            extension_origin=origin,
            vision_batch_size=max(
                1, min(8, int(g("server", "vision_batch_size", "8") or 8))),
        )

    @classmethod
    def load(cls) -> "LensConfig":
        path = config_path()
        return cls.load_from_file(path if path.exists() else None)


def write_config_template(path: str | os.PathLike) -> None:
    p = Path(path)
    if p.exists():
        raise FileExistsError(f"配置文件已存在：{p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    os.chmod(p, 0o600)


def ensure_token(path: str | os.PathLike | None = None) -> str:
    """读取已有 token，否则生成 32 字节随机 hex 并以 600 落盘。"""
    p = Path(path) if path is not None else token_path()
    if p.exists():
        os.chmod(p, 0o600)
        token = p.read_text(encoding="utf-8").strip()
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            raise ValueError(f"token 文件内容无效：{p}")
        return token
    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    p.write_text(token, encoding="utf-8")
    os.chmod(p, 0o600)
    return token
