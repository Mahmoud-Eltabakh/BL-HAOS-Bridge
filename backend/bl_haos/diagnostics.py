"""Bounded operator diagnostics, support export, and correlated runtime events."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from .health import HealthRegistry, redact_value, safe_detail


class DiagnosticsService:
    """Project canonical health observations into bounded support-facing data."""

    CONTRACT_VERSION = 1
    EVENT_VERSION = 1
    MAX_EVENTS = 50
    MAX_EVENT_AGE = 24 * 60 * 60
    MAX_EVENT_BYTES = 4096
    MAX_BUNDLE_BYTES = 64 * 1024
    MAX_COLLECTION_ITEMS = 100

    _OMITTED_KEYS = {"dbus_payload", "raw_dbus", "raw_output", "media", "media_url", "output"}

    def __init__(self, health: HealthRegistry, clock: Callable[[], float] = time.time) -> None:
        self.health = health
        self.clock = clock
        self._events: deque[dict[str, Any]] = deque(maxlen=self.MAX_EVENTS)

    @classmethod
    def _bounded(cls, value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "[omitted]"
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key in sorted(value, key=str):
                name = str(key)
                lower = name.lower()
                if name in cls._OMITTED_KEYS or any(token in lower for token in ("token", "password", "secret", "authorization", "credential")):
                    continue
                result[name] = cls._bounded(value[key], depth + 1)
                if len(result) >= cls.MAX_COLLECTION_ITEMS:
                    break
            return result
        if isinstance(value, (list, tuple, set)):
            return [cls._bounded(item, depth + 1) for item in list(value)[: cls.MAX_COLLECTION_ITEMS]]
        if isinstance(value, str):
            return redact_value(value)[:256]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return safe_detail(value)

    @classmethod
    def _event_size(cls, event: Mapping[str, Any]) -> int:
        return len(json.dumps(event, sort_keys=True, separators=(",", ":")).encode())

    def record_event(
        self,
        name: str,
        *,
        component: str,
        speaker: str | None = None,
        adapter: str | None = None,
        detail: Any = None,
        failure_class: Any = None,
        transition_reason: str | None = None,
        recovery: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "version": self.EVENT_VERSION,
            "name": str(name)[:64],
            "timestamp": self.clock(),
            "correlation_id": correlation_id or uuid.uuid4().hex,
            "component": str(component)[:64],
        }
        if speaker:
            event["speaker"] = str(speaker)[:64]
        if adapter:
            event["adapter"] = str(adapter)[:64]
        if detail is not None:
            event["detail"] = self._bounded(detail)
        if failure_class is not None:
            event["failure_class"] = getattr(failure_class, "value", str(failure_class))
        if transition_reason:
            event["transition_reason"] = str(transition_reason)[:256]
        if recovery:
            event["recovery"] = str(recovery)[:64]
        if self._event_size(event) > self.MAX_EVENT_BYTES:
            event["detail"] = "[omitted]"
        self._events.append(event)
        return dict(event)

    def events(self) -> list[dict[str, Any]]:
        cutoff = self.clock() - self.MAX_EVENT_AGE
        return [dict(event) for event in self._events if event["timestamp"] >= cutoff]

    def _failure_records(self, snapshot: Any) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for component in snapshot.components.values():
            if component.failure:
                failures.append({
                    "component": component.name,
                    "classification": component.failure.classification.value,
                    "detail": component.failure.detail,
                    "observed_at": component.failure.observed_at,
                })
        for speaker in snapshot.speakers.values():
            if speaker.failure:
                failures.append({
                    "component": "speaker",
                    "speaker": speaker.address,
                    "classification": speaker.failure.classification.value,
                    "detail": speaker.failure.detail,
                    "observed_at": speaker.failure.observed_at,
                })
        return sorted(failures, key=lambda item: item["observed_at"], reverse=True)[: self.MAX_COLLECTION_ITEMS]

    def snapshot(self, *, adapters: Any = None, sinks: Any = None) -> dict[str, Any]:
        health = self.health.snapshot()
        failures = self._failure_records(health)
        return {
            "contract_version": self.CONTRACT_VERSION,
            "health_version": health.version,
            "status": health.status.value,
            "lifecycle": health.lifecycle.value,
            "timestamp": self.clock(),
            "components": {
                name: {
                    "state": component.state.value,
                    "required": component.required,
                    "source": component.source,
                    "failure_class": component.failure.classification.value if component.failure else None,
                }
                for name, component in sorted(health.components.items())
            },
            "adapters": self._bounded(adapters or []),
            "speakers": [
                {
                    "address": speaker.address,
                    "state": speaker.state.value,
                    "transition": speaker.transition,
                    "attempt": speaker.attempt,
                    "failure_class": speaker.failure.classification.value if speaker.failure else None,
                    "observed_at": speaker.observed_at,
                }
                for speaker in sorted(health.speakers.values(), key=lambda item: item.address)[: self.MAX_COLLECTION_ITEMS]
            ],
            "sink_availability": self._bounded(sinks or {"available": False, "count": 0}),
            "last_failure": failures[0] if failures else None,
            "recent_failures": failures,
            "event_count": len(self.events()),
        }

    def support_bundle(self, *, adapters: Any = None, sinks: Any = None) -> dict[str, Any]:
        bundle = {
            "schema": "bl-haos.support-bundle",
            "versions": {"diagnostics": self.CONTRACT_VERSION, "events": self.EVENT_VERSION, "health": 1},
            "diagnostics": self.snapshot(adapters=adapters, sinks=sinks),
            "lifecycle": self.health.snapshot().lifecycle.value,
            "events": self.events(),
        }
        encoded = json.dumps(self._bounded(bundle), sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > self.MAX_BUNDLE_BYTES:
            bundle["events"] = []
            encoded = json.dumps(self._bounded(bundle), sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > self.MAX_BUNDLE_BYTES:
            bundle["diagnostics"]["recent_failures"] = []
            bundle["diagnostics"]["speakers"] = []
            bundle["diagnostics"]["adapters"] = []
            encoded = json.dumps(self._bounded(bundle), sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > self.MAX_BUNDLE_BYTES:
            bundle["diagnostics"] = {
                "contract_version": self.CONTRACT_VERSION,
                "status": bundle["diagnostics"]["status"],
                "lifecycle": bundle["diagnostics"]["lifecycle"],
                "event_count": 0,
            }
        return self._bounded(bundle)