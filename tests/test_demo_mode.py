import json

from fastapi.testclient import TestClient

from backend.bl_haos.demo import DEMO_SCENARIOS, DemoRuntime
from backend.bl_haos.main import app


def test_demo_scenarios_are_named_and_byte_stable():
    assert {
        "healthy",
        "pairing_failure",
        "sink_missing",
        "reconnect_exhausted",
        "native_integration_unavailable",
        "restart_degraded",
    } <= set(DEMO_SCENARIOS)

    first = DemoRuntime("sink_missing").export()
    second = DemoRuntime("sink_missing").export()
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(second, sort_keys=True, separators=(",", ":"))
    assert first["diagnostics"]["last_failure"]["classification"] == "sink_missing"
    assert first["events"] == second["events"]


def test_demo_provider_has_no_live_dependency_initializers():
    runtime = DemoRuntime("healthy")
    assert runtime.live_initialization_calls == []
    assert runtime.get_adapters()[0].interface == "hci0"
    assert runtime.get_devices()[0].address == "aa:bb:cc:11:22:33"
    assert runtime.diagnostics["demo_mode"] is True


def test_unknown_demo_scenario_is_rejected():
    try:
        DemoRuntime("unknown")
    except ValueError as error:
        assert str(error) == "Unknown demo scenario"
    else:
        raise AssertionError("unknown demo scenario must be rejected")


def test_demo_mode_injects_before_live_manager_and_serves_production_routes(monkeypatch):
    monkeypatch.setenv("BLHAOS_DEMO_MODE", "true")
    monkeypatch.setenv("BLHAOS_DEMO_SCENARIO", "reconnect_exhausted")
    with TestClient(app) as client:
        assert app.state.demo_runtime.live_initialization_calls == []
        response = client.get("/api/diagnostics/native")
        assert response.status_code == 200
        assert response.json()["native_transport_ready"] is True
        assert client.get("/api/health").status_code == 200