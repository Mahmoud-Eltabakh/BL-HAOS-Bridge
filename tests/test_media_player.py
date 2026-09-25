import asyncio
import json
import os
import signal
import time
from types import SimpleNamespace

import pytest
from backend.bl_haos.ha.player import MediaPlayerBridge
from backend.bl_haos.health import FailureClass, HealthRegistry, SpeakerState


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stdout = object()
        self.signals = []

    def send_signal(self, signal_number):
        self.signals.append(signal_number)

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


async def fake_sink(_address):
    return "bluez_output.10_22_33_44_55_66.1"


async def fake_process(*args, **_kwargs):
    return FakeProcess(returncode=0 if args[0] == "wpctl" else None)

@pytest.mark.asyncio
async def test_media_player_state_transitions():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    assert bridge.get_state(address) == "idle"
    assert bridge.get_volume(address) == 0.70

    await bridge.handle_command(address, "PLAY_MEDIA:https://example.test/audio.mp3?token=signed")
    assert bridge.get_state(address) == "playing"

    # Send PAUSE
    await bridge.handle_command(address, "PAUSE")
    assert bridge.get_state(address) == "paused"

    # Send STOP
    await bridge.handle_command(address, "STOP")
    assert bridge.get_state(address) == "idle"

@pytest.mark.asyncio
async def test_media_player_volume_and_tts():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    # Set volume
    await bridge.handle_command(address, "VOLUME:0.85")
    assert bridge.get_volume(address) == 0.85

    # Play media stream
    tts_url = "http://homeassistant.local:8123/api/tts_proxy/message123.mp3?cache=signed"
    await bridge.handle_command(address, f"PLAY_MEDIA:{tts_url}")
    assert bridge.get_state(address) == "playing"


@pytest.mark.asyncio
async def test_media_player_rejects_unsafe_url_before_subprocess_creation():
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    with pytest.raises(Exception, match="safe HTTP"):
        await bridge.play_url("10:22:33:44:55:66", "file:///etc/passwd")
    assert calls == []


@pytest.mark.asyncio
async def test_media_player_keeps_valid_url_as_one_process_argument():
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3?token=signed")

    ffmpeg_args = calls[0][0]
    assert ffmpeg_args[0] == "ffmpeg"
    assert ffmpeg_args.count("https://example.test/audio.mp3?token=signed") == 1


@pytest.mark.asyncio
async def test_play_replays_last_url_after_stop():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    await bridge.handle_command(address, "PLAY_MEDIA:https://example.test/audio.mp3")
    await bridge.handle_command(address, "STOP")
    assert bridge.get_state(address) == "idle"

    # Pressing Play with nothing running (e.g. after a stop or add-on restart)
    # should resume the last known stream instead of erroring.
    await bridge.execute(address, "play")
    assert bridge.get_state(address) == "playing"


@pytest.mark.asyncio
async def test_play_without_any_prior_media_still_raises():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    with pytest.raises(Exception, match="No active playback to resume"):
        await bridge.execute(address, "play")


@pytest.mark.asyncio
async def test_keepalive_pulses_idle_speakers_without_changing_state():
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append(args[0])
        return FakeProcess(returncode=0)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    bridge.register_keepalive(address)

    assert bridge.get_state(address) == "idle"
    await bridge._send_keepalive_pulse(address)

    assert calls[0] == "ffmpeg"
    assert calls[1] == "pw-play"
    # A keep-alive pulse must never surface as playback in the entity state.
    assert bridge.get_state(address) == "idle"
    assert address not in bridge.active_processes


@pytest.mark.asyncio
async def test_keepalive_skips_speakers_with_active_playback():
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append(args[0])
        return FakeProcess(returncode=0 if args[0] == "wpctl" else None)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    bridge.register_keepalive(address)

    await bridge.handle_command(address, "PLAY_MEDIA:https://example.test/audio.mp3")
    calls.clear()

    await bridge._send_keepalive_pulse(address)
    assert calls == []


@pytest.mark.asyncio
async def test_unregister_keepalive_removes_address():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"
    bridge.register_keepalive(address)
    assert address in bridge.keepalive_addresses

    bridge.unregister_keepalive(address)
    assert address not in bridge.keepalive_addresses


@pytest.mark.asyncio
async def test_media_player_rejects_unsafe_sink_before_process_creation():
    calls = []

    async def unsafe_sink(_address):
        return "sink;pw-play --target attacker"

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=unsafe_sink, process_factory=process_factory)
    with pytest.raises(Exception, match="PipeWire sink is invalid"):
        await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")
    assert calls == []


@pytest.mark.asyncio
async def test_media_player_subprocesses_use_fixed_argv_and_no_shell():
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    assert calls[0][0][:4] == ("ffmpeg", "-nostdin", "-loglevel", "error")
    assert calls[1][0][:3] == ("pw-play", "--target", "bluez_output.10_22_33_44_55_66.1")
    assert all(call[1].get("shell") is not True for call in calls)


class ProbeProcess(FakeProcess):
    def __init__(self, output: bytes, returncode: int = 0):
        super().__init__(returncode=returncode)
        self.output = output

    async def communicate(self):
        return self.output, b""


class HangingProbeProcess(FakeProcess):
    async def communicate(self):
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_resolve_sink_prefers_matching_pipewire_sink():
    graph = [{"id": 42, "props": {"media.class": "Audio/Sink", "node.name": "bluez_output.10_22_33_44_55_66.1", "device.description": "10:22:33:44:55:66"}}]

    async def process_factory(*args, **_kwargs):
        assert args[0] == "pw-dump"
        return ProbeProcess(json.dumps(graph).encode())

    bridge = MediaPlayerBridge(process_factory=process_factory)
    assert await bridge._async_resolve_sink("10:22:33:44:55:66") == "bluez_output.10_22_33_44_55_66.1"


@pytest.mark.asyncio
async def test_resolve_sink_stops_when_pipewire_probe_hangs(monkeypatch):
    process = HangingProbeProcess()

    async def process_factory(*args, **_kwargs):
        return process

    monkeypatch.setattr("backend.bl_haos.ha.player.SINK_PROBE_TIMEOUT", 0.01)
    bridge = MediaPlayerBridge(process_factory=process_factory)

    assert await bridge._async_resolve_sink("10:22:33:44:55:66") is None
    assert process.returncode == -9


@pytest.mark.asyncio
async def test_resolve_sink_falls_back_to_host_pulseaudio(monkeypatch):
    calls = []

    async def process_factory(*args, **_kwargs):
        calls.append(args)
        if args[0] == "pw-dump":
            return ProbeProcess(b"[]")
        return ProbeProcess(b"12\tbluez_sink.10_22_33_44_55_66.a2dp_sink\tmodule-bluez5-device\ts16le 2ch 48000Hz\tIDLE\n")

    monkeypatch.setattr("backend.bl_haos.ha.player.os.path.exists", lambda path: path == "/run/audio/pulse.sock")
    bridge = MediaPlayerBridge(process_factory=process_factory)

    assert await bridge._async_resolve_sink("10:22:33:44:55:66") == "pulse:bluez_sink.10_22_33_44_55_66.a2dp_sink"
    assert calls[1][:4] == ("pactl", "-s", "unix:/run/audio/pulse.sock", "list")


@pytest.mark.asyncio
async def test_media_player_uses_paplay_for_pulseaudio_sink():
    calls = []

    async def pulse_sink(_address):
        return "pulse:bluez_sink.10_22_33_44_55_66.a2dp_sink"

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=pulse_sink, process_factory=process_factory)
    await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    assert calls[1][0][:3] == ("paplay", "--device", "bluez_sink.10_22_33_44_55_66.a2dp_sink")
    assert "-" not in calls[1][0], "paplay must read from stdin without a '-' argument"
    assert calls[1][1]["env"]["PULSE_SERVER"] == "unix:/run/audio/pulse.sock"


@pytest.mark.asyncio
async def test_connected_speaker_without_sink_reports_transport_held():
    async def no_sink(_address):
        return None

    health = HealthRegistry()
    health.observe_speaker("10:22:33:44:55:66", SpeakerState.CONNECTED)
    bridge = MediaPlayerBridge(sink_resolver=no_sink, health_registry=health)

    with pytest.raises(Exception, match="Bluetooth audio sink is unavailable"):
        await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    failure = health.snapshot().components["pipewire"].failure
    assert failure is not None
    assert failure.classification == FailureClass.SINK_UNAVAILABLE_TRANSPORT_HELD
    assert "restart the add-on" in (failure.detail or "")


class WedgedProcess(FakeProcess):
    """A playback child that ignores every signal and never exits.

    This models a decoder/player stuck on a wedged A2DP sink. Before the fix
    `_stop_processes` awaited such a child forever, so the *next* play_media
    never spawned its processes: the first play looked dead and the second one
    worked because there was nothing left to tear down.
    """

    def __init__(self, pid=4242):
        super().__init__(returncode=None)
        self.pid = pid

    def terminate(self):
        pass

    def kill(self):
        pass

    async def wait(self):
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_stop_processes_never_hangs_on_a_wedged_child(monkeypatch):
    """Teardown must always return, however badly a child misbehaves."""
    monkeypatch.setattr("backend.bl_haos.ha.player.STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr("backend.bl_haos.ha.player.KILL_GRACE_SECONDS", 0.05)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"
    bridge.active_processes[address] = (WedgedProcess(pid=111), WedgedProcess(pid=222))

    started = time.monotonic()
    await bridge._stop_processes(address)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "teardown must stay bounded so the next command still runs"
    assert address not in bridge.active_processes


@pytest.mark.asyncio
async def test_play_media_still_spawns_after_a_wedged_predecessor(monkeypatch):
    """The reported symptom: the second play worked, the first one silently did nothing."""
    monkeypatch.setattr("backend.bl_haos.ha.player.STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr("backend.bl_haos.ha.player.KILL_GRACE_SECONDS", 0.05)

    spawned = []

    async def process_factory(*args, **kwargs):
        spawned.append(args[0])
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    bridge.active_processes[address] = (WedgedProcess(pid=111), WedgedProcess(pid=222))

    await asyncio.wait_for(bridge.play_url(address, "https://example.test/audio.mp3"), timeout=2)

    assert spawned == ["ffmpeg", "pw-play"]


@pytest.mark.asyncio
async def test_stop_processes_continues_children_before_signalling(monkeypatch):
    """A SIGSTOPped child never sees SIGTERM; it must be continued first."""
    monkeypatch.setattr("backend.bl_haos.ha.player.STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr("backend.bl_haos.ha.player.KILL_GRACE_SECONDS", 0.05)
    sequence = []
    monkeypatch.setattr(
        MediaPlayerBridge, "_continue_process", staticmethod(lambda process: sequence.append("continue"))
    )
    monkeypatch.setattr(
        MediaPlayerBridge, "_terminate_process", staticmethod(lambda process: sequence.append("terminate"))
    )

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"
    bridge.active_processes[address] = (FakeProcess(returncode=None), FakeProcess(returncode=None))

    await bridge._stop_processes(address)

    assert sequence == ["continue", "continue", "terminate", "terminate"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are required")
def test_continue_process_signals_the_process_group(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.bl_haos.ha.player.os.killpg", lambda pid, sig: calls.append((pid, sig)))

    MediaPlayerBridge._continue_process(SimpleNamespace(pid=1234))

    assert calls == [(1234, signal.SIGCONT)]


@pytest.mark.asyncio
async def test_stop_processes_returns_without_waiting_out_the_grace_window(monkeypatch):
    """A wedged child must be reaped in the background, not in front of the user."""
    monkeypatch.setattr("backend.bl_haos.ha.player.COMMAND_STOP_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr("backend.bl_haos.ha.player.STOP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr("backend.bl_haos.ha.player.KILL_GRACE_SECONDS", 0.01)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"
    bridge.active_processes[address] = (WedgedProcess(pid=111), WedgedProcess(pid=222))

    started = time.monotonic()
    await bridge._stop_processes(address)
    elapsed = time.monotonic() - started

    assert elapsed < 0.3, "the command path must not wait out the escalation window"
    assert address not in bridge.active_processes

    await asyncio.sleep(0.1)
    assert not bridge._background_tasks, "the background reaper must finish and be collected"


@pytest.mark.asyncio
async def test_stop_processes_is_a_noop_without_active_processes():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)

    await bridge._stop_processes("10:22:33:44:55:66")

    assert bridge.active_processes == {}


@pytest.mark.asyncio
async def test_play_url_serializes_concurrent_commands_per_speaker():
    """Overlapping play requests must not race on the same process pair."""
    active = 0
    observed = []

    async def slow_sink(_address):
        nonlocal active
        active += 1
        observed.append(active)
        await asyncio.sleep(0.02)
        active -= 1
        return "bluez_output.10_22_33_44_55_66.1"

    bridge = MediaPlayerBridge(sink_resolver=slow_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    await asyncio.gather(
        bridge.play_url(address, "https://example.test/one.mp3"),
        bridge.play_url(address, "https://example.test/two.mp3"),
    )

    assert max(observed) == 1
    assert len(observed) == 2


@pytest.mark.asyncio
async def test_pulseaudio_player_bounds_queued_latency():
    """Pause/stop responsiveness depends on a small client-side buffer."""
    from backend.bl_haos.ha.player import PLAYER_LATENCY_MSEC

    calls = []

    async def pulse_sink(_address):
        return "pulse:bluez_sink.10_22_33_44_55_66.a2dp_sink"

    async def process_factory(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=pulse_sink, process_factory=process_factory)
    await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    paplay_args = calls[1]
    assert f"--latency-msec={PLAYER_LATENCY_MSEC}" in paplay_args
    assert PLAYER_LATENCY_MSEC <= 500
