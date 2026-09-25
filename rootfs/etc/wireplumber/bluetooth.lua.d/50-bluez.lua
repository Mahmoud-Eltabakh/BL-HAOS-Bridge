-- WirePlumber 0.4.x BlueZ Configuration for BL-HAOS
bluez_monitor = bluez_monitor or { properties = {}, rules = {} }
bluez_monitor.properties = bluez_monitor.properties or {}
bluez_monitor.rules = bluez_monitor.rules or {}

bluez_monitor.properties["bluez5.roles"] = "[ a2dp_sink a2dp_source ]"
bluez_monitor.properties["bluez5.hfphsp-backend"] = "none"
bluez_monitor.properties["bluez5.enable-sbc-xq"] = true
bluez_monitor.properties["bluez5.enable-volume-sync"] = true
bluez_monitor.properties["bluez5.enable-hw-volume"] = true
bluez_monitor.properties["bluez5.codecs"] = "[ ldac aptx_hd aptx aac sbc_xq sbc ]"
bluez_monitor.properties["bluez5.default.rate"] = 48000

table.insert(bluez_monitor.rules, {
  matches = {
    {
      { "device.name", "matches", "bluez_card.*" },
    },
  },
  apply_properties = {
    ["bluez5.auto-connect"] = "[ a2dp_sink a2dp_source ]",
    ["bluez5.hw-volume"] = "[ a2dp_sink a2dp_source ]",
    -- Belt and braces with 51-bluez-no-suspend.lua: whether a rule property
    -- reaches the sink *node* or stays on the card object depends on the
    -- WirePlumber build, so the no-suspend policy is declared on both. It is
    -- ignored on objects that are neither, never the reverse.
    ["session.suspend-timeout-seconds"] = 0,
  },
})
