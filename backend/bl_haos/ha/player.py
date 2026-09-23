"""Supervise native media playback for Bluetooth PipeWire sinks."""

import asyncio
import json
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from ..config import ConfigStore
from ..health import FailureClass, HealthRegistry, HealthState, normalize_address, validate_identifier, validate_media_url

logger = logging.getLogger("bl_haos.ha.player")


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
        if operation == "play":
            if address not in self.active_processes:
                last_url = self.last_urls.get(address)
                if not last_url:
                    raise MediaPlayerError("No active playback to resume")
                await self.play_url(address, last_url)
                return
            await self._signal_processes(address, getattr(signal, "SIGCONT", signal.SIGTERM))
            self.states[address] = "playing"
        elif operation == "pause":
            if address not in self.active_processes:
                raise MediaPlayerError("No active playback to pause")
            await self._signal_processes(address, getattr(signal, "SIGSTOP", signal.SIGTERM))
            self.states[address] = "paused"
        elif operation == "stop":
            await self._stop_processes(address)
            self.states[address] = "idle"
        elif operation == "set_volume":
            if volume is None or not 0 <= volume <= 1:
                raise MediaPlayerError("Volume must be between 0.0 and 1.0")
            self.volumes[address] = volume
            if self.config_store:
                self.config_store.update_speaker(address, default_volume=round(volume * 100))
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
        sink = await self._sink_resolver(addr)
        if not sink:
            if self.health:
                self.health.observe_component(
                    "pipewire", HealthState.UNAVAILABLE, failure=FailureClass.SINK_MISSING,
                    detail="No matching A2DP sink", source="pw-dump"
                )
            raise MediaPlayerError("Connected PipeWire A2DP sink is unavailable")
        try:
            sink = validate_identifier(sink, "PipeWire sink")
        except ValueError as error:
            raise MediaPlayerError("Connected PipeWire sink is invalid") from error
        if self.health:
            self.health.observe_component("pipewire", HealthState.HEALTHY, source="pw-dump")
        await self._stop_processes(addr)
        try:
            decoder = await self._process_factory(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", url,
                "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            player = await self._process_factory(
                "pw-play", "--target", sink, "--raw", "--rate", "48000", "--channels", "2", "-",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, asyncio.SubprocessError) as error:
            raise MediaPlayerError("Unable to start media playback") from error
        self.active_processes[addr] = (decoder, player)
        self.last_urls[addr] = url
        self.states[addr] = "playing"
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
            sink = validate_identifier(sink, "PipeWire sink")
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
                "pw-play", "--target", sink, "--raw", "--rate", "48000", "--channels", "2", "-",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, asyncio.SubprocessError) as error:
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
                return None
            graph = json.loads(stdout)
        except Exception:
            if self.health:
                self.health.observe_component(
                    "pipewire", HealthState.UNAVAILABLE,
                    failure=FailureClass.PIPEWIRE_UNAVAILABLE,
                    detail="pw-dump probe unavailable", source="pw-dump"
                )
            return None
        address_clean = address.strip().lower()
        address_key = address_clean.replace(":", "_")
        for node in graph:
            props = {**node.get("props", {}), **node.get("info", {}).get("props", {})}
            values = " ".join(str(value).lower() for value in props.values())
            media_class = props.get("media.class", "")
            if media_class == "Audio/Sink" or "sink" in media_class.lower():
                if address_clean in values or address_key in values:
                    return props.get("node.name") or str(node.get("id"))
        return None

    async def _apply_volume(self, address: str, volume: float) -> None:
        sink = await self._sink_resolver(address)
        if sink:
            try:
                sink = validate_identifier(sink, "PipeWire sink")
            except ValueError:
                logger.debug("Volume update skipped for invalid sink")
                return
            try:
                process = await self._process_factory("wpctl", "set-volume", sink, str(volume))
                await process.wait()
            except Exception as e:
                logger.debug("wpctl set-volume notice for %s (%s): %s", address, sink, e)

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
            self.active_processes.pop(address, None)
            self.states[address] = "idle"
            await self._notify(address)

    async def async_shutdown(self) -> None:
        await self.stop_keepalive()
        for address in tuple(self.active_processes):
            await self._stop_processes(address)
            self.states[address] = "idle"
