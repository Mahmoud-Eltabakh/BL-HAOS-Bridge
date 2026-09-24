"""BL-HAOS: Bluetooth Audio Adapter Package."""

import socket
import sys

# Windows development compatibility shim for dbus-fast
if sys.platform == "win32":
    if not hasattr(socket, "CMSG_LEN"):
        socket.CMSG_LEN = lambda length: length + 16  # type: ignore[attr-defined]
    if not hasattr(socket, "CMSG_SPACE"):
        socket.CMSG_SPACE = lambda length: length + 16  # type: ignore[attr-defined]
    if not hasattr(socket, "SCM_RIGHTS"):
        socket.SCM_RIGHTS = 0x01  # type: ignore[attr-defined]

__version__ = "0.2.41"
