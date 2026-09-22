import pytest
from backend.bl_haos.bluetooth.agent import BlueZAgent

def test_bluez_agent_pin_callback():
    custom_pin = "1234"
    agent = BlueZAgent(pin_callback=lambda dev: custom_pin)

    dev_path = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    assert agent.get_pin(dev_path) == "1234"
    assert agent.RequestPinCode.__wrapped__(agent, dev_path) == "1234"

def test_bluez_agent_confirmation():
    agent = BlueZAgent(confirm_callback=lambda dev, key: True)
    dev_path = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    assert agent.confirm_passkey(dev_path, 123456) is True
    agent.RequestConfirmation.__wrapped__(agent, dev_path, 123456)

    agent_reject = BlueZAgent(confirm_callback=lambda dev, key: False)
    assert agent_reject.confirm_passkey(dev_path, 123456) is False
    with pytest.raises(Exception, match="Rejected"):
        agent_reject.RequestConfirmation.__wrapped__(agent_reject, dev_path, 123456)

def test_bluez_agent_display_and_cancel():
    agent = BlueZAgent()
    dev_path = "/org/bluez/hci0/dev_11_22_33_44_55_66"

    agent.DisplayPinCode.__wrapped__(agent, dev_path, "9999")
    assert dev_path in agent.active_requests
    assert agent.active_requests[dev_path]["pincode"] == "9999"

    agent.Cancel.__wrapped__(agent)
    assert len(agent.active_requests) == 0
