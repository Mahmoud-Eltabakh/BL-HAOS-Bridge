import asyncio
import json
import logging
import os
import signal
import time
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.bl_haos.constants import PLAYBACK_PROTOCOL_WHITELIST
from backend.bl_haos.ha.player import MediaPlayerBridge, MediaPlayerError
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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "spawn", "unexpected", "stderr", "drain", "probe", "metadata"])
async def test_player_url_redact_real_execution_logs(caplog, failure):
    sentinels = (
        "playerhostsentinel.example", "playerpathsentinel", "playerquerykeysentinel",
        "playerqueryvaluesentinel", "playerusersentinel", "playerpasswordsentinel", "playertokensentinel",
    )
    url = (
        f"https://{sentinels[0]}/{sentinels[1]}?{sentinels[2]}={sentinels[3]}"
        f"&user={sentinels[4]}&password={sentinels[5]}&token={sentinels[6]}"
    )
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "ffmpeg" and failure in {"spawn", "unexpected"}:
            raise (OSError(url) if failure == "spawn" else RuntimeError(url))
        if args[0] == "ffprobe" and failure == "probe":
            raise RuntimeError(" ".join(sentinels))
        process = FakeProcess(returncode=0 if args[0] == "wpctl" else None)
        if args[0] == "ffprobe" and failure == "metadata":
            process.communicate = AsyncMock(return_value=(b"duration=240\n", b""))
        if args[0] == "ffmpeg" and failure in {"stderr", "drain"}:
            lines = [f"error {url}\n".encode(), " ".join(sentinels).encode(), b""]
            process.stderr = SimpleNamespace(readline=AsyncMock(
                side_effect=RuntimeError(url) if failure == "drain" else lines,
            ))
        return process

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    address = "10:22:33:44:55:66"
    caplog.set_level(logging.DEBUG, logger="bl_haos")
    caplog.set_level(logging.DEBUG, logger="backend.bl_haos")
    caplog.clear()
    error_text = ""
    if failure in {"spawn", "unexpected"}:
        with pytest.raises(MediaPlayerError) as caught:
            await bridge.execute(address, "play_media", url=url)
        error_text = "".join(traceback.format_exception(caught.value))
    else:
        await bridge.execute(address, "play_media", url=url)
        assert bridge.last_urls[address] == url
        assert bridge.get_state(address) == "playing"
        if failure == "metadata":
            await bridge._async_probe_duration(address, url)
            assert bridge.get_timeline(address)["duration"] == 240
            assert bridge.get_timeline(address)["title"] == sentinels[1]
        tasks = list(bridge._background_tasks)
        await bridge.execute(address, "stop")
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        assert not bridge._background_tasks
    decoder_args = next(args for args, _ in calls if args[0] == "ffmpeg")
    assert decoder_args.count(url) == 1
    assert decoder_args[decoder_args.index("-i") + 1] == url
    assert decoder_args[decoder_args.index("-protocol_whitelist") + 1] == PLAYBACK_PROTOCOL_WHITELIST
    assert all(kwargs.get("shell") is not True for _, kwargs in calls)
    assert "call enter function=" in caplog.text
    assert "play_media" in caplog.text
    for sentinel in (url, *sentinels):
        assert sentinel not in caplog.text + error_text


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://rejectusersentinel:rejectpasswordsentinel@rejecthostsentinel.example/rejectpathsentinel?token=rejecttokensentinel",
    "file:///rejectpathsentinel?token=rejecttokensentinel",
    "http://127.0.0.1/rejectpathsentinel?token=rejecttokensentinel",
    "https://[malformedhostsentinel]/rejectpathsentinel?token=rejecttokensentinel",
    "https://rejecthostsentinel.example:porttokensentinel/rejectpathsentinel?token=rejecttokensentinel",
])
async def test_player_url_redact_rejection_before_side_effects(caplog, url):
    process_factory = AsyncMock()
    sink_resolver = AsyncMock()
    bridge = MediaPlayerBridge(sink_resolver=sink_resolver, process_factory=process_factory)
    caplog.set_level(logging.DEBUG, logger="bl_haos")
    caplog.set_level(logging.DEBUG, logger="backend.bl_haos")
    with pytest.raises(MediaPlayerError) as caught:
        await bridge.execute("10:22:33:44:55:66", "play_media", url=url)
    process_factory.assert_not_awaited()
    sink_resolver.assert_not_awaited()
    output = caplog.text + "".join(traceback.format_exception(caught.value))
    for sentinel in (url, "rejectusersentinel", "rejectpasswordsentinel", "rejecthostsentinel", "rejectpathsentinel", "rejecttokensentinel", "malformedhostsentinel", "porttokensentinel"):
        assert sentinel not in output
    assert caught.value.__suppress_context__
    assert str(caught.value) in {
        "Media URL must be a safe HTTP(S) URL",
        "Media URL must not target a loopback, link-local or multicast address",
    }


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
async def test_resolve_sink_records_the_rate_the_link_runs_at():
    """The rate belongs to the diagnostic that explains a quality complaint.

    The A2DP link is asked for 48 kHz and the player feeds 48 kHz, so the graph
    usually confirms there is nothing in between. When a speaker negotiates 44.1
    kHz anyway, something converts every sample - and that has to be visible.
    """
    graph = [{
        "id": 42,
        "props": {
            "media.class": "Audio/Sink",
            "node.name": "bluez_output.10_22_33_44_55_66.1",
            "device.description": "10:22:33:44:55:66",
            "audio.rate": 44100,
        },
    }]

    async def process_factory(*args, **_kwargs):
        return ProbeProcess(json.dumps(graph).encode())

    bridge = MediaPlayerBridge(process_factory=process_factory)
    assert await bridge._async_resolve_sink("10:22:33:44:55:66") == "bluez_output.10_22_33_44_55_66.1"
    assert bridge.sink_rates == {"10:22:33:44:55:66": 44100}


def test_node_rate_reads_every_shape_pipewire_reports_it_in():
    from backend.bl_haos.ha.player import node_rate_from_props

    # A plain property, a fraction string, and the format parameter.
    assert node_rate_from_props({"audio.rate": 48000}, {}) == 48000
    assert node_rate_from_props({"node.rate": "1/44100"}, {}) == 44100
    assert node_rate_from_props({}, {"info": {"params": {"Format": [{"format": {"rate": 44100}}]}}}) == 44100
    assert node_rate_from_props({}, {"info": {"params": {"Format": [{"rate": 48000}]}}}) == 48000
    # Nothing usable is reported as nothing, never as a guess.
    assert node_rate_from_props({}, {}) is None
    assert node_rate_from_props({"audio.rate": 0}, {}) is None
    assert node_rate_from_props({"audio.rate": "unknown"}, {}) is None
    assert node_rate_from_props(None, None) is None


@pytest.mark.asyncio
async def test_playback_log_names_the_codec_the_rate_and_the_buffer(caplog):
    """One read-only line answers "why does it sound like that?".

    The codec says which encoder is running, the rate says whether anything is
    converting the samples, and the buffer says how much slack the player holds.
    """
    graph = [{
        "id": 42,
        "props": {
            "media.class": "Audio/Sink",
            "node.name": "bluez_output.10_22_33_44_55_66.1",
            "device.description": "10:22:33:44:55:66",
            "api.bluez5.codec": "ldac",
            "audio.rate": 44100,
        },
    }]

    async def process_factory(*args, **_kwargs):
        if args[0] == "pw-dump":
            return ProbeProcess(json.dumps(graph).encode())
        return FakeProcess()

    bridge = MediaPlayerBridge(process_factory=process_factory)

    with caplog.at_level("INFO", logger="bl_haos.ha.player"):
        await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    assert "using A2DP codec ldac" in caplog.text
    assert "at 44100 Hz (resampling from 48000 Hz)" in caplog.text
    assert f"buffer {bridge.player_settings.latency_msec} ms" in caplog.text


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
    assert MediaPlayerBridge._text("Artist: Album") == "Artist: Album"
    assert MediaPlayerBridge._text("javascript:secret") is None
    assert MediaPlayerBridge._text("title with DEL\x7f control") is None
    assert MediaPlayerBridge._text("title with C1\x85 control") is None


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


@pytest.mark.asyncio
async def test_players_ask_the_stream_for_a_high_quality_resampler():
    """PipeWire's own resampler default (4 of 15) must not be what converts audio.

    Matching the link's rate means conversion usually happens in ffmpeg instead,
    but a speaker that negotiates 44.1 kHz while a 48 kHz source plays still gets
    converted by the server - at its default quality unless the stream says
    otherwise. Both transports are told.
    """
    calls = []

    async def process_factory(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    await bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    pw_play_args = calls[1]
    assert "--properties" in pw_play_args
    properties = json.loads(pw_play_args[pw_play_args.index("--properties") + 1])
    assert properties["resample.quality"] == bridge.player_settings.resample_quality
    assert bridge.player_settings.resample_quality > 4, "the server default is 4 of 15"

    calls.clear()

    async def pulse_sink(_address):
        return "pulse:bluez_sink.10_22_33_44_55_66.a2dp_sink"

    pulse_bridge = MediaPlayerBridge(sink_resolver=pulse_sink, process_factory=process_factory)
    await pulse_bridge.play_url("10:22:33:44:55:66", "https://example.test/audio.mp3")

    paplay_args = calls[1]
    assert f"--property=resample.quality={pulse_bridge.player_settings.resample_quality}" in paplay_args


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


@pytest.mark.asyncio
async def test_codec_override_skips_auto_without_probing_for_it():
    """`auto` means "negotiate the best", so there is nothing to pin.

    It used to be treated as a codec name: the bridge listed the card's profiles
    looking for one called "auto" and warned once per playback when it found none.
    """
    from backend.bl_haos.config import ConfigStore

    store = ConfigStore(config_file=os.path.join(os.path.dirname(__file__), "..", ".tmp-codec-auto.json"))
    try:
        store.update_speaker("10:22:33:44:55:66", codec_override="auto")
        calls = []

        async def process_factory(*args, **kwargs):
            calls.append(args)
            return FakeProcess(returncode=0)

        bridge = MediaPlayerBridge(config_store=store, sink_resolver=fake_sink, process_factory=process_factory)
        await bridge._apply_codec_override("10:22:33:44:55:66")

        assert calls == [], "no profile listing and no warning for the default"
    finally:
        store.file_path.unlink(missing_ok=True)


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


def _queue_test_bridge(rng=None):
    spawned = []
    playback_processes = []

    async def process_factory(*args, **kwargs):
        spawned.append(args)
        process = FakeProcess(returncode=0 if args[0] not in {"ffmpeg", "pw-play", "paplay"} else None)
        if args[0] in {"ffmpeg", "pw-play", "paplay"}:
            playback_processes.append(process)
        return process

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory, rng=rng)
    bridge.warmup_attempts = 0
    return bridge, spawned, playback_processes


async def _wait_for_queue_condition(condition, timeout=1.0):
    async def wait():
        while not condition():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)


async def _cleanup_queue_test(bridge):
    await bridge.async_shutdown()
    if bridge._background_tasks:
        await asyncio.gather(*tuple(bridge._background_tasks), return_exceptions=True)
    await asyncio.sleep(0)
    assert not bridge._background_tasks


@pytest.mark.asyncio
async def test_queue_start_uses_normalized_speaker_and_exposes_only_sanitized_state():
    bridge, spawned, processes = _queue_test_bridge()
    address = "10-22-33-44-55-66"
    secret_url = "https://media.example.test/first.mp3?authSig=queue-secret"

    snapshot = await bridge.start_queue(
        address,
        "Morning set",
        [{"url": secret_url, "title": "First track", "artist": "Artist"}],
    )

    current = bridge.get_queue("10:22:33:44:55:66")
    assert current is not None
    for field in ("status", "playlist", "title", "artist", "duration", "index", "count", "shuffle", "repeat", "error"):
        assert current.get(field) == snapshot.get(field)
    assert isinstance(snapshot["position"], (int, float))
    assert isinstance(current["position"], (int, float))
    assert abs(current["position"] - snapshot["position"]) < 0.1
    assert snapshot["status"] == "playing"
    assert snapshot["playlist"] == "Morning set"
    assert snapshot["title"] == "First track"
    assert snapshot["artist"] == "Artist"
    assert snapshot["index"] == snapshot["count"] == 1
    assert len(processes) == 2
    assert [args[0] for args in spawned if args[0] in {"ffmpeg", "pw-play"}] == ["ffmpeg", "pw-play"]
    assert secret_url not in str(snapshot)
    assert "authSig" not in str(snapshot)
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_advances_only_when_both_children_exit_zero():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Two tracks", [
        {"url": "https://media.example.test/first.mp3", "title": "First"},
        {"url": "https://media.example.test/second.mp3", "title": "Second"},
    ])
    first_pair = tuple(processes[:2])
    first_pair[0].returncode = 0
    first_pair[1].returncode = 0

    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["index"] == 2
    )

    snapshot = bridge.get_queue(address)
    assert snapshot["index"] == 2
    assert snapshot["title"] == "Second"
    assert snapshot["status"] == "playing"
    assert len(processes) == 4
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
@pytest.mark.parametrize("decoder_code,player_code", [(0, 1), (1, 0), (-15, 0), (255, 0)])
async def test_queue_failure_retains_current_item_without_advancing(decoder_code, player_code):
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Two tracks", [
        {"url": "https://media.example.test/failed.mp3?token=never-show", "title": "Failed item"},
        {"url": "https://media.example.test/next.mp3", "title": "Must not start"},
    ])
    first_pair = tuple(processes[:2])
    first_pair[0].returncode = decoder_code
    first_pair[1].returncode = player_code

    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["status"] == "failed"
    )

    snapshot = bridge.get_queue(address)
    assert snapshot["status"] == "failed"
    assert snapshot["title"] == "Failed item"
    assert snapshot["index"] == 1
    assert snapshot["error"] == "Playback failed"
    assert len(processes) == 2
    assert "never-show" not in str(snapshot)
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_replacement_fences_old_process_watcher():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Old", [
        {"url": "https://media.example.test/old.mp3", "title": "Old item"},
        {"url": "https://media.example.test/old-next.mp3", "title": "Old next"},
    ])
    old_pair = tuple(processes[:2])

    await bridge.start_queue(address, "Replacement", [
        {"url": "https://media.example.test/new.mp3", "title": "New item"},
    ])
    old_pair[0].returncode = 0
    old_pair[1].returncode = 0
    await bridge._watch_processes(address, *old_pair)

    snapshot = bridge.get_queue(address)
    assert snapshot["playlist"] == "Replacement"
    assert snapshot["title"] == "New item"
    assert snapshot["index"] == snapshot["count"] == 1
    assert len(processes) == 4
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_stop_and_standalone_play_clear_queued_work():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Queued", [
        {"url": "https://media.example.test/queued.mp3", "title": "Queued item"},
        {"url": "https://media.example.test/tail.mp3", "title": "Queued tail"},
    ])

    await bridge.execute(address, "stop")
    assert bridge.get_queue(address) is None
    assert len(processes) == 2
    with pytest.raises(MediaPlayerError, match="No active playback to resume"):
        await bridge.execute(address, "play")
    assert len(processes) == 2

    await bridge.start_queue(address, "Replaced", [
        {"url": "https://media.example.test/replaced.mp3", "title": "Replaced item"},
    ])
    await bridge.play_url(address, "https://media.example.test/single.mp3")

    assert bridge.get_queue(address) is None
    assert bridge.get_state(address) == "playing"
    assert len(processes) == 6
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_invalid_replacement_preserves_current_session_and_process_pair():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Current", [
        {"url": "https://media.example.test/current.mp3", "title": "Current item"},
    ])
    current_pair = bridge.active_processes[address]

    with pytest.raises(MediaPlayerError, match="Queue request is invalid"):
        await bridge.start_queue(address, "Rejected", [
            {"url": "file:///not-allowed.mp3", "title": "Must not replace"},
        ])

    assert bridge.get_queue(address)["playlist"] == "Current"
    assert bridge.active_processes[address] == current_pair
    assert len(processes) == 2
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_completion_retains_snapshot_and_clears_playable_urls():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    secret_url = "https://media.example.test/complete.mp3?token=completed-secret"
    await bridge.start_queue(address, "Complete", [
        {"url": secret_url, "title": "Completed item"},
    ])
    pair = tuple(processes[:2])
    pair[0].returncode = 0
    pair[1].returncode = 0

    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["status"] == "completed"
    )

    snapshot = bridge.get_queue(address)
    assert snapshot["status"] == "completed"
    assert snapshot["title"] == "Completed item"
    assert snapshot["index"] == snapshot["count"] == 1
    assert secret_url not in str(snapshot)
    assert all(not item.url for item in bridge.playback_sessions[address].items)
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_partial_process_spawn_is_cleaned_up(caplog):
    spawned_decoder = None

    async def process_factory(*args, **kwargs):
        nonlocal spawned_decoder
        if args[0] == "ffmpeg":
            spawned_decoder = FakeProcess()
            return spawned_decoder
        if args[0] == "pw-play":
            raise OSError("private spawn detail")
        return FakeProcess(returncode=0)

    bridge = MediaPlayerBridge(sink_resolver=fake_sink, process_factory=process_factory)
    bridge.warmup_attempts = 0

    snapshot = await bridge.start_queue("10:22:33:44:55:66", "Partial", [
        {"url": "https://media.example.test/partial.mp3", "title": "Partial"},
    ])

    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "Playback failed"
    assert spawned_decoder is not None and spawned_decoder.returncode is not None
    assert bridge.active_processes == {}
    assert "private spawn detail" not in caplog.text
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_start_bounds_item_count_and_aggregate_payload_before_replacement():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Current", [
        {"url": "https://media.example.test/current.mp3", "title": "Current item"},
    ])
    current_pair = bridge.active_processes[address]
    too_many = [
        {"url": f"https://media.example.test/{index}.mp3", "title": "Track"}
        for index in range(251)
    ]
    too_large = [
        {"url": f"https://media.example.test/{'x' * 1100}", "title": "Track"}
        for _ in range(250)
    ]

    with pytest.raises(MediaPlayerError, match="Queue request is invalid"):
        await bridge.start_queue(address, "Too many", too_many)
    with pytest.raises(MediaPlayerError, match="Queue request is too large"):
        await bridge.start_queue(address, "Too large", too_large)

    assert bridge.get_queue(address)["playlist"] == "Current"
    assert bridge.active_processes[address] == current_pair
    assert len(processes) == 2
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_next_is_explicit_skip_and_previous_obeys_three_second_rule():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Navigation", [
        {"url": "https://media.example.test/one.mp3", "title": "One"},
        {"url": "https://media.example.test/two.mp3", "title": "Two"},
        {"url": "https://media.example.test/three.mp3", "title": "Three"},
    ])

    await bridge.control_queue(address, "next")
    assert bridge.get_queue(address)["title"] == "Two"
    bridge.timelines[address]["position"] = 4.0
    bridge.timelines[address]["clock_started_at"] = None
    await bridge.control_queue(address, "previous")
    assert bridge.get_queue(address)["title"] == "Two"
    assert bridge.get_timeline(address)["position"] == 0

    bridge.timelines[address]["position"] = 2.0
    await bridge.control_queue(address, "previous")
    assert bridge.get_queue(address)["title"] == "One"
    await bridge.control_queue(address, "previous")
    assert bridge.get_queue(address)["title"] == "One"
    assert len(processes) == 10
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_shuffle_keeps_current_item_and_only_reorders_tail():
    class ReverseShuffle:
        @staticmethod
        def shuffle(items):
            items.reverse()

    bridge, _spawned, _processes = _queue_test_bridge(rng=ReverseShuffle())
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Shuffle", [
        {"url": f"https://media.example.test/{name}.mp3", "title": name}
        for name in ("One", "Two", "Three", "Four")
    ])

    await bridge.control_queue(address, "set_shuffle", shuffle=True)

    session = bridge.playback_sessions[address]
    assert session.items[session.index].title == "One"
    assert [item.title for item in session.items] == ["One", "Four", "Three", "Two"]
    assert bridge.get_queue(address)["shuffle"] is True
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_repeat_one_restarts_only_after_success_and_repeat_all_wraps():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Repeat", [
        {"url": "https://media.example.test/one.mp3", "title": "One"},
        {"url": "https://media.example.test/two.mp3", "title": "Two"},
    ])
    await bridge.control_queue(address, "set_repeat", repeat="one")

    first_pair = tuple(processes[:2])
    first_pair[0].returncode = 0
    first_pair[1].returncode = 0
    await _wait_for_queue_condition(lambda: len(processes) == 4)
    assert bridge.get_queue(address)["title"] == "One"
    assert bridge.get_queue(address)["repeat"] == "one"
    assert len(processes) == 4

    await bridge.control_queue(address, "set_repeat", repeat="all")
    second_pair = tuple(processes[2:4])
    second_pair[0].returncode = 0
    second_pair[1].returncode = 0
    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["index"] == 2
    )
    assert bridge.get_queue(address)["title"] == "Two"
    third_pair = tuple(processes[4:6])
    third_pair[0].returncode = 0
    third_pair[1].returncode = 0
    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["index"] == 1 and len(processes) == 8
    )
    assert bridge.get_queue(address)["title"] == "One"
    assert len(processes) == 8
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_failure_requires_explicit_play_retry_or_next_skip():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Recovery", [
        {"url": "https://media.example.test/failed.mp3", "title": "Failed"},
        {"url": "https://media.example.test/next.mp3", "title": "Next"},
    ])
    pair = tuple(processes[:2])
    pair[0].returncode = 0
    pair[1].returncode = 2
    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["status"] == "failed"
    )

    assert bridge.get_queue(address)["status"] == "failed"
    assert len(processes) == 2
    await bridge.control_queue(address, "play")
    assert bridge.get_queue(address)["status"] == "playing"
    assert bridge.get_queue(address)["title"] == "Failed"
    assert len(processes) == 4

    retry_pair = tuple(processes[2:4])
    retry_pair[0].returncode = 3
    retry_pair[1].returncode = 0
    await _wait_for_queue_condition(
        lambda: (queue := bridge.get_queue(address)) is not None and queue["status"] == "failed"
    )
    await bridge.control_queue(address, "next")
    assert bridge.get_queue(address)["title"] == "Next"
    assert bridge.get_queue(address)["status"] == "playing"
    assert len(processes) == 6
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_pause_resume_stop_and_shutdown_do_not_advance():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Lifecycle", [
        {"url": "https://media.example.test/one.mp3", "title": "One"},
        {"url": "https://media.example.test/two.mp3", "title": "Two"},
    ])

    await bridge.control_queue(address, "pause")
    assert bridge.get_queue(address)["status"] == "paused"
    await bridge.control_queue(address, "play")
    assert bridge.get_queue(address)["status"] == "playing"
    assert len(processes) == 2

    pair = tuple(processes[:2])
    await bridge.execute(address, "stop")
    pair[0].returncode = 0
    pair[1].returncode = 0
    await bridge._watch_processes(address, *pair)
    assert bridge.get_queue(address) is None
    assert len(processes) == 2

    await bridge.start_queue(address, "Shutdown", [
        {"url": "https://media.example.test/one.mp3", "title": "One"},
    ])
    await bridge.async_shutdown()
    assert bridge.get_queue(address) is None
    assert bridge.playback_sessions == {}


@pytest.mark.asyncio
async def test_queue_disconnect_invalidates_generation_before_late_completion():
    bridge, _spawned, processes = _queue_test_bridge()
    address = "10:22:33:44:55:66"
    await bridge.start_queue(address, "Disconnect", [
        {"url": "https://media.example.test/one.mp3", "title": "One"},
        {"url": "https://media.example.test/two.mp3", "title": "Two"},
    ])
    pair = tuple(processes[:2])

    bridge.unregister_keepalive(address)
    pair[0].returncode = 0
    pair[1].returncode = 0
    await bridge._watch_processes(address, *pair)

    assert bridge.get_queue(address) is None
    assert len(processes) == 2
    await _cleanup_queue_test(bridge)


@pytest.mark.asyncio
async def test_queue_invalidation_normalizes_blueZ_address_casing():
    bridge, _spawned, _processes = _queue_test_bridge()
    address = "aa:bb:cc:dd:ee:66"
    await bridge.start_queue(address, "Normalize", [
        {"url": "https://media.example.test/item.mp3", "title": "Item"},
    ])
    generation = bridge._playback_generations[address]

    bridge._invalidate_playback_session("AA-BB-CC-DD-EE-66")

    assert bridge.get_queue(address) is None
    assert bridge._playback_generations[address] == generation + 1
    await _cleanup_queue_test(bridge)

