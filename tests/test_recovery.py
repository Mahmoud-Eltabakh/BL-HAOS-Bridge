import asyncio

import pytest

from backend.bl_haos.diagnostics import DiagnosticsService
from backend.bl_haos.health import FailureClass, HealthRegistry, SpeakerState
from backend.bl_haos.recovery import RecoveryService


class FakeManager:
    def __init__(self):
        self.calls = []

    async def connect_device(self, address):
        self.calls.append(("connect_device", address))
        return True

    async def recover_dbus(self):
        self.calls.append(("recover_dbus",))
        return True

    async def ensure_device(self, address):
        self.calls.append(("ensure_device", address))
        return object()


@pytest.fixture
def recovery():
    health = HealthRegistry()
    diagnostics = DiagnosticsService(health, clock=lambda: 1700000000.0)
    manager = FakeManager()
    return RecoveryService(health, diagnostics, manager), manager, health


def test_supported_failure_classes_have_complete_guidance(recovery):
    service, _, _ = recovery

    for failure_class in ("pairing_failed", FailureClass.SINK_MISSING.value, "native_integration_unavailable", FailureClass.RECONNECT_EXHAUSTED.value):
        guidance = service.guidance(failure_class)
        assert guidance["failure_class"] == failure_class
        assert guidance["diagnosis"]
        assert guidance["next_steps"]
        assert guidance["prerequisites"]
        assert guidance["actions"]
        assert guidance["expected_outcome"]
        assert guidance["status"] == "available"

    generic = service.guidance("future_failure")
    assert generic["status"] == "escalate"
    assert generic["actions"] == ["refresh_diagnostics"]


@pytest.mark.asyncio
async def test_recovery_action_is_allowlisted_normalized_and_correlated(recovery):
    service, manager, health = recovery
    health.observe_speaker("AA-BB-CC-11-22-33", SpeakerState.UNAVAILABLE, failure=FailureClass.RECONNECT_EXHAUSTED)

    result = await service.execute("retry_reconnect", target="AA-BB-CC-11-22-33")

    assert result["action_id"] == "retry_reconnect"
    assert result["target"] == "aa:bb:cc:11:22:33"
    assert result["correlation_id"] == "recovery-0001"
    assert result["result"] == "succeeded"
    assert result["next_guidance"]
    assert manager.calls == [("connect_device", "aa:bb:cc:11:22:33")]


@pytest.mark.asyncio
async def test_recovery_rejects_arbitrary_commands_and_unvalidated_targets(recovery):
    service, manager, _ = recovery

    with pytest.raises(ValueError, match="Unsupported recovery action"):
        await service.execute("python -c evil", target="aa:bb:cc:11:22:33")
    with pytest.raises(ValueError, match="Invalid recovery target"):
        await service.execute("retry_reconnect", target="not-a-device")
    with pytest.raises(ValueError, match="Target is required"):
        await service.execute("retry_reconnect")
    assert manager.calls == []


@pytest.mark.asyncio
async def test_recovery_conflict_is_bounded(recovery):
    service, manager, _ = recovery
    started = asyncio.Event()
    release = asyncio.Event()

    async def connect(address):
        started.set()
        await release.wait()
        return True

    manager.connect_device = connect
    first = asyncio.create_task(service.execute("retry_reconnect", target="aa:bb:cc:11:22:33"))
    await started.wait()
    second = await service.execute("retry_reconnect", target="aa:bb:cc:11:22:33")
    assert second["result"] == "conflict"
    release.set()
    assert (await first)["result"] == "succeeded"