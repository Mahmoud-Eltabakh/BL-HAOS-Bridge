import asyncio

import pytest
from backend.bl_haos.ha.player import MediaPlayerBridge


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
