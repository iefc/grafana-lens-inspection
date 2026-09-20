"""run 落盘、截图 artifact、失败重试与 resume 跳过已完成面板。"""

import json

from grafana_lens.resume import RunStore


def _dashboard():
    return {"uid": "k8s", "title": "K8s"}


def _panels():
    return [
        {"panelId": 12, "title": "CPU"},
        {"panelId": 13, "title": "Mem"},
    ]


def test_create_persists_state_and_events(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    loaded = store.load(state["runId"])
    assert loaded["status"] == "running"
    assert loaded["panels"]["12"]["state"] == "pending"
    events = (tmp_path / state["runId"] /
              "events.jsonl").read_text(encoding="utf-8")
    assert "run_started" in events


def test_panel_done_writes_jpeg_artifact_and_strips_base64(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.panel_started(state, 12)
    store.panel_done(
        state,
        12,
        {"panel_id": 12, "imageBase64": "aGVsbG8=", "status": "normal"},
    )
    artifact = tmp_path / state["runId"] / "artifacts" / "panel-12.jpg"
    assert artifact.read_bytes() == b"hello"
    saved = json.loads(
        (tmp_path / state["runId"] / "state.json").read_text(encoding="utf-8"))
    finding = saved["panels"]["12"]["finding"]
    assert "imageBase64" not in finding
    assert finding["imagePath"] == str(artifact)
    assert saved["panels"]["12"]["state"] == "done"


def test_panel_captured_writes_jpeg_and_resets_attempts(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.panel_started(state, 12)
    assert state["panels"]["12"]["attempts"] == 1
    store.panel_captured(
        state,
        12,
        {
            "imageBase64": "aGVsbG8=",
            "mimeType": "image/jpeg",
            "width": 10,
            "height": 10,
            "sizeBytes": 5,
            "panelMeta": {"panelId": 12},
        },
    )
    loaded = store.load(state["runId"])
    item = loaded["panels"]["12"]
    artifact = tmp_path / state["runId"] / "artifacts" / "panel-12.jpg"
    assert item["state"] == "captured"
    assert item["attempts"] == 0
    assert item["imagePath"] == str(artifact)
    assert artifact.read_bytes() == b"hello"
    assert store.panel_unfinished(item)
    events = (tmp_path / state["runId"] /
              "events.jsonl").read_text(encoding="utf-8")
    assert "panel_captured" in events


def test_failed_attempt_is_recorded_for_retry(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.panel_started(state, 13)
    store.panel_failed(
        state, 13, {"code": "RENDER_TIMEOUT", "message": "timeout"})
    loaded = store.load(state["runId"])
    assert loaded["panels"]["13"]["state"] == "failed"
    assert loaded["panels"]["13"]["attempts"] == 1
    store.panel_started(loaded, 13)
    assert loaded["panels"]["13"]["attempts"] == 2


def test_finish_writes_finished_at(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.finish(state, "done")
    loaded = store.load(state["runId"])
    assert loaded["status"] == "done"
    assert loaded["finishedAt"]


def test_latest_unfinished_prefers_most_progress(tmp_path):
    store = RunStore(tmp_path)
    older = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.panel_started(older, 12)
    store.panel_done(older, 12, {"panel_id": 12, "status": "normal"})
    newer = store.create(_dashboard(), _panels(), "now-1h", "now")
    picked = store.latest_unfinished("k8s")
    assert picked["runId"] == older["runId"]
    store.finish(store.load(older["runId"]), "done")
    picked = store.latest_unfinished("k8s")
    assert picked["runId"] == newer["runId"]
    assert store.latest_unfinished("other") is None


def test_latest_active_returns_running_run_without_uid(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    assert store.latest_active()["runId"] == state["runId"]
    store.finish(state, "done")
    assert store.latest_active() is None


def test_latest_for_dashboard_returns_recent_done_run(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.finish(state, "done")
    picked = store.latest_for_dashboard("k8s")
    assert picked["runId"] == state["runId"]
    assert store.latest_unfinished("k8s") is None


def test_latest_for_dashboard_ignores_stale_done_run(tmp_path):
    store = RunStore(tmp_path)
    state = store.create(_dashboard(), _panels(), "now-1h", "now")
    store.finish(state, "done")
    path = tmp_path / state["runId"] / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updatedAt"] = "2000-01-01T00:00:00Z"
    data["finishedAt"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.latest_for_dashboard("k8s") is None
