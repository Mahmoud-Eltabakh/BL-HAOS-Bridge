"""Aggressive Auto-Reconnect Engine for Bluetooth Audio Speakers."""

import asyncio
import logging
import random
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel

from .manager import BluetoothManager
from .models import DeviceInfo
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


class AutoReconnectEngine:
    def __init__(
        self,
        manager: BluetoothManager,
        initial_backoff: float = 1.0,
        backoff_multiplier: float = 1.5,
        max_backoff: float = 20.0,
        max_failures_before_breaker: int = 8,
        circuit_breaker_cooldown: float = 10.0,
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
        """Calculate exponential backoff with +/- 15% random jitter."""
        delay = min(self.max_backoff, self.initial_backoff * (self.backoff_multiplier ** step))
        jitter = delay * random.uniform(-0.15, 0.15)
        return max(0.5, delay + jitter)

    def _handle_bluetooth_event(self, event_type: str, data: Any):
        """React to live Bluetooth events from the manager."""
        if not self._running:
            return

        if event_type in ("device_updated", "device_discovered"):
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
            if self.health:
                self.health.observe_speaker(addr, SpeakerState.CONNECTED)
            profile.state = ReconnectState.CONNECTED
            profile.consecutive_failures = 0
            profile.backoff_step = 0
            profile.next_retry_time = 0.0
            profile.circuit_broken_until = 0.0
        else:
            # Device is disconnected
            if profile.state == ReconnectState.CONNECTED or profile.state == ReconnectState.IDLE:
                logger.info("Speaker %s disconnected. Arming auto-reconnect backoff.", addr)
                profile.state = ReconnectState.BACKOFF
                if self.health:
                    self.health.observe_speaker(addr, SpeakerState.DISCONNECTED)
                delay = self._calculate_backoff_delay(profile.backoff_step)
                profile.next_retry_time = now + delay

            elif profile.state == ReconnectState.BACKOFF:
                # Fast-track wake-up: if RSSI packet or device advertisement seen, attempt immediate connection
                if device.rssi is not None and now < profile.next_retry_time:
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
            await asyncio.sleep(0.5)

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

            # Check scheduled retry
            if profile.state == ReconnectState.BACKOFF and now >= profile.next_retry_time:
                current = self._inflight.get(addr)
                if current is None or current.done():
                    task = asyncio.create_task(self._attempt_reconnect(profile))
                    self._inflight[addr] = task
                    task.add_done_callback(
                        lambda completed, address=addr: self._release_inflight(address, completed)
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
                if self.health:
                    self.health.observe_speaker(addr, SpeakerState.CONNECTED)
                profile.consecutive_failures = 0
                profile.backoff_step = 0
                profile.next_retry_time = 0.0
            except Exception as e:
                profile.consecutive_failures += 1
                profile.backoff_step += 1
                logger.warning("Failed to reconnect to %s: %s (Failures: %d)", addr, e, profile.consecutive_failures)
                logger.debug("Auto-reconnect next retry calculation for %s (backoff_step=%d)", addr, profile.backoff_step)

                if profile.consecutive_failures >= 2:
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
                            profile.consecutive_failures = max(profile.consecutive_failures, 3)
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
                            failure=FailureClass.STALE_BLUEZ_OBJECT if profile.consecutive_failures >= 2 else None,
                            detail=e if profile.consecutive_failures >= 2 else None,
                            attempt=profile.consecutive_failures,
                        )
                    delay = self._calculate_backoff_delay(profile.backoff_step)
                    profile.next_retry_time = time.time() + delay
