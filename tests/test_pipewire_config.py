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

def test_wireplumber_logind_masked():
    logind_lua = Path("rootfs/etc/wireplumber/main.lua.d/20-logind.lua")
    assert logind_lua.exists(), "20-logind.lua must exist in /etc/wireplumber to mask logind in container"
    content = logind_lua.read_text(encoding="utf-8")
    assert "logind" in content.lower()
