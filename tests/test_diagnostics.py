import json
from itertools import count

from backend.bl_haos.diagnostics import DiagnosticsService
from backend.bl_haos.health import FailureClass, HealthRegistry, HealthState, SpeakerState


def populated_service() -> DiagnosticsService:
    observations = count(1)
    registry = HealthRegistry(clock=lambda: float(next(observations)))
    registry.set_lifecycle(HealthState.HEALTHY)
    registry.observe_component("bluetooth", HealthState.HEALTHY, source="bluez")
    registry.observe_component(
        "pipewire",
        HealthState.DEGRADED,
        failure=FailureClass.SINK_MISSING,
        detail="token=secret https://user:pass@example.test/audio?key=hidden",
        source="pipewire",
    )
    registry.observe_speaker(
        "AA:BB:CC:11:22:33",
        SpeakerState.UNAVAILABLE,
        failure=FailureClass.RECONNECT_EXHAUSTED,
        detail="authorization=Bearer secret",
        attempt=3,
    )
    return DiagnosticsService(registry, clock=lambda: 1000.0)


def test_diagnostics_projection_is_versioned_and_uses_canonical_health():
    service = populated_service()

    payload = service.snapshot(
        adapters=[{"name": "hci0", "powered": True}],
        sinks={"available": False, "count": 0},
    )

    assert payload["contract_version"] == 1
    assert payload["status"] == "degraded"
    assert payload["lifecycle"] == "healthy"
    assert payload["components"]["pipewire"]["failure_class"] == "sink_missing"
    assert payload["speakers"][0]["transition"] == "unavailable"
    assert payload["adapters"] == [{"name": "hci0", "powered": True}]
    assert payload["sink_availability"] == {"available": False, "count": 0}
    assert payload["last_failure"]["classification"] == "reconnect_exhausted"
    assert payload["timestamp"] == 1000.0


def test_support_bundle_redacts_sensitive_values_and_is_deterministic():
    service = populated_service()
    service.record_event(
        "playback_failure",
        component="pipewire",
        detail={
            "authorization": "Bearer native-secret",
            "url": "https://user:pass@example.test/media?token=secret",
            "dbus_payload": {"Address": "AA:BB:CC:11:22:33"},
            "output": "x" * 1000,
        },
        failure_class=FailureClass.PIPEWIRE_UNAVAILABLE,
    )

    first = service.support_bundle()
    second = service.support_bundle()
    encoded = json.dumps(first, sort_keys=True)

    assert first == second
    assert "native-secret" not in encoded
    assert "user:pass" not in encoded
    assert "token=secret" not in encoded
    assert "dbus_payload" not in encoded
    assert len(encoded.encode()) <= service.MAX_BUNDLE_BYTES
    assert first["diagnostics"]["recent_failures"]
    assert first["versions"] == {"diagnostics": 1, "events": 1, "health": 1}


def test_event_history_is_bounded_and_correlation_is_stable():
    service = populated_service()
    first = service.record_event("startup", component="bridge")
    for index in range(service.MAX_EVENTS + 5):
        service.record_event("transition", component="bridge", detail={"index": index})

    assert first["correlation_id"]
    assert len(service.events()) == service.MAX_EVENTS
    assert service.events()[0]["detail"]["index"] == 5
    assert all(len(json.dumps(event).encode()) <= service.MAX_EVENT_BYTES for event in service.events())


def test_telemetry_envelope_carries_context_failure_and_recovery_without_secrets():
    service = populated_service()
    event = service.record_event(
        "reconnect_outcome",
        component="bluetooth",
        speaker="AA:BB:CC:11:22:33",
        adapter="hci0",
        detail={"authorization": "Bearer should-not-escape", "attempt": 2},
        failure_class=FailureClass.RECONNECT_EXHAUSTED,
        transition_reason="sink unavailable",
        recovery="exhausted",
        correlation_id="corr-123",
    )

    assert event["version"] == 1
    assert event["correlation_id"] == "corr-123"
    assert event["speaker"] == "AA:BB:CC:11:22:33"
    assert event["adapter"] == "hci0"
    assert event["failure_class"] == "reconnect_exhausted"
    assert event["transition_reason"] == "sink unavailable"
    assert event["recovery"] == "exhausted"
    assert "should-not-escape" not in json.dumps(event)