-- WirePlumber 0.4.x BlueZ Configuration for BL-HAOS
bluez_monitor.properties = {
  ["bluez5.enable-sbc-xq"] = true,
  ["bluez5.enable-volume-sync"] = true,
  ["bluez5.enable-hw-volume"] = true,
  ["bluez5.codecs"] = "[ ldac aptx_hd aptx aac sbc_xq sbc ]",
  ["bluez5.default.rate"] = 48000,
}

bluez_monitor.rules = {
  {
    matches = {
      {
        { "device.name", "matches", "bluez_card.*" },
      },
    },
    apply_properties = {
      ["bluez5.auto-connect"] = "[ a2dp_sink ]",
      ["bluez5.hw-volume"] = "[ a2dp_sink ]",
    },
  },
}
