"""Bounded, local incident evidence; never performs recovery actions."""

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
from threading import Lock
import time
from typing import Callable

from .call_trace import trace_call
from .health import HealthRegistry

logger = logging.getLogger("bl_haos.incidents")
RETENTION_DAYS = 30
DAILY_BYTES = 2 * 1024 * 1024
SAMPLE_SECONDS = 5


class IncidentRecorder(logging.Handler):
    """Aggregate known backend failures and correlate sampled canonical health."""

    @trace_call
    def __init__(
        self, directory: Path, health: HealthRegistry, client_count: Callable[[], int],
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(logging.WARNING)
        self.directory = directory
        self.health = health
        self.client_count = client_count
        self.clock = clock
        self.counts: Counter[str] = Counter()
        self.previous: dict | None = None
        self.last_write = 0.0
        self.dropped = 0
        self.task: asyncio.Task | None = None
        self.storage_error: str | None = None
        self._storage_lock = asyncio.Lock()
        self._counts_lock = Lock()
        self._stopping = asyncio.Event()

    @trace_call
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("bl_haos.incidents"):
            return
        # Match locally, but never persist the message, arguments or traceback.
        message = record.getMessage().lower()
        patterns = (
            ("reconnect_exhausted", "tripping circuit breaker"),
            ("connection_busy", "in progress"),
            ("link_unreachable", "br-connection"),
            ("sink_missing", "sink"),
            ("playback_failure", "playback"),
        )
        category = next((key for key, marker in patterns if marker in message), "backend_warning")
        with self._counts_lock:
            self.counts[category] += 1

    @trace_call
    def _context(self) -> dict:
        snapshot = self.health.snapshot()
        return {
            "health": snapshot.status.value,
            "native_clients": self.client_count(),
            "components": {
                name: {"state": item.state.value,
                       "failure": item.failure.classification.value if item.failure else None}
                for name, item in snapshot.components.items()
            },
            "speakers": {
                hashlib.sha256(address.encode()).hexdigest()[:12]: {
                    "state": item.state.value, "disconnects": item.disconnects,
                    "connects": item.connects, "suppressed_flaps": item.suppressed_flaps,
                    "failure": item.failure.classification.value if item.failure else None,
                }
                for address, item in snapshot.speakers.items()
            },
        }

    @trace_call
    def _append(self, entry: dict) -> bool:
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        cutoff = now.date() - timedelta(days=RETENTION_DAYS - 1)
        for path in self.directory.glob("incidents-????-??-??.jsonl"):
            try:
                day = datetime.strptime(path.stem.removeprefix("incidents-"), "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
        target = self.directory / f"incidents-{now.date().isoformat()}.jsonl"
        line = (json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n").encode()
        size = target.stat().st_size if target.exists() else 0
        if size + len(line) > DAILY_BYTES:
            return False
        with target.open("ab") as stream:
            stream.write(line)
        target.chmod(0o600)
        return True

    @trace_call
    def _findings(self, context: dict, counts: dict[str, int]) -> list[str]:
        findings = list(counts)
        if context["native_clients"] == 0:
            findings.append("native_client_absent")
        if any(item["state"] != "connected" for item in context["speakers"].values()):
            findings.append("speaker_not_connected")
        if any(item["failure"] for item in context["components"].values()):
            findings.append("component_failure_observed")
        if self.previous is not None and context != self.previous:
            findings.append("health_or_connection_changed")
        return findings

    @trace_call
    async def sample(self, *, force: bool = False) -> None:
        async with self._storage_lock:
            context = self._context()
            with self._counts_lock:
                counts = dict(self.counts)
                self.counts.clear()
            now = self.clock()
            if not force and not counts and context == self.previous and now - self.last_write < 3600:
                return
            entry = {
                "version": 1, "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "kind": "observation" if self.previous is not None else "monitor_started",
                "patterns": counts, "context": context, "dropped_observations": self.dropped,
                "findings": self._findings(context, counts),
            }
            try:
                written = await asyncio.to_thread(self._append, entry)
            except OSError as error:
                self.storage_error = type(error).__name__
                self.dropped += 1
                logger.error("Incident storage failed: %s", self.storage_error)
                return
            if not written:
                self.dropped += 1
                if self.dropped == 1:
                    logger.warning("Incident daily storage limit reached; further observations will be dropped")
                return
            if self.storage_error:
                logger.info("Incident storage recovered")
            self.storage_error = None
            self.previous = context
            self.last_write = now

    @trace_call
    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=SAMPLE_SECONDS)
            except TimeoutError:
                await self.sample()

    @trace_call
    async def start(self) -> None:
        if self.task is not None:
            return
        self._stopping.clear()
        await self.sample(force=True)
        logging.getLogger("bl_haos").addHandler(self)
        self.task = asyncio.create_task(self._run(), name="bl-haos-incidents")
        self.task.add_done_callback(self._on_done)

    @trace_call
    def _on_done(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Incident monitor stopped unexpectedly: %s", type(task.exception()).__name__)

    @trace_call
    async def stop(self) -> None:
        logging.getLogger("bl_haos").removeHandler(self)
        if self.task is not None:
            self._stopping.set()
            await self.task
            self.task = None
        await self.sample(force=True)
        self.close()

    @trace_call
    def status(self) -> dict:
        return {
            "running": self.task is not None and not self.task.done(),
            "retention_days": RETENTION_DAYS, "daily_byte_limit": DAILY_BYTES,
            "sample_seconds": SAMPLE_SECONDS, "storage_error": self.storage_error,
            "dropped_observations": self.dropped,
        }

    @trace_call
    def _read_recent(self) -> list[dict]:
        # Read only a bounded tail of the latest daily file, never the archive.
        paths = sorted(self.directory.glob("incidents-????-??-??.jsonl"))
        if not paths:
            return []
        with paths[-1].open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 128 * 1024)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.read().splitlines()[-100:]
        return [json.loads(line) for line in lines]

    @trace_call
    async def recent(self) -> list[dict]:
        async with self._storage_lock:
            return await asyncio.to_thread(self._read_recent)
