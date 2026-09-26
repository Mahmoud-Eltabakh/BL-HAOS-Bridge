"""Aggressive Auto-Reconnect Engine for Bluetooth Audio Speakers."""

import asyncio
import logging
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
    RECONNECT_MIN_FAILURES_AFTER_HEAL,
    RECONNECT_POLL_INTERVAL_SECONDS,
    RECONNECT_POST_CONNECT_SETTLE_SECONDS,
    RECONNECT_SELF_HEAL_FAILURE_THRESHOLD,
)
from ..health import FailureClass, HealthRegistry, SpeakerState, normalize_address

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
        self.manager = manager
        self.initial_backoff = initial_backoff
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff = max_backoff
        self.max_failures = max_failures_before_breaker
        self.breaker_cooldown = circuit_breaker_cooldown
        self.health = health_registry

        self.profiles: dict[str, SpeakerReconnectProfile] = {}
        self.adapter_locks: dict[str, asyncio.Lock] = {}
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._inflight: dict[str, asyncio.Task] = {}

        # Subscribe to BluetoothManager events
        self.manager.add_event_listener(self._handle_bluetooth_event)

    def _get_adapter_lock(self, adapter_name: str) -> asyncio.Lock:
        if adapter_name not in self.adapter_locks:
            self.adapter_locks[adapter_name] = asyncio.Lock()
        return self.adapter_locks[adapter_name]

    def _release_inflight(self, address: str, task: asyncio.Task) -> None:
        """Release ownership only when the completed task is still current."""
        if self._inflight.get(address) is task:
            self._inflight.pop(address, None)

    def register_speaker(self, address: str, enabled: bool = True, preferred_adapter: str | None = None) -> None:
        """Register a trusted speaker for automatic reconnection tracking."""
        addr = normalize_address(address)
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

    def unregister_speaker(self, address: str) -> None:
        """Unregister a speaker from auto-reconnection."""
        addr = normalize_address(address)
        logger.debug("AutoReconnect unregistering speaker %s", addr)
        task = self._inflight.pop(addr, None)
        if task and not task.done():
            task.cancel()
        if addr in self.profiles:
            del self.profiles[addr]

    def _calculate_backoff_delay(self, step: int) -> float:
        """Calculate exponential backoff with random jitter."""
        delay = min(self.max_backoff, self.initial_backoff * (self.backoff_multiplier ** step))
        jitter = delay * random.uniform(-RECONNECT_BACKOFF_JITTER, RECONNECT_BACKOFF_JITTER)
        return max(RECONNECT_MIN_BACKOFF_SECONDS, delay + jitter)

    def _handle_bluetooth_event(self, event_type: str, data: Any):
        """React to live Bluetooth events from the manager."""
        if not self._running:
            return

        if event_type in (EVENT_DEVICE_UPDATED, EVENT_DEVICE_DISCOVERED):
            if isinstance(data, DeviceInfo):
                self._on_device_event(data)

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
            elif profile.state == ReconnectState.BACKOFF:
                # Fast-track wake-up: an advertisement or RSSI sample means the
                # speaker is in range, so a pending retry can be pulled forward -
                # but never while an attempt is in flight, while the link is still
                # settling, or during a flap cooldown.
                if (
                    device.rssi is not None
                    and now < profile.next_retry_time
                    and now >= profile.cooldown_until
                    and now - profile.connected_at >= RECONNECT_POST_CONNECT_SETTLE_SECONDS
                    and not self._attempt_in_progress(addr)
                ):
                    logger.info("Presence advertisement detected for %s (RSSI %d). Fast-tracking reconnect.", addr, device.rssi)
                    profile.next_retry_time = now

    async def start(self) -> None:
        """Start the background auto-reconnect worker."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._reconnect_loop())
        logger.info("AutoReconnectEngine started.")

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

    async def _reconnect_loop(self) -> None:
        """Continuous background tick checking for pending reconnects."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("Error in reconnect loop tick: %s", e)
            await asyncio.sleep(RECONNECT_POLL_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        now = time.time()
        for addr, profile in list(self.profiles.items()):
            if not profile.enabled:
                continue

            # Check circuit breaker reset
            if profile.state == ReconnectState.CIRCUIT_BROKEN:
                if now >= profile.circuit_broken_until:
                    logger.info("Circuit breaker cooldown expired for %s. Resuming backoff.", addr)
                    profile.state = ReconnectState.BACKOFF
                    profile.consecutive_failures = 0
                    profile.next_retry_time = now
                else:
                    continue

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
            ):
                current = self._inflight.get(addr)
                if current is None or current.done():
                    task = asyncio.create_task(self._attempt_reconnect(profile))
                    self._inflight[addr] = task
                    task.add_done_callback(
                        lambda completed, address=addr: self._release_inflight(address, completed)
                    )

    def _attempt_in_progress(self, address: str) -> bool:
        """Is a connect attempt already running for this speaker?"""
        task = self._inflight.get(address)
        return task is not None and not task.done()

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
        profile.next_retry_time = now + delay

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

    async def _recover_stale_device(self, profile: SpeakerReconnectProfile) -> bool:
        """Clear stale BlueZ state and re-establish pairing for a blocked speaker."""
        addr = profile.address
        logger.warning("Attempting self-healing recovery for stale Bluetooth speaker %s", addr)

        try:
            await self.manager.remove_device(addr)
        except Exception as exc:
            logger.debug("Cleanup remove for %s failed during auto-recovery: %s", addr, exc)

        try:
            await self.manager.pair_and_trust(addr)
        except Exception as exc:
            logger.warning("Pair & trust recovery for %s failed: %s", addr, exc)
            if self.health:
                self.health.observe_speaker(addr, SpeakerState.RECONNECTING, failure=FailureClass.STALE_BLUEZ_OBJECT, detail=exc)

        try:
            await self.manager.connect_device(addr)
            logger.info("Self-healing recovery succeeded for %s", addr)
            return True
        except Exception as exc:
            logger.warning("Reconnect after recovery failed for %s: %s", addr, exc)
            if self.health:
                self.health.observe_speaker(addr, SpeakerState.UNAVAILABLE, failure=FailureClass.STALE_BLUEZ_OBJECT, detail=exc)
            return False

    async def _attempt_reconnect(self, profile: SpeakerReconnectProfile) -> None:
        addr = profile.address
        dev = self.manager.get_device_by_address(addr)
        if not dev:
            if self.health:
                self.health.observe_speaker(addr, SpeakerState.UNAVAILABLE, failure=FailureClass.DBUS_DISCONNECTED, detail="Device cache unavailable")
            return

        adapter_name = profile.preferred_adapter or dev.adapter_name
        lock = self._get_adapter_lock(adapter_name)

        if lock.locked():
            # Another reconnect is in progress on this adapter; postpone slightly
            logger.debug("Adapter %s is locked by another reconnect operation; deferring %s", adapter_name, addr)
            profile.next_retry_time = time.time() + 1.0
            return

        async with lock:
            profile.state = ReconnectState.RECONNECTING
            if self.health:
                self.health.observe_speaker(addr, SpeakerState.RECONNECTING, attempt=profile.consecutive_failures + 1)
            logger.info("Attempting auto-reconnect to %s on adapter %s (Attempt %d)", addr, adapter_name, profile.consecutive_failures + 1)
            try:
                await self.manager.connect_device(addr)
                logger.info("Successfully reconnected to speaker %s", addr)
                logger.debug("Auto-reconnect succeeded for %s, resetting failure counters", addr)
                profile.state = ReconnectState.CONNECTED
                # Start the settle window: the link is up, so nothing may touch it
                # (including a fast-tracked retry) while BlueZ finishes the A2DP
                # transport.
                profile.connected_at = time.time()
                profile.disconnected_since = None
                if self.health:
                    self.health.observe_speaker(addr, SpeakerState.CONNECTED)
                profile.consecutive_failures = 0
                profile.backoff_step = 0
                profile.next_retry_time = 0.0
            except BluetoothOperationInProgress as busy:
                # BlueZ - or another client - is already bringing this link up.
                # Retrying now is exactly what made the two connect requests race,
                # so wait for the outcome instead of counting a failure.
                logger.info(
                    "Speaker %s already has a connection in progress (%s); leaving it to settle.",
                    addr,
                    busy,
                )
                profile.disconnected_since = None
                profile.state = ReconnectState.BACKOFF
                profile.consecutive_failures = 0
                profile.backoff_step = 0
                profile.next_retry_time = time.time() + RECONNECT_POST_CONNECT_SETTLE_SECONDS
            except Exception as e:
                profile.consecutive_failures += 1
                profile.backoff_step += 1
                logger.warning("Failed to reconnect to %s: %s (Failures: %d)", addr, e, profile.consecutive_failures)
                logger.debug("Auto-reconnect next retry calculation for %s (backoff_step=%d)", addr, profile.backoff_step)

                if profile.consecutive_failures >= RECONNECT_SELF_HEAL_FAILURE_THRESHOLD:
                    try:
                        recovered = await self._recover_stale_device(profile)
                        if recovered:
                            profile.state = ReconnectState.CONNECTED
                            profile.consecutive_failures = 0
                            profile.backoff_step = 0
                            profile.next_retry_time = 0.0
                            if self.health:
                                self.health.observe_speaker(addr, SpeakerState.CONNECTED)
                            logger.info("Self-healing recovery connected stale speaker %s", addr)
                        else:
                            profile.state = ReconnectState.BACKOFF
                            profile.next_retry_time = time.time() + self.initial_backoff
                            profile.consecutive_failures = max(profile.consecutive_failures, RECONNECT_MIN_FAILURES_AFTER_HEAL)
                            logger.info("Triggered self-healing recovery for stale speaker %s", addr)
                        return
                    except Exception as recovery_exc:
                        logger.warning("Auto-recovery failed for %s: %s", addr, recovery_exc)

                if profile.consecutive_failures >= self.max_failures:
                    logger.error("Max failures reached for %s. Tripping circuit breaker for %.1fs.", addr, self.breaker_cooldown)
                    profile.state = ReconnectState.CIRCUIT_BROKEN
                    profile.circuit_broken_until = time.time() + self.breaker_cooldown
                    if self.health:
                        self.health.observe_speaker(
                            addr,
                            SpeakerState.UNAVAILABLE,
                            failure=FailureClass.RECONNECT_EXHAUSTED,
                            detail="Reconnect attempts exhausted",
                            attempt=profile.consecutive_failures,
                        )
                else:
                    profile.state = ReconnectState.BACKOFF
                    if self.health:
                        self.health.observe_speaker(
                            addr,
                            SpeakerState.RECONNECTING,
                            failure=FailureClass.STALE_BLUEZ_OBJECT if profile.consecutive_failures >= RECONNECT_SELF_HEAL_FAILURE_THRESHOLD else None,
                            detail=e if profile.consecutive_failures >= RECONNECT_SELF_HEAL_FAILURE_THRESHOLD else None,
                            attempt=profile.consecutive_failures,
                        )
                    delay = self._calculate_backoff_delay(profile.backoff_step)
                    profile.next_retry_time = time.time() + delay
