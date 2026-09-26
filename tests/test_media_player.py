import asyncio
import json
import os
import signal
import time
from types import SimpleNamespace

import pytest
from backend.bl_haos.constants import PLAYBACK_PROTOCOL_WHITELIST
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

    async def communicate(self):
        """Probes read stdout via communicate(); playback children never do."""
        return b"", b""


async def fake_sink(_address):
    return "bluez_output.10_22_33_44_55_66.1"


async def fake_process(*args, **_kwargs):
    return FakeProcess(returncode=0 if args[0] == "wpctl" else None)


def test_sink_warmup_is_dropped_without_a_running_loop():
    """Regression: a synchronous listener path must not abandon a coroutine.

    ``register_keepalive`` is reached from a Bluetooth event listener, so it can
    run outside the event loop. Creating the warm-up task there used to raise
    RuntimeError and leave ``_async_warm_sink`` un-awaited - the warm-up silently
    never happened and the suite leaked a "was never awaited" RuntimeWarning.
    """
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    bridge.register_keepalive(address)

    assert bridge.keepalive_addresses == {address}
    assert bridge._warmup_tasks == {}
    assert bridge._background_tasks == set()


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
    # The decoder is pinned to network protocols: a manifest must not be able to
    # pull the bridge's own filesystem into a stream.
    assert ffmpeg_args[ffmpeg_args.index("-protocol_whitelist") + 1] == PLAYBACK_PROTOCOL_WHITELIST
    assert "file" not in PLAYBACK_PROTOCOL_WHITELIST.split(",")
    assert "concat" not in PLAYBACK_PROTOCOL_WHITELIST.split(",")


@pytest.mark.asyncio
async def test_play_url_refuses_targets_that_are_the_bridge_itself():
    """Loopback, metadata and legacy numeric spellings must never be fetched."""
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)

    for url in (
        "http://127.0.0.1:8099/api/devices?audio_only=false",
        "http://localhost:8099/api/health",
        "http://[::ffff:127.0.0.1]:8099/api/health",
        "http://2130706433:8099/api/health",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/audio.mp3",
    ):
        with pytest.raises(Exception, match="loopback, link-local or multicast"):
            await bridge.play_url("10:22:33:44:55:66", url)

    assert calls == [], "no decoder or player may be spawned for a refused target"


@pytest.mark.asyncio
async def test_play_url_still_accepts_lan_and_public_targets():
    """Home Assistant serves TTS and local media from a private address."""
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)

    for url in (
        "http://192.168.1.21:8123/api/tts_proxy/some-hash.mp3",
        "https://example.com/stream.mp3",
    ):
        await bridge.play_url("10:22:33:44:55:66", url)
        decoder_args = [call[0] for call in calls if call[0][0] == "ffmpeg"][-1]
        assert decoder_args.count(url) == 1


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
    # Registering also schedules a connect-time sink warm-up; it is covered by
    # its own tests and would otherwise race this one's spawn assertions.
    await bridge._cancel_sink_warmup(address)

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
    await bridge._cancel_sink_warmup(address)

    await bridge.handle_command(address, "PLAY_MEDIA:https://example.test/audio.mp3")
    calls.clear()

    await bridge._send_keepalive_pulse(address)
    assert calls == []


@pytest.mark.asyncio
async def test_connect_warms_the_sink_so_the_first_play_does_not_pay_for_it():
    """The A2DP sink is opened at connect time, not on the user's first play."""
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append(args[0])
        return FakeProcess(returncode=0)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"

    bridge.register_keepalive(address)
    await bridge._warmup_tasks[address]

    assert calls[0] == "ffmpeg"
    assert calls[1] == "pw-play"
    # Warming the sink must not pretend audio is playing.
    assert bridge.get_state(address) == "idle"
    assert address not in bridge.active_processes


@pytest.mark.asyncio
async def test_connect_warmup_retries_while_the_sink_is_still_appearing():
    """The sink node is often published a moment after the connect event."""
    resolutions = {"count": 0}
    calls = []

    async def late_sink(_address):
        resolutions["count"] += 1
        if resolutions["count"] < 3:
            return None
        return "bluez_output.10_22_33_44_55_66.1"

    async def process_factory(*args, **kwargs):
        calls.append(args[0])
        return FakeProcess(returncode=0)

    bridge = MediaPlayerBridge(sink_resolver=late_sink, process_factory=process_factory)
    bridge.warmup_retry_seconds = 0
    address = "10:22:33:44:55:66"

    bridge.register_keepalive(address)
    await bridge._warmup_tasks[address]

    # Retrying until the sink exists is the point; the exact number of probes is
    # not, because the warm-up also syncs the speaker's volume once it is up.
    assert resolutions["count"] >= 3, "the warm-up must keep probing until the sink exists"
    assert "ffmpeg" in calls
    assert "wpctl" in calls, "the connected speaker is put at its configured level"


@pytest.mark.asyncio
async def test_connect_warmup_gives_up_within_its_budget():
    """A speaker that never presents a sink must not leave a task looping."""
    resolutions = {"count": 0}

    async def missing_sink(_address):
        resolutions["count"] += 1
        return None

    bridge = MediaPlayerBridge(sink_resolver=missing_sink, process_factory=fake_process)
    bridge.warmup_attempts = 3
    bridge.warmup_retry_seconds = 0
    address = "10:22:33:44:55:66"

    bridge.register_keepalive(address)
    await bridge._warmup_tasks[address]

    assert resolutions["count"] == 3
    assert address not in bridge._warmup_tasks


@pytest.mark.asyncio
async def test_play_takes_over_from_a_pending_warmup():
    """A real play must not race the connect-time pulse for the same sink."""
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"
    bridge.register_keepalive(address)

    await bridge.handle_command(address, "PLAY_MEDIA:https://example.test/audio.mp3")

    assert address not in bridge._warmup_tasks
    assert bridge.get_state(address) == "playing"


@pytest.mark.asyncio
async def test_sink_warmup_can_be_disabled():
    """Callers that manage sink readiness themselves can switch warming off."""
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    bridge.warmup_attempts = 0

    bridge.register_keepalive("10:22:33:44:55:66")

    assert bridge._warmup_tasks == {}
    assert "10:22:33:44:55:66" in bridge.keepalive_addresses


@pytest.mark.asyncio
async def test_unregister_keepalive_cancels_a_pending_warmup():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"
    bridge.register_keepalive(address)

    bridge.unregister_keepalive(address)

    assert address not in bridge.keepalive_addresses
    assert address not in bridge._warmup_tasks


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


class DurationProbeProcess(FakeProcess):
    """An ffprobe invocation that reports one fixed duration."""

    def __init__(self, payload: bytes = b"240.05\n"):
        super().__init__(returncode=0)
        self._payload = payload

    async def communicate(self):
        return self._payload, b""


def test_parse_duration_reads_ffprobe_output():
    from backend.bl_haos.ha.player import MediaPlayerBridge

    assert MediaPlayerBridge._parse_duration("240.05\n") == pytest.approx(240.05)
    assert MediaPlayerBridge._parse_duration("12.5\n") == pytest.approx(12.5)
    assert MediaPlayerBridge._parse_duration("duration=240.05\n") == pytest.approx(240.05)
    assert MediaPlayerBridge._parse_duration("N/A\n") is None
    assert MediaPlayerBridge._parse_duration("0\n") is None
    assert MediaPlayerBridge._parse_duration("") is None


def test_parse_probe_metadata_reads_tags_and_duration():
    from backend.bl_haos.ha.player import MediaPlayerBridge

    metadata = MediaPlayerBridge._parse_probe_metadata("title=Athan Fajr\nartist=Malek\n duration=240.05\n")
    assert metadata == {"duration": pytest.approx(240.05), "title": "Athan Fajr", "artist": "Malek"}

    # A stream without tags must still yield its duration.
    assert MediaPlayerBridge._parse_probe_metadata("240.05\n")["duration"] == pytest.approx(240.05)
    assert MediaPlayerBridge._parse_probe_metadata("") == {"duration": None, "title": None, "artist": None}
    assert MediaPlayerBridge._parse_probe_metadata("title=\n")["title"] is None


def test_title_from_url_prefers_a_readable_filename():
    from backend.bl_haos.ha.player import MediaPlayerBridge

    assert (
        MediaPlayerBridge._title_from_url(
            "http://ha:8123/media/local/03.athan_fajr_Malek%20Chibat%20Al-Hamd.mp3?authSig=abc"
        )
        == "03.athan fajr Malek Chibat Al-Hamd"
    )
    assert MediaPlayerBridge._title_from_url("http://ha:8123/audio/track.flac") == "track"
    # Synthesized speech lives behind a cache hash; never show that hash.
    assert MediaPlayerBridge._title_from_url("http://ha:8123/api/tts_proxy/abc_-123.mp3") == "Text to speech"
    assert MediaPlayerBridge._title_from_url("http://ha:8123/") is None
    assert MediaPlayerBridge._title_from_url("") is None


@pytest.mark.asyncio
async def test_timeline_exposes_the_title_while_playing():
    """The card must name what is playing, not just show a progress bar."""
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    await bridge.play_url(
        address, "http://ha:8123/media/local/04.athan_mishary.mp3?authSig=signed"
    )

    # Available immediately, before the ffprobe refinement lands.
    assert bridge.get_timeline(address)["title"] == "04.athan mishary"

    await bridge.execute(address, "stop")
    assert bridge.get_timeline(address)["title"] is None


@pytest.mark.asyncio
async def test_probe_tags_refine_the_title_and_artist():
    async def process_factory(*args, **kwargs):
        if args[0] == "ffprobe":
            return DurationProbeProcess(payload=b"title=Athan Fajr\nartist=Malek Chibat\n duration=240.05\n")
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"

    await bridge.play_url(address, "http://ha:8123/media/local/04.athan_mishary.mp3")
    assert bridge.get_timeline(address)["title"] == "04.athan mishary"

    await asyncio.sleep(0.01)

    timeline = bridge.get_timeline(address)
    assert timeline["title"] == "Athan Fajr"
    assert timeline["artist"] == "Malek Chibat"
    assert timeline["duration"] == pytest.approx(240.05)


@pytest.mark.asyncio
async def test_timeline_tracks_position_and_ignores_paused_time():
    """HA needs a position clock that does not advance while paused."""
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    assert bridge.get_timeline(address) == {
        "position": None,
        "duration": None,
        "position_updated_at": None,
        "title": None,
        "artist": None,
    }

    await bridge.play_url(address, "https://example.test/audio.mp3")
    started = bridge.get_timeline(address)
    assert started["position"] is not None and started["position"] >= 0
    assert started["duration"] is None, "duration arrives later from the background probe"
    assert started["position_updated_at"] is not None

    await asyncio.sleep(0.05)
    assert bridge.get_timeline(address)["position"] > started["position"]

    await bridge.execute(address, "pause")
    paused = bridge.get_timeline(address)["position"]
    await asyncio.sleep(0.05)
    assert bridge.get_timeline(address)["position"] == paused, "the clock must freeze"

    await bridge.execute(address, "play")
    await asyncio.sleep(0.05)
    assert bridge.get_timeline(address)["position"] > paused

    await bridge.execute(address, "stop")
    assert bridge.get_timeline(address)["position"] is None


@pytest.mark.asyncio
async def test_timeline_position_never_exceeds_a_known_duration():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    await bridge.play_url(address, "https://example.test/audio.mp3")
    await asyncio.sleep(0.05)
    bridge.timelines[address]["duration"] = 0.01

    assert bridge.get_timeline(address)["position"] == 0.01


@pytest.mark.asyncio
async def test_duration_probe_publishes_media_length():
    """The probe must run in the background so playback is never delayed."""
    notified = []

    async def process_factory(*args, **kwargs):
        if args[0] == "ffprobe":
            return DurationProbeProcess()
        return FakeProcess()

    async def state_callback(address):
        notified.append(address)

    bridge = MediaPlayerBridge(
        sink_resolver=fake_sink, process_factory=process_factory, state_callback=state_callback
    )
    address = "10:22:33:44:55:66"

    await bridge.play_url(address, "https://example.test/audio.mp3")
    assert bridge.get_timeline(address)["duration"] is None

    await asyncio.sleep(0.01)

    assert bridge.get_timeline(address)["duration"] == pytest.approx(240.05)
    assert notified.count(address) >= 2, "HA is told again once the length is known"


@pytest.mark.asyncio
async def test_duration_probe_does_not_attach_to_a_superseded_stream():
    async def process_factory(*args, **kwargs):
        if args[0] == "ffprobe":
            return DurationProbeProcess()
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"

    await bridge.play_url(address, "https://example.test/one.mp3")
    await bridge.execute(address, "stop")
    await asyncio.sleep(0.01)

    assert bridge.get_timeline(address)["duration"] is None


@pytest.mark.asyncio
async def test_playback_end_clears_the_timeline():
    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    address = "10:22:33:44:55:66"

    await bridge.play_url(address, "https://example.test/audio.mp3")
    processes = bridge.active_processes[address]
    for process in processes:
        process.returncode = 0

    await bridge._watch_processes(address, *processes)

    assert bridge.get_state(address) == "idle"
    assert bridge.get_timeline(address)["position"] is None


@pytest.mark.asyncio
async def test_pipewire_player_bounds_queued_latency():
    """The PipeWire transport must get the buffer the PulseAudio transport gets.

    pw-play's own default is 100ms, and a bare ``--latency`` number means *samples*
    rather than milliseconds, so the flag has to carry its unit. Without it the
    transport this add-on actually uses held a fifth of the other one's buffer and
    any jitter beyond that was audible as stutter.
    """
    from backend.bl_haos.ha.player import PLAYER_LATENCY_MSEC

    calls = []

    async def process_factory(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    assert f"--latency={PLAYER_LATENCY_MSEC}ms" in calls[1]


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


def test_parse_sink_volume_reads_both_audio_servers():
    """The read-back has to understand what each client actually prints."""
    pactl = (
        "Volume: front-left: 45875 /  70% / -9.29 dB,   front-right: 45875 /  70% / -9.29 dB\n"
        "        balance 0.00\n"
    )
    assert MediaPlayerBridge.parse_sink_volume("pulse", pactl) == pytest.approx(0.70)
    assert MediaPlayerBridge.parse_sink_volume("pipewire", "Volume: 0.42\n") == pytest.approx(0.42)
    # A muted sink still reports its level: silence behind a non-zero slider is
    # reported as such rather than silently turned into 0.
    assert MediaPlayerBridge.parse_sink_volume("pipewire", "Volume: 0.42 [MUTED]\n") == pytest.approx(0.42)
    # Channels that disagree are reported at the loudest one.
    uneven = "Volume: front-left: 19661 / 30% / -31.00 dB, front-right: 45875 / 70% / -9.29 dB"
    assert MediaPlayerBridge.parse_sink_volume("pulse", uneven) == pytest.approx(0.70)
    assert MediaPlayerBridge.parse_sink_volume("pipewire", "") is None
    assert MediaPlayerBridge.parse_sink_volume("pipewire", "garbage") is None
    assert MediaPlayerBridge.parse_sink_volume("pulse", "no volume here") is None


class VolumeProcess(FakeProcess):
    """A probe whose stdout answers with an audio-server volume listing."""

    def __init__(self, output: bytes, returncode: int = 0):
        super().__init__(returncode=returncode)
        self._output = output

    async def communicate(self):
        return self._output, b""


@pytest.mark.asyncio
async def test_volume_is_read_back_from_the_sink_and_published():
    """A level changed on the speaker itself must reach the sliders.

    The speaker owns its volume: nothing in the audio graph tells the bridge when
    someone presses the speaker's buttons, so the sink is asked directly and a
    changed level is published to the dashboard and the entity.
    """
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append(args)
        return VolumeProcess(b"Volume: 0.42\n")

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    published = []

    async def state_callback(addr):
        published.append((addr, bridge.get_volume(addr)))

    bridge._state_callback = state_callback

    assert await bridge.refresh_volume(address) == pytest.approx(0.42)
    assert bridge.get_volume(address) == pytest.approx(0.42)
    assert published == [(address, pytest.approx(0.42))]
    assert calls[0][0] == "wpctl" and calls[0][1] == "get-volume"

    # Reading the same level again must not republish it.
    published.clear()
    await bridge.refresh_volume(address)
    assert published == []


@pytest.mark.asyncio
async def test_volume_read_back_uses_the_pulse_client_for_a_pulse_sink():
    calls = []

    async def pulse_sink(_address):
        return "pulse:bluez_sink.10_22_33_44_55_66.a2dp_sink"

    async def process_factory(*args, **kwargs):
        calls.append(args)
        return VolumeProcess(b"Volume: front-left: 32768 / 50% / -18.06 dB, front-right: 32768 / 50%")

    bridge = MediaPlayerBridge(sink_resolver=pulse_sink, process_factory=process_factory)

    assert await bridge.refresh_volume("10:22:33:44:55:66") == pytest.approx(0.50)
    assert calls[0][0] == "pactl"
    assert "get-sink-volume" in calls[0]
    assert "bluez_sink.10_22_33_44_55_66.a2dp_sink" in calls[0]


@pytest.mark.asyncio
async def test_volume_read_back_keeps_the_last_level_when_the_probe_fails():
    """An unreadable sink must not wipe the level the sliders are showing."""

    async def process_factory(*args, **kwargs):
        return VolumeProcess(b"", returncode=1)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    bridge.volumes[address] = 0.35

    assert await bridge.refresh_volume(address) is None
    assert bridge.get_volume(address) == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_volume_watch_reads_connected_speakers_on_its_own_clock():
    """The poll is what makes a level changed on the speaker show up by itself."""
    reads = []

    async def process_factory(*args, **kwargs):
        reads.append(args)
        return VolumeProcess(b"Volume: 0.55\n")

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    bridge.keepalive_addresses.add(address)
    bridge.volume_poll_seconds = 0.01

    bridge.start_volume_watch()
    for _ in range(300):
        if address in bridge.volumes:
            break
        await asyncio.sleep(0.01)
    await bridge.stop_volume_watch()

    assert bridge.volumes[address] == pytest.approx(0.55)
    assert reads, "the watch must read the sink"
    assert bridge._volume_watch_task is None


def test_get_volume_falls_back_to_the_speakers_own_startup_volume(tmp_path):
    """An unread speaker reports its configured level, never a global default."""
    from backend.bl_haos.config import ConfigStore

    store = ConfigStore(config_file=str(tmp_path / "settings.json"))
    store.update_speaker("10:22:33:44:55:66", default_volume=35)
    bridge = MediaPlayerBridge(config_store=store, sink_resolver=fake_sink, process_factory=fake_process)
    bridge.volumes.clear()

    assert bridge.get_volume("10:22:33:44:55:66") == pytest.approx(0.35)
    # A speaker nobody has configured still reports the global default.
    assert bridge.get_volume("AA:BB:CC:DD:EE:99") == pytest.approx(0.70)


def test_codec_from_props_reads_the_bluez_card_and_the_profile_fallback():
    """The negotiated codec is what tells a bad link from a downgraded one."""
    from backend.bl_haos.ha.player import codec_from_props

    address = "AA:BB:CC:DD:EE:01"
    card = {"device.name": "bluez_card.AA_BB_CC_DD_EE_01", "api.bluez5.codec": "LDAC"}
    assert codec_from_props(card, address) == "ldac"

    # Some builds only report the active profile, whose suffix names the codec.
    fallback = {"device.name": "bluez_card.AA_BB_CC_DD_EE_01", "api.bluez5.profile": "a2dp-sink-sbc-xq"}
    assert codec_from_props(fallback, address) == "sbc_xq"

    other = {"device.name": "bluez_card.11_22_33_44_55_66", "api.bluez5.codec": "aptx"}
    assert codec_from_props(other, address) is None, "another speaker's codec is not ours"

    junk = {"device.name": "bluez_card.AA_BB_CC_DD_EE_01", "api.bluez5.codec": "not a codec!"}
    assert codec_from_props(junk, address) is None
    assert codec_from_props(None, address) is None


def test_codec_from_graph_scans_until_it_finds_the_speaker():
    from backend.bl_haos.ha.player import codec_from_graph

    graph = [
        {"props": {"node.name": "ffmpeg-keepalive"}},
        {"info": {"props": {"device.name": "bluez_card.EC_81_93_53_A9_16", "api.bluez5.codec": "SBC"}}},
    ]
    assert codec_from_graph(graph, "ec:81:93:53:a9:16") == "sbc"
    assert codec_from_graph(graph, "aa:bb:cc:dd:ee:01") is None
    assert codec_from_graph("not a graph", "aa:bb:cc:dd:ee:01") is None


def test_profile_for_codec_matches_the_normalised_spelling():
    """Profile names are hyphenated, codec names underscored; both must match."""
    profiles = ["a2dp-sink-sbc-xq", "a2dp-sink-ldac", "off", "headset-head-unit"]
    assert MediaPlayerBridge._profile_for_codec(profiles, "sbc_xq") == "a2dp-sink-sbc-xq"
    assert MediaPlayerBridge._profile_for_codec(profiles, "ldac") == "a2dp-sink-ldac"
    assert MediaPlayerBridge._profile_for_codec(profiles, "aptx_hd") is None


@pytest.mark.asyncio
async def test_codec_override_pins_the_profile_the_server_offers(monkeypatch):
    """A configured codec pin is applied by discovered profile name."""
    calls: list[tuple] = []

    class CardListing:
        returncode = 0

        async def communicate(self):
            return (
                b"Card #1\n\tName: bluez_card.10_22_33_44_55_66\n\tProfiles:\n"
                b"\t\ta2dp-sink-sbc-xq: High Fidelity Playback (codec SBC-XQ)\n"
                b"\t\ta2dp-sink-ldac: High Fidelity Playback (codec LDAC)\n"
                b"\t\toff: Off\n",
                b"",
            )

    class SetProfile:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_process(*args, **kwargs):
        calls.append(args)
        return CardListing() if "list" in args else SetProfile()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    monkeypatch.setattr(bridge, "_configured_codec", lambda address: "sbc_xq")
    bridge._last_codec["10:22:33:44:55:66"] = "ldac"

    await bridge._apply_codec_override("10:22:33:44:55:66")

    set_calls = [args for args in calls if "set-card-profile" in args]
    assert len(set_calls) == 1
    assert set_calls[0][-2:] == ("bluez_card.10_22_33_44_55_66", "a2dp-sink-sbc-xq")


@pytest.mark.asyncio
async def test_codec_override_is_skipped_when_the_speaker_is_already_on_it(monkeypatch):
    """Steady state costs nothing: the pin is not re-applied on every play."""
    calls: list[tuple] = []

    async def fake_process(*args, **kwargs):
        calls.append(args)
        raise AssertionError("no profile switch is expected")

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=fake_process)
    monkeypatch.setattr(bridge, "_configured_codec", lambda address: "sbc_xq")
    bridge._last_codec["10:22:33:44:55:66"] = "sbc_xq"

    await bridge._apply_codec_override("10:22:33:44:55:66")

    assert calls == []

