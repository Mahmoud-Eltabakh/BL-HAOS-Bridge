"""Supervise native media playback for Bluetooth PipeWire sinks."""

import asyncio
import json
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any, Optional
from urllib.parse import urlsplit

from ..config import ConfigStore

logger = logging.getLogger("bl_haos.ha.player")


class MediaPlayerError(RuntimeError):
    """Raised when an add-on playback operation cannot be completed."""


class MediaPlayerBridge:
    def __init__(
        self,
        config_store: Optional[ConfigStore] = None,
        sink_resolver: Optional[Callable[[str], Awaitable[str | None]]] = None,
        process_factory: Optional[Callable[..., Awaitable[asyncio.subprocess.Process]]] = None,
        state_callback: Optional[Callable[[str], Awaitable[None] | None]] = None,
    ):
        self.config_store = config_store
        self.states: dict[str, str] = {}
        self.volumes: dict[str, float] = {}
        self.active_processes: dict[str, tuple[asyncio.subprocess.Process, asyncio.subprocess.Process]] = {}
        self._sink_resolver = sink_resolver or self._async_resolve_sink
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._state_callback = state_callback
        if config_store:
            for address, speaker in config_store.settings.speakers.items():
                self.volumes[self._address(address)] = speaker.default_volume / 100

    @staticmethod
    def _address(address: str) -> str:
        return address.strip().lower().replace("-", ":")

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
                raise MediaPlayerError("No active playback to resume")
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
            await self._apply_volume(address, volume)
            self.volumes[address] = volume
            if self.config_store:
                self.config_store.update_speaker(address, default_volume=round(volume * 100))
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
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or any(ord(character) < 32 for character in url)
        ):
            raise MediaPlayerError("Media URL must be a safe HTTP(S) URL")
        sink = await self._sink_resolver(addr)
        if not sink:
            raise MediaPlayerError("Connected PipeWire A2DP sink is unavailable")
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
        self.states[addr] = "playing"
        if hasattr(decoder.stdout, "read") and hasattr(player.stdin, "write"):
            asyncio.create_task(self._pipe_audio(decoder, player))
        asyncio.create_task(self._watch_processes(addr, decoder, player))
        await self._notify(addr)

    async def set_volume(self, address: str, volume: float) -> None:
        """Set volume level (0.0 - 1.0)."""
        await self.execute(address, "set_volume", volume=volume)

    async def _async_resolve_sink(self, address: str) -> str | None:
        process = await self._process_factory("pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        if process.returncode:
            return None
        try:
            graph = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            return None
        address_key = address.replace(":", "_")
        for node in graph:
            props = node.get("info", {}).get("props", {})
            values = " ".join(str(value).lower() for value in props.values())
            if props.get("media.class") == "Audio/Sink" and (address in values or address_key in values):
                return props.get("node.name")
        return None

    async def _apply_volume(self, address: str, volume: float) -> None:
        sink = await self._sink_resolver(address)
        if not sink:
            raise MediaPlayerError("Connected PipeWire A2DP sink is unavailable")
        process = await self._process_factory("wpctl", "set-volume", sink, str(volume))
        await process.wait()
        if process.returncode:
            raise MediaPlayerError("Unable to apply PipeWire volume")

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
        for address in tuple(self.active_processes):
            await self._stop_processes(address)
            self.states[address] = "idle"
