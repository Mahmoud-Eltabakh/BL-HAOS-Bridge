"""BlueZ D-Bus Agent Implementation (org.bluez.Agent1)."""

import logging
from collections.abc import Callable
from typing import Any

from dbus_fast.service import ServiceInterface, method

from .constants import AGENT_INTERFACE, AGENT_PAIRING_REFUSED_ERROR

logger = logging.getLogger("bl_haos.bluetooth.agent")


class BlueZAgent(ServiceInterface):
    """BlueZ agent that answers pairing requests only when the operator asked.

    Every callback defaults to *refusing*. A device in radio range must never be
    able to pair by itself, so `BluetoothManager` installs callbacks that consult
    the short-lived pairing window opened by an operator-initiated pairing
    request. Callbacks return `None`/`False` to reject (see THREAT-MODEL.md, T3).

    Rejections raise the BlueZ `Rejected` error, which ends the attempt instead of
    supplying a credential.
    """

    def __init__(
        self,
        pin_callback: Callable[[str], str | None] | None = None,
        passkey_callback: Callable[[str], int | None] | None = None,
        confirm_callback: Callable[[str, int], bool] | None = None,
        authorization_callback: Callable[[str], bool] | None = None,
    ):
        super().__init__(AGENT_INTERFACE)
        self.pin_callback = pin_callback or (lambda device: None)
        self.passkey_callback = passkey_callback or (lambda device: None)
        self.confirm_callback = confirm_callback or (lambda device, passkey: False)
        self.authorization_callback = authorization_callback or (lambda device: False)
        self.active_requests: dict[str, Any] = {}

    @staticmethod
    def _refuse(device: str, action: str) -> None:
        logger.warning("Refusing %s for unauthorized device %s", action, device)
        raise Exception(AGENT_PAIRING_REFUSED_ERROR)

    def get_pin(self, device: str) -> str | None:
        """Helper to invoke pin callback directly."""
        pin = self.pin_callback(device)
        return None if pin is None else str(pin)

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
        if pin is None:
            self._refuse(device, "PIN pairing")
        return str(pin)

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # type: ignore[name-defined]
        logger.info("DisplayPinCode for %s: %s", device, pincode)
        self.active_requests[device] = {"type": "display_pin", "pincode": pincode}

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # type: ignore[name-defined]
        logger.info("RequestPasskey received for device %s", device)
        passkey = self.passkey_callback(device)
        if passkey is None:
            self._refuse(device, "passkey pairing")
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
        if not self.confirm_callback(device, passkey):
            self._refuse(device, "pairing confirmation")

    @method()
    def RequestAuthorization(self, device: "o"):  # type: ignore[name-defined]
        logger.info("RequestAuthorization for %s", device)
        if not self.authorization_callback(device):
            self._refuse(device, "connection authorization")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # type: ignore[name-defined]
        logger.info("AuthorizeService for %s (UUID: %s)", device, uuid)
        if not self.authorization_callback(device):
            self._refuse(device, "service authorization")

    @method()
    def Cancel(self):
        logger.info("Agent request cancelled by BlueZ")
        self.active_requests.clear()
