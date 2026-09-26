from pathlib import Path


def test_wireplumber_codec_ranking():
    bluez_lua = Path("rootfs/etc/wireplumber/bluetooth.lua.d/50-bluez.lua")
    assert bluez_lua.exists(), "50-bluez.lua must exist"

    content = bluez_lua.read_text(encoding="utf-8")
    assert 'bluez5.enable-sbc-xq' in content
    assert 'ldac aptx_hd aptx aac sbc_xq sbc' in content

def test_wireplumber_volume_sync():
    """Volume sync is `hw-volume`, not a property that never existed.

    The config used to set `bluez5.enable-volume-sync`, which no WirePlumber
    release defines: volume sync to the speaker's own AVRCP volume comes from
    `bluez5.enable-hw-volume` plus the per-profile `bluez5.hw-volume`, so the
    dead line is gone and the tests assert what actually does the work.
    """
    bluez_lua = Path("rootfs/etc/wireplumber/bluetooth.lua.d/50-bluez.lua")
    assert bluez_lua.exists(), "50-bluez.lua must exist"

    content = bluez_lua.read_text(encoding="utf-8")
    assert 'bluez5.enable-volume-sync"]' not in content, "not a real property; do not resurrect it"
    assert 'bluez5.enable-hw-volume' in content
    assert '["bluez5.hw-volume"] = "[ a2dp_sink a2dp_source ]"' in content
    assert 'a2dp_sink' in content


def test_wireplumber_asks_for_the_rate_the_player_feeds():
    """The link and the player must agree on 48 kHz, or something resamples.

    `bluez5.default.rate` is a real WirePlumber 0.4 property ("the bluetooth
    default audio rate"), and the player feeds the same 48 kHz. A speaker that
    negotiates 44.1 kHz anyway is reported by the playback log line instead of
    being silently converted at the server's default resampler quality.
    """
    from backend.bl_haos.config import PlayerSettings

    content = Path("rootfs/etc/wireplumber/bluetooth.lua.d/50-bluez.lua").read_text(encoding="utf-8")

    fed_rate = PlayerSettings.model_fields["sample_rate_hz"].default
    assert f'bluez5.default.rate"] = {fed_rate}' in content


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
