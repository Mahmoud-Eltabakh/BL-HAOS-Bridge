from pathlib import Path

def test_pipewire_clock_config():
    clock_conf = Path("rootfs/etc/pipewire/pipewire.conf.d/10-clock.conf")
    assert clock_conf.exists(), "10-clock.conf must exist"

    content = clock_conf.read_text(encoding="utf-8")
    assert "default.clock.rate = 48000" in content
    assert "default.clock.quantum = 1024" in content
    assert "default.clock.min-quantum = 512" in content
    assert "default.clock.max-quantum = 2048" in content

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
