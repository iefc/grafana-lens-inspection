"""CLI 四子命令冒烟（01 §7.3）。"""

import pytest

from grafana_lens.cli import main


def test_no_args_prints_usage(capsys):
    rc = main([])
    assert rc != 0
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    for sub in ("serve", "pair", "init-config", "doctor"):
        assert sub in out


def test_init_config_creates_file(tmp_path, monkeypatch, capsys):
    target = tmp_path / "config"
    monkeypatch.setenv("GRAFANA_LENS_HOME", str(tmp_path))
    rc = main(["init-config"])
    assert rc == 0
    assert target.exists()
    assert "已生成" in capsys.readouterr().out


def test_init_config_refuses_overwrite(tmp_path, monkeypatch, capsys):
    target = tmp_path / "config"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GRAFANA_LENS_HOME", str(tmp_path))
    rc = main(["init-config"])
    assert rc != 0
    assert "已存在" in capsys.readouterr().err


def test_doctor_reports_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRAFANA_LENS_HOME", str(tmp_path))
    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "配置文件" in out
    assert "ARK API Key" in out
    assert "9527" in out
    assert "token" in out.lower()


def test_pair_opens_temporary_pairing_window(tmp_path, monkeypatch, capsys):
    import grafana_lens.ws_server as ws_server

    opened: dict[str, object] = {}

    async def fake_run_pairing_server(*, token: str, host: str, port: int) -> bool:
        opened.update(token=token, host=host, port=port)
        return True

    monkeypatch.setenv("GRAFANA_LENS_HOME", str(tmp_path))
    monkeypatch.setattr(ws_server, "run_pairing_server", fake_run_pairing_server)

    rc = main(["pair"])

    assert rc == 0
    assert opened["host"] == "127.0.0.1"
    assert opened["port"] == 9527
    assert len(str(opened["token"])) == 64
    assert "60 秒" in capsys.readouterr().out
