from pathlib import Path


def test_wireplumber_codec_ranking():
    bluez_lua = Path("rootfs/etc/wireplumber/bluetooth.lua.d/50-bluez.lua")
    assert bluez_lua.exists(), "50-bluez.lua must exist"

    content = bluez_lua.read_text(encoding="utf-8")
    assert 'bluez5.enable-sbc-xq' in content
    assert 'ldac aptx_hd aptx aac sbc_xq sbc' in content

def test_wireplumber_volume_sync():
    bluez_lua = Path("rootfs/etc/wireplumber/bluetooth.lua.d/50-bluez.lua")
    assert bluez_lua.exists(), "50-bluez.lua must exist"

    content = bluez_lua.read_text(encoding="utf-8")
    assert 'bluez5.enable-volume-sync' in content
    assert 'bluez5.enable-hw-volume' in content
    assert 'a2dp_sink' in content


def test_bluetooth_nodes_are_never_suspended_on_idle():
    """A suspended A2DP sink costs seconds to resume, heard as a slow first play.

    WirePlumber suspends an idle node after 5 seconds by default; resuming it
    re-acquires the transport and re-negotiates the codec. The no-suspend policy
    has to reach the sink *node*, which is matched by node.name (the WirePlumber
    0.4 lua form: bluez_monitor + apply_properties).
    """
    rule_file = Path("rootfs/etc/wireplumber/bluetooth.lua.d/51-bluez-no-suspend.lua")
    assert rule_file.exists(), "51-bluez-no-suspend.lua must exist"

    content = rule_file.read_text(encoding="utf-8")
    assert "bluez_monitor.rules" in content, "WirePlumber 0.4 lua form required for this image"
    assert '"node.name", "matches", "bluez_output.*"' in content, "sink nodes must be matched"
    assert "session.suspend-timeout-seconds" in content
    assert '["session.suspend-timeout-seconds"] = 0' in content, "0 disables suspension"


def test_suspend_policy_is_declared_on_the_card_rule_too():
    """Belt and braces: whether a rule property lands on the node or the card
    object depends on the WirePlumber build, so both carry the policy."""
    content = Path("rootfs/etc/wireplumber/bluetooth.lua.d/50-bluez.lua").read_text(encoding="utf-8")

    assert 'bluez5.auto-connect' in content
    assert '["session.suspend-timeout-seconds"] = 0' in content
