"""BlueZ D-Bus Agent Implementation (org.bluez.Agent1)."""

import logging
from collections.abc import Callable
from typing import Any

from dbus_fast.service import ServiceInterface, method

from .constants import AGENT_INTERFACE

logger = logging.getLogger("bl_haos.bluetooth.agent")


class BlueZAgent(ServiceInterface):
    def __init__(
        self,
        pin_callback: Callable[[str], str] | None = None,
        passkey_callback: Callable[[str], int] | None = None,
        confirm_callback: Callable[[str, int], bool] | None = None,
    ):
        super().__init__(AGENT_INTERFACE)
        self.pin_callback = pin_callback or (lambda dev: "0000")
        self.passkey_callback = passkey_callback or (lambda dev: 0)
        self.confirm_callback = confirm_callback or (lambda dev, key: True)
        self.active_requests: dict[str, Any] = {}

    def get_pin(self, device: str) -> str:
        """Helper to invoke pin callback directly."""
        return str(self.pin_callback(device))

    def confirm_passkey(self, device: str, passkey: int) -> bool:
        """Helper to invoke confirmation callback directly."""
        return bool(self.confirm_callback(device, passkey))

    @method()
    def Release(self):
        logger.info("Agent released by BlueZ")

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # type: ignore[name-defined]
        logger.info("RequestPinCode received for device %s", device)
        pin = self.pin_callback(device)
        return str(pin)

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # type: ignore[name-defined]
        logger.info("DisplayPinCode for %s: %s", device, pincode)
        self.active_requests[device] = {"type": "display_pin", "pincode": pincode}

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # type: ignore[name-defined]
        logger.info("RequestPasskey received for device %s", device)
        passkey = self.passkey_callback(device)
        return int(passkey)

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # type: ignore[name-defined]
        logger.info("DisplayPasskey for %s: %s (entered: %d)", device, passkey, entered)
        self.active_requests[device] = {
            "type": "display_passkey",
            "passkey": passkey,
            "entered": entered,
        }

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # type: ignore[name-defined]
        logger.info("RequestConfirmation for %s with passkey %06d", device, passkey)
        confirmed = self.confirm_callback(device, passkey)
        if not confirmed:
            raise Exception("org.bluez.Error.Rejected: Pairing confirmation rejected by user")

    @method()
    def RequestAuthorization(self, device: "o"):  # type: ignore[name-defined]
        logger.info("RequestAuthorization for %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # type: ignore[name-defined]
        logger.info("AuthorizeService for %s (UUID: %s)", device, uuid)

    @method()
    def Cancel(self):
        logger.info("Agent request cancelled by BlueZ")
        self.active_requests.clear()
