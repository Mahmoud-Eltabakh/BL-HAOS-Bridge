import asyncio
import json

import pytest

from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.bluetooth.reconnect import AutoReconnectEngine, ReconnectState
from backend.bl_haos.health import (
    FailureClass,
    HealthRegistry,
    HealthState,
    SpeakerState,
    safe_detail,
)


def test_health_snapshot_is_versioned_bounded_and_redacted():
    registry = HealthRegistry()
    registry.observe_component(
        "pipewire",
        HealthState.UNAVAILABLE,
        failure=FailureClass.PIPEWIRE_UNAVAILABLE,
        detail="token=super-secret https://example.test/media?key=secret\nraw output",
    )

    snapshot = registry.snapshot()
    payload = snapshot.model_dump(mode="json")
    encoded = json.dumps(payload)

    assert payload["version"] == 1
    assert payload["status"] == "unavailable"
    assert "super-secret" not in encoded
    assert "https://example.test" not in encoded
    assert len(payload["components"]["pipewire"]["failure"]["detail"]) <= 256


def test_nested_diagnostics_redact_credentials_and_url_userinfo():
    detail = safe_detail({
        "authorization": "Bearer expected-token",
        "nested": {"password": "secret-password", "url": "https://user:pass@example.test/audio?token=query-secret"},
        "items": ["api_key=list-secret"],
    })

    assert detail is not None
    assert "expected-token" not in detail
    assert "secret-password" not in detail
    assert "query-secret" not in detail
    assert "user:pass" not in detail


def test_required_precedence_and_optional_component_isolation():
    registry = HealthRegistry()
    registry.set_lifecycle(HealthState.HEALTHY)
    registry.observe_component("bluetooth", HealthState.HEALTHY)
    registry.observe_component("pipewire", HealthState.HEALTHY)
    registry.observe_component(
        "native_bridge", HealthState.DEGRADED, required=False,
        failure=FailureClass.NATIVE_INTEGRATION_UNAVAILABLE,
    )
    assert registry.snapshot().status == HealthState.HEALTHY

    registry.observe_component(
        "bluetooth", HealthState.UNAVAILABLE, failure=FailureClass.DBUS_UNAVAILABLE
    )
    assert registry.snapshot().status == HealthState.UNAVAILABLE


def test_unprobed_optional_component_does_not_degrade_status():
    registry = HealthRegistry()
    registry.set_lifecycle(HealthState.HEALTHY)
    registry.observe_component("bluetooth", HealthState.HEALTHY)
    registry.observe_component("pipewire", HealthState.UNKNOWN, required=False)

    assert registry.snapshot().status == HealthState.HEALTHY


def test_speaker_transition_preserves_safe_failure_reason():
    registry = HealthRegistry()
    registry.observe_speaker("AA-BB-CC-11-22-33", SpeakerState.UNKNOWN)
    registry.observe_speaker("aa:bb:cc:11:22:33", SpeakerState.RECONNECTING)
    registry.observe_speaker(
        "aa:bb:cc:11:22:33",
        SpeakerState.UNAVAILABLE,
        failure=FailureClass.RECONNECT_EXHAUSTED,
        detail="attempt=9 token=hidden",
        attempt=9,
    )
    speaker = registry.snapshot().speakers["aa:bb:cc:11:22:33"]
    assert speaker.transition == "reconnecting->unavailable"
    assert speaker.failure.classification == FailureClass.RECONNECT_EXHAUSTED
    assert "hidden" not in (speaker.failure.detail or "")


@pytest.mark.asyncio
async def test_reconnect_tick_does_not_create_duplicate_workers():
    manager = type("Manager", (), {})()
    manager.add_event_listener = lambda listener: None
    manager.get_device_by_address = lambda address: DeviceInfo(
        path="/speaker", adapter_path="/org/bluez/hci0", address=address,
        trusted=True, is_audio_sink=True,
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def connect(address):
        started.set()
        await release.wait()

    manager.connect_device = connect
    registry = HealthRegistry()
    engine = AutoReconnectEngine(manager, health_registry=registry)
    engine.register_speaker("AA-BB-CC-11-22-33")
    profile = engine.profiles["aa:bb:cc:11:22:33"]
    profile.state = "backoff"
    profile.next_retry_time = 0

    await engine._tick()
    await engine._tick()
    assert len(engine._inflight) == 1
    await started.wait()
    release.set()
    await asyncio.gather(*engine._inflight.values())
    assert len(engine._inflight) == 0
    await engine.stop()


@pytest.mark.asyncio
async def test_reconnect_worker_ownership_is_cleaned_after_cancellation():
    manager = type("Manager", (), {})()
    manager.add_event_listener = lambda listener: None
    manager.get_device_by_address = lambda address: DeviceInfo(
        path="/speaker", adapter_path="/org/bluez/hci0", address=address,
        trusted=True, is_audio_sink=True,
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def connect(address):
        started.set()
        await release.wait()

    manager.connect_device = connect
    engine = AutoReconnectEngine(manager)
    engine.register_speaker("AA-BB-CC-11-22-33")
    engine.register_speaker("AA-BB-CC-11-22-33")
    profile = engine.profiles["aa:bb:cc:11:22:33"]
    profile.state = "backoff"
    profile.next_retry_time = 0

    await engine._tick()
    worker = engine._inflight[profile.address]
    await started.wait()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    assert profile.address not in engine._inflight
    release.set()


@pytest.mark.asyncio
async def test_reconnect_exhaustion_publishes_unavailable_and_holds_breaker():
    manager = type("Manager", (), {})()
    manager.add_event_listener = lambda listener: None
    manager.get_device_by_address = lambda address: DeviceInfo(
        path="/speaker", adapter_path="/org/bluez/hci0", address=address,
        trusted=True, is_audio_sink=True,
    )

    async def fail_connect(address):
        raise RuntimeError("connection refused")

    manager.connect_device = fail_connect
    registry = HealthRegistry()
    engine = AutoReconnectEngine(
        manager,
        max_failures_before_breaker=1,
        health_registry=registry,
    )
    engine.register_speaker("AA-BB-CC-11-22-33")
    profile = engine.profiles["aa:bb:cc:11:22:33"]
    profile.state = "backoff"
    profile.next_retry_time = 0

    await engine._tick()
    await asyncio.gather(*engine._inflight.values())

    speaker = registry.snapshot().speakers[profile.address]
    assert profile.state == ReconnectState.CIRCUIT_BROKEN
    assert speaker.state == SpeakerState.UNAVAILABLE
    assert speaker.failure.classification == FailureClass.RECONNECT_EXHAUSTED
    assert speaker.attempt == 1
    assert profile.address not in engine._inflight

    await engine._tick()
    assert profile.address not in engine._inflight


def test_link_churn_counters_only_move_on_state_edges():
    """These counters are how an operator tells a flapping link from a dropout."""
    registry = HealthRegistry()
    address = "AA:BB:CC:DD:EE:01"
    registry.observe_speaker(address, SpeakerState.DISCONNECTED)
    registry.observe_speaker(address, SpeakerState.CONNECTED)
    registry.observe_speaker(address, SpeakerState.CONNECTED)
    registry.observe_speaker(address, SpeakerState.DISCONNECTED)
    assert registry.speakers[address.lower()].transition == "connected->disconnected"

    # A repeated observation is the same state, not another event.
    registry.observe_speaker(address, SpeakerState.DISCONNECTED)

    speaker = registry.speakers[address.lower()]
    assert speaker.connects == 1, "a repeated observation is not a new connection"
    assert speaker.disconnects == 1, "a first sighting of 'disconnected' is not a dropout"
    assert speaker.state == SpeakerState.DISCONNECTED


def test_suppressed_flaps_and_the_negotiated_codec_are_reported():
    registry = HealthRegistry()
    address = "AA:BB:CC:DD:EE:01"
    registry.observe_speaker(address, SpeakerState.CONNECTED)
    registry.record_suppressed_flap(address, "reconnected inside the grace window")
    registry.record_suppressed_flap(address, "flap limit reached; holding reconnects")
    registry.record_speaker_codec(address, "SBC")

    speaker = registry.speakers[address.lower()]
    assert speaker.suppressed_flaps == 2
    assert speaker.codec == "sbc"
    # The reason is the most recent one, so a cooldown reports itself last.
    assert "holding reconnects" in speaker.link_reason
    # Reporting a codec must not disturb the link counters.
    assert speaker.connects == 1
    assert speaker.disconnects == 0


def test_codec_from_the_audio_graph_is_bounded_before_it_is_reported():
    """The codec is read from a local process, so it is not trusted verbatim."""
    registry = HealthRegistry()
    address = "AA:BB:CC:DD:EE:01"
    registry.observe_speaker(address, SpeakerState.CONNECTED)

    registry.record_speaker_codec(address, "../../etc/passwd; rm -rf /")
    assert registry.speakers[address.lower()].codec is None

    registry.record_speaker_codec(address, "AptX-HD")
    assert registry.speakers[address.lower()].codec == "aptx-hd"

