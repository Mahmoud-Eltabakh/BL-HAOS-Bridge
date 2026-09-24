"""Supervise native media playback for Bluetooth PipeWire sinks."""

import asyncio
import json
import logging
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..config import ConfigStore
from ..health import FailureClass, HealthRegistry, HealthState, normalize_address, validate_identifier, validate_media_url

logger = logging.getLogger("bl_haos.ha.player")
PULSE_SOCKET = "/run/audio/pulse.sock"
PULSE_SERVER = f"unix:{PULSE_SOCKET}"


class MediaPlayerError(RuntimeError):
    """Raised when an add-on playback operation cannot be completed."""


class MediaPlayerBridge:
    def __init__(
        self,
        config_store: ConfigStore | None = None,
        sink_resolver: Callable[[str], Awaitable[str | None]] | None = None,
        process_factory: Callable[..., Awaitable[asyncio.subprocess.Process]] | None = None,
        state_callback: Callable[[str], Awaitable[None] | None] | None = None,
        health_registry: HealthRegistry | None = None,
    ):
        self.config_store = config_store
        self.states: dict[str, str] = {}
        self.volumes: dict[str, float] = {}
        self.active_processes: dict[str, tuple[asyncio.subprocess.Process, asyncio.subprocess.Process]] = {}
        self.last_urls: dict[str, str] = {}
        self.keepalive_addresses: set[str] = set()
        self.keepalive_interval: float = 240.0
        self.keepalive_pulse_duration: float = 1.0
        self._keepalive_task: asyncio.Task | None = None
        self._sink_resolver = sink_resolver or self._async_resolve_sink
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._state_callback = state_callback
        self.health = health_registry
        if config_store:
            for address, speaker in config_store.settings.speakers.items():
                try:
                    normalized = self._address(address)
                except ValueError:
                    logger.warning("Ignoring invalid persisted speaker identifier")
                    continue
                self.volumes[normalized] = speaker.default_volume / 100

    @staticmethod
    def _address(address: str) -> str:
        return normalize_address(address)

    def get_state(self, address: str) -> str:
        return self.states.get(self._address(address), "idle")

    def get_volume(self, address: str) -> float:
        return self.volumes.get(self._address(address), 0.70)

    async def _notify(self, address: str) -> None:
        if self._state_callback:
            result = self._state_callback(address)
            if result is not None:
                await result

    async def execute(self, address: str, operation: str, *, volume: float | None = None, url: str | None = None) -> None:
        """Execute one validated native operation and publish only confirmed state."""
        address = self._address(address)
        logger.debug(
            "Executing player operation '%s' for speaker %s (volume=%s, url=%s)",
            operation,
            address,
            volume,
            url,
        )
        if operation == "play":
            if address not in self.active_processes:
                last_url = self.last_urls.get(address)
                if not last_url:
                    logger.debug("Playback resume failed for %s: no active or previous URL", address)
                    raise MediaPlayerError("No active playback to resume")
                await self.play_url(address, last_url)
                return
            logger.debug("Resuming playback processes for %s (SIGCONT)", address)
            await self._signal_processes(address, getattr(signal, "SIGCONT", signal.SIGTERM))
            self.states[address] = "playing"
        elif operation == "pause":
            if address not in self.active_processes:
                logger.debug("Pause operation failed for %s: no active playback process", address)
                raise MediaPlayerError("No active playback to pause")
            logger.debug("Pausing playback processes for %s (SIGSTOP)", address)
            await self._signal_processes(address, getattr(signal, "SIGSTOP", signal.SIGTERM))
            self.states[address] = "paused"
        elif operation == "stop":
            logger.debug("Stopping playback processes for %s", address)
            await self._stop_processes(address)
            self.states[address] = "idle"
        elif operation == "set_volume":
            if volume is None or not 0 <= volume <= 1:
                raise MediaPlayerError("Volume must be between 0.0 and 1.0")
            self.volumes[address] = volume
            if self.config_store:
                self.config_store.update_speaker(address, default_volume=round(volume * 100))
            logger.debug("Setting volume for %s to %s", address, volume)
            await self._apply_volume(address, volume)
        elif operation == "play_media":
            if not url:
                raise MediaPlayerError("Media URL is required")
            await self.play_url(address, url)
            return
        else:
            raise MediaPlayerError("Unsupported media player operation")
        await self._notify(address)

    async def handle_command(self, address: str, command: str) -> None:
        """Adapt an optional legacy command topic to the native operation dispatcher."""
        cmd = command.strip()
        addr = self._address(address)
        logger.info("Received command for %s", addr)

        if cmd == "PLAY":
            await self.execute(addr, "play")
        elif cmd == "PAUSE":
            await self.execute(addr, "pause")
        elif cmd == "STOP":
            await self.execute(addr, "stop")
        elif cmd.startswith("PLAY_MEDIA:"):
            await self.execute(addr, "play_media", url=cmd[len("PLAY_MEDIA:"):])
        elif cmd.startswith("VOLUME:"):
            try:
                await self.execute(addr, "set_volume", volume=float(cmd[len("VOLUME:"):]))
            except ValueError:
                raise MediaPlayerError("Volume must be numeric")

    async def play_url(self, address: str, url: str) -> None:
        """Decode one validated URL and route it to the selected A2DP sink."""
        addr = self._address(address)
        try:
            url = validate_media_url(url)
        except ValueError as error:
            raise MediaPlayerError("Media URL must be a safe HTTP(S) URL") from error
        logger.debug("Resolving audio sink for %s...", addr)
        sink = await self._sink_resolver(addr)
        if not sink:
            logger.debug("No audio sink found for %s", addr)
            if self.health:
                failure = FailureClass.SINK_MISSING
                detail = "No matching PipeWire or host PulseAudio A2DP sink"
                speaker = self.health.speakers.get(addr)
                if speaker and speaker.state.value == "connected":
                    failure = FailureClass.SINK_UNAVAILABLE_TRANSPORT_HELD
                    detail = "Connected BlueZ device has no PipeWire or host PulseAudio A2DP sink; restart the add-on to disarm host Bluetooth discovery"
                self.health.observe_component(
                    "pipewire", HealthState.UNAVAILABLE, failure=failure,
                    detail=detail, source="pw-dump+pactl"
                )
            raise MediaPlayerError("Connected Bluetooth audio sink is unavailable")
        try:
            transport, sink_name = self._parse_sink(sink)
        except ValueError as error:
            label = "PulseAudio" if sink.startswith("pulse:") else "PipeWire"
            raise MediaPlayerError(f"Connected {label} sink is invalid") from error
        logger.debug("Resolved sink '%s' via %s transport for speaker %s", sink_name, transport, addr)
        if self.health:
            self.health.observe_component("pipewire", HealthState.HEALTHY, source=transport)
        await self._stop_processes(addr)
        try:
            logger.debug("Spawning ffmpeg decoder and %s player for %s", transport, addr)
            decoder = await self._process_factory(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", url,
                "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            player = await self._process_factory(
                *self._player_command(transport, sink_name),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, **self._player_environment(transport),
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.debug("Failed to spawn playback processes for %s: %s", addr, error)
            raise MediaPlayerError("Unable to start media playback") from error
        self.active_processes[addr] = (decoder, player)
        self.last_urls[addr] = url
        self.states[addr] = "playing"
        logger.debug(
            "Playback started for %s (decoder PID=%s, player PID=%s)",
            addr,
            getattr(decoder, "pid", None),
            getattr(player, "pid", None),
        )
        if hasattr(decoder.stdout, "read") and hasattr(player.stdin, "write"):
            asyncio.create_task(self._pipe_audio(decoder, player))
        asyncio.create_task(self._watch_processes(addr, decoder, player))
        await self._notify(addr)

    async def set_volume(self, address: str, volume: float) -> None:
        """Set volume level (0.0 - 1.0)."""
        await self.execute(address, "set_volume", volume=volume)

    def register_keepalive(self, address: str) -> None:
        """Track a connected speaker so its BT radio is periodically nudged awake."""
        self.keepalive_addresses.add(self._address(address))

    def unregister_keepalive(self, address: str) -> None:
        """Stop nudging a speaker that is no longer connected/trusted."""
        self.keepalive_addresses.discard(self._address(address))

    async def start_keepalive(self) -> None:
        """Start the background low-duty-cycle keep-alive loop."""
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def stop_keepalive(self) -> None:
        """Stop the keep-alive loop."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        """Send a brief silent pulse to idle speakers so they don't auto-sleep."""
        try:
            while True:
                await asyncio.sleep(self.keepalive_interval)
                for address in list(self.keepalive_addresses):
                    await self._send_keepalive_pulse(address)
        except asyncio.CancelledError:
            pass

    async def _send_keepalive_pulse(self, address: str) -> None:
        """Play a short, silent PCM burst to the sink without touching playback state."""
        address = self._address(address)
        if address in self.active_processes:
            return  # already streaming real audio; no nudge needed
        sink = await self._sink_resolver(address)
        if not sink:
            return
        try:
            transport, sink_name = self._parse_sink(sink)
        except ValueError:
            logger.debug("Keep-alive pulse skipped for invalid sink")
            return
        try:
            source = await self._process_factory(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                "-i", "anullsrc=r=48000:cl=stereo", "-t", str(self.keepalive_pulse_duration),
                "-f", "s16le", "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            player = await self._process_factory(
                *self._player_command(transport, sink_name),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, **self._player_environment(transport),
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.debug("Keep-alive pulse failed to start for %s: %s", address, error)
            return
        if hasattr(source.stdout, "read") and hasattr(player.stdin, "write"):
            await self._pipe_audio(source, player)
        for process in (source, player):
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.keepalive_pulse_duration + 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

    async def _async_resolve_sink(self, address: str) -> str | None:
        graph = []
        try:
            process = await self._process_factory("pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await process.communicate()
            if process.returncode:
                if self.health:
                    self.health.observe_component(
                        "pipewire", HealthState.UNAVAILABLE,
                        failure=FailureClass.PIPEWIRE_UNAVAILABLE,
                        detail="pw-dump probe failed", source="pw-dump"
                    )
            else:
                graph = json.loads(stdout)
        except Exception:
            if self.health:
                self.health.observe_component(
                    "pipewire", HealthState.UNAVAILABLE,
                    failure=FailureClass.PIPEWIRE_UNAVAILABLE,
                    detail="pw-dump probe unavailable", source="pw-dump"
                )
        address_clean = address.strip().lower()
        address_key = address_clean.replace(":", "_")
        for node in graph:
            props = {**node.get("props", {}), **node.get("info", {}).get("props", {})}
            values = " ".join(str(value).lower() for value in props.values())
            media_class = props.get("media.class", "")
            if media_class == "Audio/Sink" or "sink" in media_class.lower():
                if address_clean in values or address_key in values:
                    return props.get("node.name") or str(node.get("id"))
        if os.path.exists(PULSE_SOCKET):
            try:
                process = await self._process_factory(
                    "pactl", "-s", PULSE_SERVER, "list", "sinks", "short",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await process.communicate()
                expected = f"bluez_sink.{address_key}.a2dp_sink"
                if not process.returncode:
                    for line in self._decode_output(stdout).splitlines():
                        fields = line.split()
                        if len(fields) > 1 and fields[1].lower() == expected:
                            return f"pulse:{fields[1]}"
            except Exception:
                logger.debug("Host PulseAudio sink probe unavailable", exc_info=True)
        return None

    @staticmethod
    def _decode_output(output: bytes | str) -> str:
        return output.decode(errors="replace") if isinstance(output, bytes) else output

    @staticmethod
    def _parse_sink(sink: str) -> tuple[str, str]:
        if sink.startswith("pulse:"):
            return "pulse", validate_identifier(sink.removeprefix("pulse:"), "PulseAudio sink")
        return "pipewire", validate_identifier(sink, "PipeWire sink")

    @staticmethod
    def _player_command(transport: str, sink: str) -> tuple[str, ...]:
        if transport == "pulse":
            return ("paplay", "--device", sink, "--raw", "--rate", "48000", "--channels", "2", "--format=s16le", "-")
        return ("pw-play", "--target", sink, "--raw", "--rate", "48000", "--channels", "2", "-")

    @staticmethod
    def _player_environment(transport: str) -> dict[str, Any]:
        return {"env": {**os.environ, "PULSE_SERVER": PULSE_SERVER}} if transport == "pulse" else {}

    async def _apply_volume(self, address: str, volume: float) -> None:
        sink = await self._sink_resolver(address)
        if sink:
            try:
                transport, sink_name = self._parse_sink(sink)
            except ValueError:
                logger.debug("Volume update skipped for invalid sink")
                return
            try:
                if transport == "pulse":
                    process = await self._process_factory(
                        "pactl", "-s", PULSE_SERVER, "set-sink-volume", sink_name, f"{round(volume * 100)}%"
                    )
                else:
                    process = await self._process_factory("wpctl", "set-volume", sink_name, str(volume))
                await process.wait()
            except Exception as e:
                logger.debug("Sink volume update notice for %s (%s): %s", address, sink_name, e)

    async def _signal_processes(self, address: str, signal_number: signal.Signals) -> None:
        for process in self.active_processes[address]:
            if getattr(process, "pid", None) and os.name != "nt":
                os.killpg(process.pid, signal_number)
            else:
                process.send_signal(signal_number)

    async def _pipe_audio(self, decoder: asyncio.subprocess.Process, player: asyncio.subprocess.Process) -> None:
        """Copy decoded PCM without introducing a shell pipeline."""
        try:
            while chunk := await decoder.stdout.read(64 * 1024):
                player.stdin.write(chunk)
                await player.stdin.drain()
        except (BrokenPipeError, ConnectionError):
            pass
        finally:
            if not player.stdin.is_closing():
                player.stdin.close()
            if decoder.returncode is None:
                try:
                    decoder.terminate()
                except ProcessLookupError:
                    pass

    async def _stop_processes(self, address: str) -> None:
        processes = self.active_processes.pop(address, ())
        for process in processes:
            if process.returncode is None:
                process.terminate()
        for process in processes:
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

    async def _watch_processes(self, address: str, decoder: asyncio.subprocess.Process, player: asyncio.subprocess.Process) -> None:
        await asyncio.gather(decoder.wait(), player.wait(), return_exceptions=True)
        if self.active_processes.get(address) == (decoder, player):
            logger.debug(
                "Playback processes ended for %s (decoder code: %s, player code: %s)",
                address,
                decoder.returncode,
                player.returncode,
            )
            self.active_processes.pop(address, None)
            self.states[address] = "idle"
            await self._notify(address)

    async def async_shutdown(self) -> None:
        logger.debug("Shutting down MediaPlayerBridge...")
        await self.stop_keepalive()
        for address in tuple(self.active_processes):
            await self._stop_processes(address)
            self.states[address] = "idle"
