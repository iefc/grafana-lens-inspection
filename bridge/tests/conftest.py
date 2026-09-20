import pytest


@pytest.fixture(autouse=True)
def _isolate_grafana_lens_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAFANA_LENS_HOME", str(
        tmp_path / "grafana-lens-home"))
