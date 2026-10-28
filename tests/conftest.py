import os
import socket
import sys
from pathlib import Path


def pytest_configure(config):
    """Run bridge tests from the module root so all relative filesystem fixtures resolve."""
    bridge_root = Path(__file__).resolve().parents[1]
    os.chdir(bridge_root)
    assert config.getini("asyncio_mode") == "auto", "Bridge tests require asyncio_mode=auto"


# Windows development compatibility shim for dbus-fast
if sys.platform == "win32":
    if not hasattr(socket, "CMSG_LEN"):
        socket.CMSG_LEN = lambda length: length + 16  # type: ignore[attr-defined]
    if not hasattr(socket, "CMSG_SPACE"):
        socket.CMSG_SPACE = lambda length: length + 16  # type: ignore[attr-defined]
    if not hasattr(socket, "SCM_RIGHTS"):
        socket.SCM_RIGHTS = 0x01  # type: ignore[attr-defined]
