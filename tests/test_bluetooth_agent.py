import pytest
from backend.bl_haos.bluetooth.agent import BlueZAgent


def test_bluez_agent_refuses_every_pairing_request_by_default():
    """Regression: the agent used to answer PIN 0000/passkey 0 and auto-confirm.

    That let any device in radio range pair while the adapter was pairable, with
    no operator involvement (see THREAT-MODEL.md, T3).
    """
    agent = BlueZAgent()
    dev_path = "/org/bluez/hci0/dev_11_22_33_44_55_66"

    assert agent.get_pin(dev_path) is None
    assert agent.confirm_passkey(dev_path, 123456) is False
    for call in (
        lambda: agent.RequestPinCode.__wrapped__(agent, dev_path),
        lambda: agent.RequestPasskey.__wrapped__(agent, dev_path),
        lambda: agent.RequestConfirmation.__wrapped__(agent, dev_path, 123456),
        lambda: agent.RequestAuthorization.__wrapped__(agent, dev_path),
        lambda: agent.AuthorizeService.__wrapped__(agent, dev_path, "0000110b-0000-1000-8000-00805f9b34fb"),
    ):
        with pytest.raises(Exception, match="Rejected"):
            call()


def test_bluez_agent_answers_only_what_the_callbacks_authorize():
    dev_path = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    agent = BlueZAgent(
        pin_callback=lambda device: "1234",
        passkey_callback=lambda device: 4321,
        confirm_callback=lambda device, key: True,
        authorization_callback=lambda device: True,
    )

    assert agent.get_pin(dev_path) == "1234"
    assert agent.RequestPinCode.__wrapped__(agent, dev_path) == "1234"
    assert agent.RequestPasskey.__wrapped__(agent, dev_path) == 4321
    assert agent.confirm_passkey(dev_path, 123456) is True
    agent.RequestConfirmation.__wrapped__(agent, dev_path, 123456)
    agent.RequestAuthorization.__wrapped__(agent, dev_path)
    agent.AuthorizeService.__wrapped__(agent, dev_path, "0000110b-0000-1000-8000-00805f9b34fb")

    agent_reject = BlueZAgent(
        pin_callback=lambda device: None,
        confirm_callback=lambda device, key: False,
        authorization_callback=lambda device: False,
    )
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
