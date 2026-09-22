"""Aggressive Auto-Reconnect Engine for Bluetooth Audio Speakers."""

import time
import random
import asyncio
import logging
from enum import Enum
from typing import Any, Dict, Optional, Set
from pydantic import BaseModel, Field

from .models import DeviceInfo
from .manager import BluetoothManager

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
    preferred_adapter: Optional[str] = None


class AutoReconnectEngine:
    def __init__(
        self,
        manager: BluetoothManager,
        initial_backoff: float = 2.0,
        backoff_multiplier: float = 2.0,
        max_backoff: float = 60.0,
        max_failures_before_breaker: int = 5,
        circuit_breaker_cooldown: float = 30.0,
    ):
        self.manager = manager
        self.initial_backoff = initial_backoff
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff = max_backoff
        self.max_failures = max_failures_before_breaker
        self.breaker_cooldown = circuit_breaker_cooldown

        self.profiles: Dict[str, SpeakerReconnectProfile] = {}
        self.adapter_locks: Dict[str, asyncio.Lock] = {}
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None

        # Subscribe to BluetoothManager events
        self.manager.add_event_listener(self._handle_bluetooth_event)

    def _get_adapter_lock(self, adapter_name: str) -> asyncio.Lock:
        if adapter_name not in self.adapter_locks:
            self.adapter_locks[adapter_name] = asyncio.Lock()
        return self.adapter_locks[adapter_name]

    def register_speaker(self, address: str, enabled: bool = True, preferred_adapter: Optional[str] = None) -> None:
        """Register a trusted speaker for automatic reconnection tracking."""
        addr = address.strip().lower()
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
        addr = address.strip().lower()
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
        addr = device.address.lower()
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
                # Fire reconnect task
                asyncio.create_task(self._attempt_reconnect(profile))

    async def _recover_stale_device(self, profile: SpeakerReconnectProfile) -> None:
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

        try:
            await self.manager.connect_device(addr)
            logger.info("Self-healing recovery succeeded for %s", addr)
        except Exception as exc:
            logger.warning("Reconnect after recovery failed for %s: %s", addr, exc)

    async def _attempt_reconnect(self, profile: SpeakerReconnectProfile) -> None:
        addr = profile.address
        dev = self.manager.get_device_by_address(addr)
        if not dev:
            return

        adapter_name = profile.preferred_adapter or dev.adapter_name
        lock = self._get_adapter_lock(adapter_name)

        if lock.locked():
            # Another reconnect is in progress on this adapter; postpone slightly
            profile.next_retry_time = time.time() + 1.0
            return

        async with lock:
            profile.state = ReconnectState.RECONNECTING
            logger.info("Attempting auto-reconnect to %s on adapter %s (Attempt %d)", addr, adapter_name, profile.consecutive_failures + 1)
            try:
                await self.manager.connect_device(addr)
                logger.info("Successfully reconnected to speaker %s", addr)
                profile.state = ReconnectState.CONNECTED
                profile.consecutive_failures = 0
                profile.backoff_step = 0
                profile.next_retry_time = 0.0
            except Exception as e:
                profile.consecutive_failures += 1
                profile.backoff_step += 1
                logger.warning("Failed to reconnect to %s: %s (Failures: %d)", addr, e, profile.consecutive_failures)

                if profile.consecutive_failures >= 2:
                    try:
                        await self._recover_stale_device(profile)
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
                else:
                    profile.state = ReconnectState.BACKOFF
                    delay = self._calculate_backoff_delay(profile.backoff_step)
                    profile.next_retry_time = time.time() + delay
