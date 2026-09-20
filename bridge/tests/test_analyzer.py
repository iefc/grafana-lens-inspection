import json

import httpx
import pytest

from grafana_lens.analyzer import ARK_HTTP_TIMEOUT, ArkClient
from grafana_lens.config import LensConfig


def test_ark_http_timeout_is_120_seconds():
    assert ARK_HTTP_TIMEOUT.read == 120.0
    assert ARK_HTTP_TIMEOUT.connect == 120.0


@pytest.fixture
def config():
    return LensConfig(None, "ep-test", "seed-2.1-pro-0915", "https://ark.example/api/v3", "127.0.0.1", 9527, 40, 300, 2, 20, 0, "chrome-extension://ifpbbimoemkbibijemmkagpngaemojha")


def response(payload):
    return {"choices": [{"message": {"content": payload}}]}


async def test_vision_inspect_retries_invalid_schema_then_returns_finding(config):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        payload = "{}" if calls == 1 else '{"panel_id":1,"panel_title":"CPU","panel_type":"stat","datasource_type":"prometheus","status":"normal","observations":[{"kind":"稳定","detail":"稳定","confidence":0.9}],"printed_values":[],"severity":"NONE"}'
        return httpx.Response(200, json=response(payload))

    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    finding = await client.vision_inspect("abc", {"panelId": 1})

    assert finding.panel_id == 1
    assert calls == 2


async def test_vision_inspect_degrades_after_schema_retries(config):
    async def handler(request):
        return httpx.Response(200, json=response("{}"))

    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    finding = await client.vision_inspect("abc", {"panelId": 9, "title": "slow"})

    assert finding.status == "no_data"
    assert finding.needs_human_confirm is True
    assert finding.panel_id == 9


async def test_vision_inspect_maps_http_failure_to_ark_error(config):
    async def handler(request):
        return httpx.Response(500, json={"error": "down"})

    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    from grafana_lens.errors import LensError
    with pytest.raises(LensError) as error:
        await client.vision_inspect("abc", {"panelId": 1})
    assert error.value.code == "ARK_ERROR"


async def test_vision_inspect_stops_at_daily_call_limit(config):
    from grafana_lens.errors import LensError

    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=response(
                '{"panel_id":1,"panel_title":"CPU","panel_type":"stat","datasource_type":"prometheus",'
                '"status":"normal","observations":[{"kind":"稳定","detail":"稳定","confidence":0.9}],'
                '"printed_values":[],"severity":"NONE"}'
            ),
        )

    limited = config.__class__(
        None,
        "ep-test",
        config.model,
        config.base_url,
        config.ws_host,
        config.ws_port,
        40,
        1,
        2,
        20,
        0,
        config.extension_origin,
    )
    client = ArkClient(limited, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    await client.vision_inspect("abc", {"panelId": 1})
    with pytest.raises(LensError) as error:
        await client.vision_inspect("abc", {"panelId": 2})
    assert error.value.code == "ARK_ERROR"
    assert "上限" in error.value.message
    assert calls == 1


async def test_vision_inspect_uses_model_when_endpoint_is_empty(config):
    config = config.__class__(None, None, "model-id", config.base_url,
                              config.ws_host, config.ws_port, 40, 300, 2, 20, 0, config.extension_origin)
    seen = {}

    async def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=response('{"panel_id":1,"panel_title":"x","panel_type":"stat","datasource_type":"other","status":"normal","observations":[],"printed_values":[],"severity":"NONE"}'))
    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    await client.vision_inspect("abc", {"panelId": 1})
    assert seen["model"] == "model-id"


async def test_synthesize_returns_report_synthesis(config):
    payload = (
        '{"environment_score":70,"summary":"整体容量偏紧。",'
        '"risk_calls":[{"panel_id":1,"risk_level":"high"}],'
        '"layers":[{"name":"节点资源","analysis":"CPU 偏高。","no_risk":false}],'
        '"actions":[{"title":"扩容","detail":"提高 limit","panel_ids":[1]}]}'
    )

    async def handler(request):
        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(200, json=response(payload))

    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    synthesis = await client.synthesize({"findings": [{"panel_id": 1, "status": "warning"}]})
    assert synthesis.environment_score == 70
    assert synthesis.risk_calls[0].risk_level == "high"


async def test_synthesize_maps_http_failure_to_ark_error(config):
    async def handler(request):
        return httpx.Response(500, json={"error": "down"})

    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    from grafana_lens.errors import LensError
    with pytest.raises(LensError) as error:
        await client.synthesize({"findings": []})
    assert error.value.code == "ARK_ERROR"


def _normal_finding(panel_id: int, title: str) -> dict:
    return {
        "panel_id": panel_id,
        "panel_title": title,
        "panel_type": "stat",
        "datasource_type": "prometheus",
        "status": "normal",
        "observations": [{"kind": "稳定", "detail": "稳定", "confidence": 0.9}],
        "printed_values": [],
        "severity": "NONE",
    }


async def test_vision_inspect_batch_sends_multiple_images_one_http_call(config):
    seen = {}

    async def handler(request):
        seen.update(json.loads(request.content))
        payload = {"findings": [_normal_finding(
            1, "CPU"), _normal_finding(2, "MEM")]}
        return httpx.Response(200, json=response(json.dumps(payload)))

    client = ArkClient(config, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    findings = await client.vision_inspect_batch([
        ("img-a", {"panelId": 1, "title": "CPU"}),
        ("img-b", {"panelId": 2, "title": "MEM"}),
    ])
    assert [item.panel_id for item in findings] == [1, 2]
    user = seen["messages"][1]["content"]
    images = [part for part in user if part.get("type") == "image_url"]
    assert len(images) == 2
    assert "img-a" in images[0]["image_url"]["url"]
    assert "img-b" in images[1]["image_url"]["url"]


async def test_vision_inspect_batch_counts_one_daily_slot(config):
    from grafana_lens.errors import LensError

    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        payload = {"findings": [_normal_finding(
            1, "A"), _normal_finding(2, "B")]}
        return httpx.Response(200, json=response(json.dumps(payload)))

    limited = config.__class__(
        None,
        "ep-test",
        config.model,
        config.base_url,
        config.ws_host,
        config.ws_port,
        40,
        1,
        2,
        20,
        0,
        config.extension_origin,
    )
    client = ArkClient(limited, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    await client.vision_inspect_batch([
        ("a", {"panelId": 1}),
        ("b", {"panelId": 2}),
    ])
    with pytest.raises(LensError) as error:
        await client.vision_inspect("c", {"panelId": 3})
    assert error.value.code == "ARK_ERROR"
    assert "上限" in error.value.message
    assert calls == 1
