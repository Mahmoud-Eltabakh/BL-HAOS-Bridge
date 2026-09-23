"""Allowlisted, bounded operator recovery actions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .diagnostics import DiagnosticsService
from .health import HealthRegistry, normalize_address, validate_identifier


GUIDANCE: dict[str, dict[str, Any]] = {
    "pairing_failed": {
        "diagnosis": "The speaker did not complete Bluetooth pairing.",
        "next_steps": ["Confirm the speaker is in pairing mode and powered on."],
        "prerequisites": ["Speaker is within radio range", "A powered Bluetooth adapter is present"],
        "actions": ["refresh_diagnostics", "refresh_device"],
        "expected_outcome": "The speaker appears as a trusted, available audio device.",
    },
    "sink_missing": {
        "diagnosis": "Bluetooth is connected but PipeWire has no matching audio sink.",
        "next_steps": ["Wait for the audio profile to settle, then recheck the dependency."],
        "prerequisites": ["Speaker is connected", "PipeWire is running"],
        "actions": ["recheck_dependency", "retry_reconnect"],
        "expected_outcome": "A connected PipeWire sink is reported for the speaker.",
    },
    "reconnect_exhausted": {
        "diagnosis": "Automatic reconnect attempts reached their bounded limit.",
        "next_steps": ["Move the speaker closer and retry one bounded reconnect."],
        "prerequisites": ["Speaker remains powered on", "The target is a normalized trusted speaker record"],
        "actions": ["retry_reconnect", "refresh_device"],
        "expected_outcome": "The speaker reconnects or returns a classified failure without more retries.",
    },
    "native_integration_unavailable": {
        "diagnosis": "The Home Assistant native bridge is not currently available.",
        "next_steps": ["Recheck the native dependency before changing speaker state."],
        "prerequisites": ["The native bridge credential is configured", "Home Assistant is reachable"],
        "actions": ["recheck_dependency", "refresh_diagnostics"],
        "expected_outcome": "Native bridge readiness is refreshed without exposing credentials.",
    },
    "dbus_unavailable": {
        "diagnosis": "The system Bluetooth D-Bus is unavailable.",
        "next_steps": ["Recheck the Bluetooth dependency once the host bus is ready."],
        "prerequisites": ["Host D-Bus is mounted", "BlueZ is running"],
        "actions": ["recheck_dependency", "refresh_diagnostics"],
        "expected_outcome": "Bluetooth readiness is refreshed with a bounded result.",
    },
    "stale_bluez_object": {
        "diagnosis": "The cached BlueZ device object is stale.",
        "next_steps": ["Refresh the normalized speaker record, then retry reconnect if needed."],
        "prerequisites": ["Speaker remains discoverable", "A powered adapter is present"],
        "actions": ["refresh_device", "retry_reconnect"],
        "expected_outcome": "A fresh device record is used for the next operation.",
    },
}

GENERIC_GUIDANCE = {
    "diagnosis": "The runtime reported an unrecognized failure classification.",
    "next_steps": ["Export diagnostics and escalate with the correlation id."],
    "prerequisites": ["The bridge is running"],
    "actions": ["refresh_diagnostics"],
    "expected_outcome": "A bounded diagnostics snapshot is available for support.",
}


class RecoveryService:
    """Execute only named manager operations with one in-flight action per target."""

    ACTIONS = {"refresh_diagnostics", "retry_reconnect", "refresh_device", "recheck_dependency"}
    COMPONENTS = {"bluetooth", "pipewire", "snapcast", "native_bridge"}
    ACTION_TIMEOUT = 5.0

    def __init__(self, health: HealthRegistry, diagnostics: DiagnosticsService, manager: Any) -> None:
        self.health = health
        self.diagnostics = diagnostics
        self.manager = manager
        self._inflight: set[str] = set()
        self._sequence = 0

    def guidance(self, failure_class: str | None) -> dict[str, Any]:
        key = str(failure_class or "unknown")
        source = GUIDANCE.get(key, GENERIC_GUIDANCE)
        return {
            "failure_class": key,
            **source,
            "status": "available" if key in GUIDANCE else "escalate",
        }

    def contract(self, diagnostics: Mapping[str, Any]) -> dict[str, Any]:
        failure = diagnostics.get("last_failure") or {}
        return {
            "contract_version": 1,
            "guidance": self.guidance(failure.get("classification")),
            "current_status": diagnostics.get("status", "unknown"),
            "correlation_id": self.diagnostics.events()[-1].get("correlation_id") if self.diagnostics.events() else None,
        }

    @staticmethod
    def _target(action_id: str, target: str | None) -> str | None:
        if action_id in {"retry_reconnect", "refresh_device"}:
            if not target:
                raise ValueError("Target is required")
            try:
                return normalize_address(target)
            except ValueError as error:
                raise ValueError("Invalid recovery target") from error
        if action_id == "recheck_dependency":
            if not target or target not in RecoveryService.COMPONENTS:
                raise ValueError("Invalid recovery target")
            return target
        return None

    async def execute(self, action_id: str, target: str | None = None) -> dict[str, Any]:
        if action_id not in self.ACTIONS:
            raise ValueError("Unsupported recovery action")
        normalized = self._target(action_id, target)
        lock_key = normalized or action_id
        if lock_key in self._inflight:
            return self._result(action_id, normalized, "conflict", "Recovery is already in progress")
        self._inflight.add(lock_key)
        try:
            try:
                if action_id == "retry_reconnect":
                    await asyncio.wait_for(self.manager.connect_device(normalized), timeout=self.ACTION_TIMEOUT)
                elif action_id == "refresh_device":
                    await asyncio.wait_for(self.manager.ensure_device(normalized), timeout=self.ACTION_TIMEOUT)
                elif action_id == "recheck_dependency":
                    if normalized == "bluetooth" and hasattr(self.manager, "recover_dbus"):
                        await asyncio.wait_for(self.manager.recover_dbus(), timeout=self.ACTION_TIMEOUT)
                return self._result(action_id, normalized, "succeeded", "Bounded recovery action completed")
            except Exception:
                return self._result(action_id, normalized, "failed", "Bounded recovery action did not complete")
        finally:
            self._inflight.discard(lock_key)

    def _result(self, action_id: str, target: str | None, result: str, detail: str) -> dict[str, Any]:
        self._sequence += 1
        correlation_id = f"recovery-{self._sequence:04d}"
        event = self.diagnostics.record_event(
            "recovery_action",
            component="recovery",
            detail={"action_id": action_id, "result": result},
            recovery=result,
            correlation_id=correlation_id,
        )
        return {
            "contract_version": 1,
            "action_id": action_id,
            "target": target,
            "correlation_id": event["correlation_id"],
            "result": result,
            "detail": detail,
            "changed": result == "succeeded" and action_id != "refresh_diagnostics",
            "next_guidance": self.guidance(None if result == "succeeded" else "unknown"),
        }