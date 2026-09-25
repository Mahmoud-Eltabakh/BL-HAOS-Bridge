-- WirePlumber 0.4.x - keep Bluetooth nodes out of suspend-on-idle.
--
-- WirePlumber suspends an idle audio node after 5 seconds by default. Resuming
-- an A2DP sink is not free: the transport is re-acquired and the codec is
-- re-negotiated, which on this hardware (LDAC/aptX on a Pi-class CPU) costs
-- several seconds. That is exactly the delay heard on the *first* play after a
-- speaker connects or after a quiet period, because the sink had already been
-- torn down again.
--
-- `session.suspend-timeout-seconds = 0` disables suspension for the matched
-- nodes, so the A2DP link stays configured between plays and playback starts
-- immediately. The trade-off is that an idle speaker keeps its radio link up;
-- that is the intended behaviour for a speaker bridge.
--
-- WirePlumber 0.5 renamed these keys (`bluez_monitor` -> `monitor.bluez`,
-- `apply_properties` -> `update-props`). This add-on ships Debian bookworm,
-- which provides WirePlumber 0.4.x, so the lua form below is the active one.

bluez_monitor = bluez_monitor or { properties = {}, rules = {} }
bluez_monitor.rules = bluez_monitor.rules or {}

table.insert(bluez_monitor.rules, {
  matches = {
    {
      -- A2DP sink nodes: what this add-on streams into (bluez_output.<MAC>.*).
      { "node.name", "matches", "bluez_output.*" },
    },
    {
      -- A2DP/HFP capture nodes, kept in the same policy for symmetry.
      { "node.name", "matches", "bluez_input.*" },
    },
  },
  apply_properties = {
    ["session.suspend-timeout-seconds"] = 0,
  },
})
