"""Multi-Room Synchronization Manager for Snapcast."""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

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
    client_ids: List[str] = Field(default_factory=list)
    muted: bool = False


class MultiroomManager:
    def __init__(self, host: str = "127.0.0.1", port: int = 1705):
        self.host = host
        self.port = port
        self.groups: Dict[str, SpeakerGroup] = {
            "default": SpeakerGroup(group_id="default", name="Whole Home Audio", stream_id="default")
        }
        self.clients: Dict[str, MultiroomClient] = {}
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}

    def get_groups(self) -> List[SpeakerGroup]:
        return list(self.groups.values())

    def get_clients(self) -> List[MultiroomClient]:
        return list(self.clients.values())

    def attach_speaker(self, address: str, name: str, latency_offset_ms: int = 0) -> MultiroomClient:
        """Register a connected Bluetooth speaker as a Snapcast client."""
        addr = address.strip().lower()
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
        addr = address.strip().lower()
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
        addr = address.strip().lower()
        client_id = f"snapclient_{addr.replace(':', '')}"
        if client_id in self.clients:
            self.clients[client_id].latency_offset_ms = offset_ms
            logger.info("Set latency offset for %s to %d ms", addr, offset_ms)
            return True
        return False

    def create_group(self, group_id: str, name: str, client_ids: Optional[List[str]] = None) -> SpeakerGroup:
        group = SpeakerGroup(
            group_id=group_id,
            name=name,
            stream_id="default",
            client_ids=client_ids or [],
        )
        self.groups[group_id] = group
        return group
