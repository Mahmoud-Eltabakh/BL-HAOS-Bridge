"""Multi-Room Synchronization Manager for Snapcast."""

import asyncio
import logging

from pydantic import BaseModel, Field

from ..health import FailureClass, HealthRegistry, HealthState, normalize_address, validate_identifier

logger = logging.getLogger("bl_haos.multiroom.manager")


class MultiroomClient(BaseModel):
    client_id: str
    speaker_address: str
    name: str
    connected: bool = True
    volume: int = 70
    latency_offset_ms: int = 0
    group_id: str = "default"


class SpeakerGroup(BaseModel):
    group_id: str
    name: str = "All Speakers"
    stream_id: str = "default"
    client_ids: list[str] = Field(default_factory=list)
    muted: bool = False


class MultiroomManager:
    def __init__(self, host: str = "127.0.0.1", port: int = 1705, health_registry: HealthRegistry | None = None):
        if not isinstance(host, str) or len(host) > 255 or any(ord(char) < 32 for char in host):
            raise ValueError("Invalid Snapcast host")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Invalid Snapcast port")
        self.host = host
        self.port = port
        self.groups: dict[str, SpeakerGroup] = {
            "default": SpeakerGroup(group_id="default", name="Whole Home Audio", stream_id="default")
        }
        self.clients: dict[str, MultiroomClient] = {}
        self.active_processes: dict[str, asyncio.subprocess.Process] = {}
        self.health = health_registry
        if self.health:
            self.health.observe_component("snapcast", HealthState.HEALTHY, required=False, source="configured")

    def observe_readiness(self, ready: bool, detail: str | None = None) -> None:
        if self.health:
            self.health.observe_component(
                "snapcast", HealthState.HEALTHY if ready else HealthState.DEGRADED,
                required=False,
                failure=None if ready else FailureClass.SNAPCAST_UNAVAILABLE,
                detail=detail or (None if ready else "Snapcast FIFO/process boundary unavailable"),
                source="configured",
            )

    def get_groups(self) -> list[SpeakerGroup]:
        return list(self.groups.values())

    def get_clients(self) -> list[MultiroomClient]:
        return list(self.clients.values())

    def attach_speaker(self, address: str, name: str, latency_offset_ms: int = 0) -> MultiroomClient:
        """Register a connected Bluetooth speaker as a Snapcast client."""
        addr = normalize_address(address)
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(char) < 32 for char in name):
            raise ValueError("Invalid speaker name")
        if not isinstance(latency_offset_ms, int) or not -5000 <= latency_offset_ms <= 5000:
            raise ValueError("Invalid latency offset")
        client_id = f"snapclient_{addr.replace(':', '')}"

        client = MultiroomClient(
            client_id=client_id,
            speaker_address=addr,
            name=name,
            connected=True,
            latency_offset_ms=latency_offset_ms,
            group_id="default",
        )
        self.clients[client_id] = client

        # Add to default group
        if client_id not in self.groups["default"].client_ids:
            self.groups["default"].client_ids.append(client_id)

        logger.info("Attached speaker %s to multi-room group default", addr)
        return client

    def detach_speaker(self, address: str) -> bool:
        """Detach speaker and stop its snapclient process."""
        addr = normalize_address(address)
        client_id = f"snapclient_{addr.replace(':', '')}"

        if client_id in self.clients:
            del self.clients[client_id]

        for group in self.groups.values():
            if client_id in group.client_ids:
                group.client_ids.remove(client_id)

        if client_id in self.active_processes:
            try:
                self.active_processes[client_id].terminate()
                del self.active_processes[client_id]
            except Exception:
                pass
        return True

    def set_latency_offset(self, address: str, offset_ms: int) -> bool:
        """Calibrate acoustic latency offset (+/- ms) for phase-accurate sync."""
        addr = normalize_address(address)
        if not isinstance(offset_ms, int) or not -5000 <= offset_ms <= 5000:
            raise ValueError("Invalid latency offset")
        client_id = f"snapclient_{addr.replace(':', '')}"
        if client_id in self.clients:
            self.clients[client_id].latency_offset_ms = offset_ms
            logger.info("Set latency offset for %s to %d ms", addr, offset_ms)
            return True
        return False

    def create_group(self, group_id: str, name: str, client_ids: list[str] | None = None) -> SpeakerGroup:
        group_id = validate_identifier(group_id, "Group identifier")
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(char) < 32 for char in name):
            raise ValueError("Invalid group name")
        client_ids = client_ids or []
        if len(client_ids) > 32 or any(not isinstance(client_id, str) or not client_id.startswith("snapclient_") or len(client_id) > 64 for client_id in client_ids):
            raise ValueError("Invalid client identifiers")
        group = SpeakerGroup(
            group_id=group_id,
            name=name,
            stream_id="default",
            client_ids=client_ids,
        )
        self.groups[group_id] = group
        return group
