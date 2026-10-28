"""Conservative scheduled reconnection for Bluetooth audio speakers."""

import asyncio
import logging
import math
import random
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .device import BluetoothOperationInProgress
from .manager import BluetoothManager
from .models import DeviceInfo
from ..constants import (
    EVENT_DEVICE_DISCOVERED,
    EVENT_DEVICE_UPDATED,
    RECONNECT_BACKOFF_JITTER,
    RECONNECT_BACKOFF_MULTIPLIER,
    RECONNECT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    RECONNECT_DISCONNECT_GRACE_SECONDS,
    RECONNECT_FLAP_COOLDOWN_SECONDS,
    RECONNECT_FLAP_LIMIT,
    RECONNECT_FLAP_WINDOW_SECONDS,
    RECONNECT_INITIAL_BACKOFF_SECONDS,
    RECONNECT_MAX_BACKOFF_SECONDS,
    RECONNECT_MAX_FAILURES_BEFORE_BREAKER,
    RECONNECT_MIN_BACKOFF_SECONDS,
    RECONNECT_POLL_INTERVAL_SECONDS,
    RECONNECT_POST_CONNECT_SETTLE_SECONDS,
)
from ..health import FailureClass, HealthRegistry, SpeakerState, normalize_address

from ..call_trace import trace_call

logger = logging.getLogger("bl_haos.bluetooth.reconnect")


class ReconnectState(str, Enum):
    IDLE = "idle"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    BACKOFF = "backoff"
    CIRCUIT_BROKEN = "circuit_broken"


class SpeakerReconnectProfile(BaseModel):
    address: str
    enabled: bool = True
    state: ReconnectState = ReconnectState.IDLE
    consecutive_failures: int = 0
    backoff_step: int = 0
    next_retry_time: float = 0.0
    circuit_broken_until: float = 0.0
    preferred_adapter: str | None = None
    # Stability bookkeeping, all wall-clock seconds. A dropout is only acted on
    # once it survives the grace window, a link that just came up is left alone for
    # the settle window, and repeated flaps earn a cooldown instead of a retry loop.
    disconnected_since: float | None = None
    connected_at: float = 0.0
    suppressed_flaps: int = 0
    flap_times: list[float] = Field(default_factory=list)
    cooldown_until: float = 0.0


class AutoReconnectEngine:
    @trace_call
    def __init__(
        self,
        manager: BluetoothManager,
        initial_backoff: float = RECONNECT_INITIAL_BACKOFF_SECONDS,
        backoff_multiplier: float = RECONNECT_BACKOFF_MULTIPLIER,
        max_backoff: float = RECONNECT_MAX_BACKOFF_SECONDS,
        max_failures_before_breaker: int = RECONNECT_MAX_FAILURES_BEFORE_BREAKER,
        circuit_breaker_cooldown: float = RECONNECT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        health_registry: HealthRegistry | None = None,
    ):
        """Accept finite initial >= 0, multiplier >= 1 and cap > 0 delays."""
        if not math.isfinite(initial_backoff) or initial_backoff < 0:
            raise ValueError("initial_backoff must be finite and non-negative")
        if not math.isfinite(backoff_multiplier) or backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be finite and at least one")
        if not math.isfinite(max_backoff) or max_backoff <= 0:
            raise ValueError("max_backoff must be finite and positive")
        self.manager = manager
        self.initial_backoff = initial_backoff
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff = max_backoff
        self.max_failures = max_failures_before_breaker
        self.breaker_cooldown = circuit_breaker_cooldown
        self.health = health_registry

        self.profiles: dict[str, SpeakerReconnectProfile] = {}
        self.globally_enabled = True
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._inflight: dict[str, asyncio.Task] = {}

        # Subscribe to BluetoothManager events
        self.manager.add_event_listener(self._handle_bluetooth_event)

    @trace_call
    def _release_inflight(self, address: str, task: asyncio.Task) -> None:
        """Release ownership only when the completed task is still current."""
        if self._inflight.get(address) is task:
            self._inflight.pop(address, None)

    @trace_call
    def register_speaker(self, address: str, enabled: bool = True, preferred_adapter: str | None = None) -> None:
        """Register a trusted speaker for automatic reconnection tracking."""
        addr = normalize_address(address)
        if not self.globally_enabled:
            return
        logger.debug("AutoReconnect registered speaker %s (enabled=%s, preferred_adapter=%s)", addr, enabled, preferred_adapter)
        if addr not in self.profiles:
            self.profiles[addr] = SpeakerReconnectProfile(
                address=addr,
                enabled=enabled,
                preferred_adapter=preferred_adapter,
            )
        else:
            self.profiles[addr].enabled = enabled
            if preferred_adapter:
                self.profiles[addr].preferred_adapter = preferred_adapter

    @trace_call
    def set_global_enabled(self, enabled: bool) -> None:
        """Enable or clear automatic reconnect profiles as a system-wide gate."""
        self.globally_enabled = enabled
        if not enabled:
            for address in tuple(self.profiles):
                self.unregister_speaker(address)

    @trace_call
    def unregister_speaker(self, address: str) -> None:
        """Unregister a speaker from auto-reconnection."""
        addr = normalize_address(address)
        logger.debug("AutoReconnect unregistering speaker %s", addr)
        task = self._inflight.pop(addr, None)
        if task and not task.done():
            task.cancel()
        if addr in self.profiles:
            del self.profiles[addr]

    @trace_call
    def _calculate_backoff_delay(self, step: int) -> float:
        """Bound log growth and scale the final value; jitter only lengthens it."""
        step = max(0, step)
        if self.initial_backoff == 0:
            delay = 0.0
        elif self.initial_backoff >= self.max_backoff:
            delay = self.max_backoff
        elif self.backoff_multiplier == 1 or step == 0:
            delay = self.initial_backoff
        elif step >= (
            (math.log(self.max_backoff) - math.log(self.initial_backoff)) / math.log(self.backoff_multiplier)
        ):
            delay = self.max_backoff
        else:
            fraction, growth = math.modf(step * math.log2(self.backoff_multiplier))
            mantissa, exponent = math.frexp(self.initial_backoff)
            delay = min(self.max_backoff, math.ldexp(mantissa, exponent + int(growth)) * math.exp2(fraction))
        floor = min(RECONNECT_MIN_BACKOFF_SECONDS, self.initial_backoff)
        delay = max(floor, delay)
        return min(self.max_backoff, delay * (1 + random.uniform(0, RECONNECT_BACKOFF_JITTER)))

    @trace_call
    def _handle_bluetooth_event(self, event_type: str, data: Any):
        """React to live Bluetooth events from the manager."""
        if not self._running:
            return

        if event_type in (EVENT_DEVICE_UPDATED, EVENT_DEVICE_DISCOVERED):
            if isinstance(data, DeviceInfo):
                self._on_device_event(data)

    @trace_call
    def _on_device_event(self, device: DeviceInfo):
        try:
            addr = normalize_address(device.address)
        except ValueError:
            return
        if addr not in self.profiles:
            # Auto-register trusted audio sinks
            if (device.trusted or device.paired or device.connected) and device.is_audio_sink:
                self.register_speaker(addr)
            else:
                return

        profile = self.profiles[addr]
        if not profile.enabled:
            return

        now = time.time()
        if device.connected:
            if profile.disconnected_since is not None:
                # The link came back before the grace window expired, so whatever
                # reported the drop was a flap - not a failure, and nothing to
                # reconnect. Acting on it is what made a single bad flag look like a
                # disconnect/reconnect loop.
                profile.disconnected_since = None
                profile.suppressed_flaps += 1
                if self.health:
                    self.health.record_suppressed_flap(addr, "reconnected inside the grace window")
                logger.info(
                    "Speaker %s reconnected inside the %ss grace window; flap suppressed (%d so far).",
                    addr,
                    int(RECONNECT_DISCONNECT_GRACE_SECONDS),
                    profile.suppressed_flaps,
                )
            if profile.state != ReconnectState.CONNECTED:
                profile.connected_at = now
                if self.health:
                    self.health.observe_speaker(addr, SpeakerState.CONNECTED)
            profile.state = ReconnectState.CONNECTED
            profile.consecutive_failures = 0
            profile.backoff_step = 0
            profile.next_retry_time = 0.0
            profile.circuit_broken_until = 0.0
        else:
            # Device is disconnected
            if profile.disconnected_since is None and profile.state in (
                ReconnectState.CONNECTED,
                ReconnectState.IDLE,
            ):
                # Do not act yet: _tick arms the backoff only once the dropout has
                # survived the grace window, so a single mis-reported flag cannot
                # start a reconnect cycle.
                profile.disconnected_since = now
                logger.debug(
                    "Speaker %s reports disconnected; waiting %ss before acting.",
                    addr,
                    int(RECONNECT_DISCONNECT_GRACE_SECONDS),
                )

    @trace_call
    async def start(self) -> None:
        """Start the background auto-reconnect worker."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._reconnect_loop())
        logger.info("AutoReconnectEngine started.")

    @trace_call
    async def stop(self) -> None:
        """Stop the background auto-reconnect worker."""
        self._running = False
        workers = list(self._inflight.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._inflight.clear()
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("AutoReconnectEngine stopped.")

    @trace_call
    async def _reconnect_loop(self) -> None:
        """Continuous background tick checking for pending reconnects."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("Error in reconnect loop tick: %s", e)
            await asyncio.sleep(RECONNECT_POLL_INTERVAL_SECONDS)

    @trace_call
    async def _tick(self) -> None:
        now = time.time()
        for addr, profile in list(self.profiles.items()):
            if not profile.enabled:
                continue

            if now < profile.circuit_broken_until:
                continue
            if profile.state == ReconnectState.CIRCUIT_BROKEN:
                logger.info("Circuit breaker cooldown expired for %s. Allowing one probe.", addr)
                profile.state = ReconnectState.BACKOFF

            # A dropout is acted on only after it has survived the grace window.
            if profile.disconnected_since is not None and profile.state in (
                ReconnectState.CONNECTED,
                ReconnectState.IDLE,
            ):
                if now - profile.disconnected_since < RECONNECT_DISCONNECT_GRACE_SECONDS:
                    continue
                self._arm_backoff(profile, now)

            # Check scheduled retry
            if (
                profile.state == ReconnectState.BACKOFF
                and now >= profile.next_retry_time
                and now >= profile.cooldown_until
                and now - profile.connected_at >= RECONNECT_POST_CONNECT_SETTLE_SECONDS
            ):
                current = self._inflight.get(addr)
                if current is None or current.done():
                    task = asyncio.create_task(self._attempt_reconnect(profile))
                    self._inflight[addr] = task
                    task.add_done_callback(
                        lambda completed, address=addr: self._release_inflight(address, completed)
                    )

    @trace_call
    def _attempt_in_progress(self, address: str) -> bool:
        """Is a connect attempt already running for this speaker?"""
        task = self._inflight.get(address)
        return task is not None and not task.done()

    @trace_call
    def _arm_backoff(self, profile: SpeakerReconnectProfile, now: float) -> None:
        """Start reconnect handling for a dropout that survived the grace window."""
        addr = profile.address
        self._record_flap(profile, now)
        profile.disconnected_since = None
        logger.info("Speaker %s disconnected. Arming auto-reconnect backoff.", addr)
        profile.state = ReconnectState.BACKOFF
        if self.health:
            self.health.observe_speaker(addr, SpeakerState.DISCONNECTED)
        delay = self._calculate_backoff_delay(profile.backoff_step)
        if profile.cooldown_until > now:
            delay = max(delay, profile.cooldown_until - now)
        profile.next_retry_time = max(profile.next_retry_time, now + delay, profile.circuit_broken_until)

    @trace_call
    def _record_flap(self, profile: SpeakerReconnectProfile, now: float) -> None:
        """Count dropouts in a sliding window and cool down a flapping link.

        A speaker that drops and returns several times inside the window is a
        marginal link: the bridge holds its reconnects for a cooldown instead of
        reconnecting in a loop. That loop used to be self-sustaining - each retry
        tore the link down again and re-negotiated the A2DP codec - which is heard
        as constant disconnects and drops in quality.
        """
        profile.flap_times = [seen for seen in profile.flap_times if now - seen <= RECONNECT_FLAP_WINDOW_SECONDS]
        profile.flap_times.append(now)
        if len(profile.flap_times) < RECONNECT_FLAP_LIMIT:
            return
        profile.flap_times.clear()
        profile.cooldown_until = now + RECONNECT_FLAP_COOLDOWN_SECONDS
        if self.health:
            self.health.record_suppressed_flap(profile.address, "flap limit reached; holding reconnects")
        logger.warning(
            "Speaker %s dropped %d times within %ss. Holding reconnects for %ss to let the link settle.",
            profile.address,
            RECONNECT_FLAP_LIMIT,
            int(RECONNECT_FLAP_WINDOW_SECONDS),
            int(RECONNECT_FLAP_COOLDOWN_SECONDS),
        )

    @trace_call
    def _defer_busy_connection(self, profile: SpeakerReconnectProfile, busy: BluetoothOperationInProgress) -> None:
        logger.info(
            "Speaker %s already has a connection in progress (%s); leaving it to settle.",
            profile.address,
            busy,
        )
        profile.disconnected_since = None
        profile.state = ReconnectState.BACKOFF
        now = time.time()
        profile.next_retry_time = max(
            profile.next_retry_time,
            now + self._calculate_backoff_delay(profile.backoff_step),
            now + RECONNECT_POST_CONNECT_SETTLE_SECONDS,
            profile.cooldown_until,
            profile.circuit_broken_until,
        )

    @trace_call
    async def _attempt_reconnect(self, profile: SpeakerReconnectProfile) -> None:
        addr = profile.address
        if self.profiles.get(addr) is not profile or not profile.enabled or time.time() < profile.circuit_broken_until:
            return
        profile.state = ReconnectState.RECONNECTING
        if self.health:
            self.health.observe_speaker(addr, SpeakerState.RECONNECTING, attempt=profile.consecutive_failures + 1)
        logger.info("Attempting auto-reconnect to %s (Attempt %d)", addr, profile.consecutive_failures + 1)
        try:
            await self.manager.connect_device(addr)
            if self.profiles.get(addr) is not profile:
                return
            profile.state = ReconnectState.CONNECTED
            profile.connected_at = time.time()
            profile.disconnected_since = None
            profile.consecutive_failures = 0
            profile.backoff_step = 0
            profile.next_retry_time = 0.0
            profile.circuit_broken_until = 0.0
            if self.health:
                self.health.observe_speaker(addr, SpeakerState.CONNECTED)
        except asyncio.CancelledError:
            if self.profiles.get(addr) is profile:
                profile.state = ReconnectState.BACKOFF
                profile.next_retry_time = max(
                    profile.next_retry_time,
                    time.time() + self._calculate_backoff_delay(profile.backoff_step),
                    profile.cooldown_until,
                    profile.circuit_broken_until,
                )
            raise
        except BluetoothOperationInProgress as busy:
            if self.profiles.get(addr) is profile:
                self._defer_busy_connection(profile, busy)
        except Exception as error:
            if self.profiles.get(addr) is not profile:
                return
            profile.consecutive_failures += 1
            profile.backoff_step += 1
            now = time.time()
            logger.warning("Failed to reconnect to %s: %s (Failures: %d)", addr, error, profile.consecutive_failures)
            if profile.consecutive_failures >= self.max_failures:
                profile.state = ReconnectState.CIRCUIT_BROKEN
                profile.circuit_broken_until = max(profile.circuit_broken_until, now + self.breaker_cooldown)
                if self.health:
                    self.health.observe_speaker(
                        addr, SpeakerState.UNAVAILABLE,
                        failure=FailureClass.RECONNECT_EXHAUSTED,
                        detail="Reconnect attempts exhausted", attempt=profile.consecutive_failures,
                    )
            else:
                profile.state = ReconnectState.BACKOFF
                if self.health:
                    self.health.observe_speaker(addr, SpeakerState.RECONNECTING, attempt=profile.consecutive_failures)
            profile.next_retry_time = max(
                profile.next_retry_time,
                now + self._calculate_backoff_delay(profile.backoff_step),
                profile.cooldown_until,
                profile.circuit_broken_until,
            )
