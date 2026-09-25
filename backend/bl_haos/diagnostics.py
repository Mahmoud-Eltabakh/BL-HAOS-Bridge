"""Bounded health observations and correlated runtime events."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from .constants import (
    BOUNDED_STRING_LENGTH,
    COMPONENT_SPEAKER,
    DIAGNOSTICS_CONTRACT_VERSION,
    DIAGNOSTICS_EVENT_VERSION,
    MAX_DETAIL_LENGTH,
    MAX_DIAGNOSTICS_DEPTH,
    MAX_IDENTIFIER_LENGTH,
    OMITTED_PLACEHOLDER,
)
from .health import HealthRegistry, redact_value, safe_detail


class DiagnosticsService:
    """Project canonical health observations into bounded support-facing data."""

    CONTRACT_VERSION = DIAGNOSTICS_CONTRACT_VERSION
    EVENT_VERSION = DIAGNOSTICS_EVENT_VERSION
    MAX_EVENTS = 50
    MAX_EVENT_AGE = 24 * 60 * 60
    MAX_EVENT_BYTES = 4096
    MAX_COLLECTION_ITEMS = 100

    _OMITTED_KEYS = {"dbus_payload", "raw_dbus", "raw_output", "media", "media_url", "output"}

    def __init__(self, health: HealthRegistry, clock: Callable[[], float] = time.time) -> None:
        self.health = health
        self.clock = clock
        self._events: deque[dict[str, Any]] = deque(maxlen=self.MAX_EVENTS)

    @classmethod
    def _bounded(cls, value: Any, depth: int = 0) -> Any:
        if depth > MAX_DIAGNOSTICS_DEPTH:
            return OMITTED_PLACEHOLDER
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
            return redact_value(value)[:BOUNDED_STRING_LENGTH]
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
            "name": str(name)[:MAX_IDENTIFIER_LENGTH],
            "timestamp": self.clock(),
            "correlation_id": correlation_id or uuid.uuid4().hex,
            "component": str(component)[:MAX_IDENTIFIER_LENGTH],
        }
        if speaker:
            event["speaker"] = str(speaker)[:MAX_IDENTIFIER_LENGTH]
        if adapter:
            event["adapter"] = str(adapter)[:MAX_IDENTIFIER_LENGTH]
        if detail is not None:
            event["detail"] = self._bounded(detail)
        if failure_class is not None:
            event["failure_class"] = getattr(failure_class, "value", str(failure_class))
        if transition_reason:
            event["transition_reason"] = str(transition_reason)[:MAX_DETAIL_LENGTH]
        if recovery:
            event["recovery"] = str(recovery)[:MAX_IDENTIFIER_LENGTH]
        if self._event_size(event) > self.MAX_EVENT_BYTES:
            event["detail"] = OMITTED_PLACEHOLDER
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
                    "component": COMPONENT_SPEAKER,
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

