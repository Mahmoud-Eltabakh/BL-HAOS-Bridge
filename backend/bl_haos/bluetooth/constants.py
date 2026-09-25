"""BlueZ D-Bus Interfaces, UUIDs, and Constants."""

BLUEZ_SERVICE = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# The D-Bus daemon itself, used to register signal match rules.
DBUS_DAEMON_SERVICE = "org.freedesktop.DBus"
DBUS_DAEMON_PATH = "/org/freedesktop/DBus"

# A D-Bus match rule is required before the bus daemon will deliver BlueZ's
# broadcast signals to this connection. dbus-fast only installs match rules
# automatically for high-level proxy `on_<Signal>` handlers, so a bare
# `add_message_handler()` receives nothing and discovery appears dead.
BLUEZ_SIGNAL_MATCH_RULES = (
    f"type='signal',sender='{BLUEZ_SERVICE}',interface='{DBUS_OM_IFACE}'",
    f"type='signal',sender='{BLUEZ_SERVICE}',interface='{DBUS_PROPERTIES_IFACE}',member='PropertiesChanged'",
)

ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
MEDIA_INTERFACE = "org.bluez.Media1"
MEDIA_CONTROL_INTERFACE = "org.bluez.MediaControl1"
MEDIA_ENDPOINT_INTERFACE = "org.bluez.MediaEndpoint1"

AGENT_PATH = "/org/bl_haos/agent"

# Bluetooth Service UUIDs
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"
ADVANCED_AUDIO_UUID = "0000110d-0000-1000-8000-00805f9b34fb"
AVRCP_REMOTE_UUID = "0000110e-0000-1000-8000-00805f9b34fb"
AVRCP_TARGET_UUID = "0000110c-0000-1000-8000-00805f9b34fb"
AVRCP_UUID = "0000110f-0000-1000-8000-00805f9b34fb"
HFP_HF_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HFP_AG_UUID = "0000111f-0000-1000-8000-00805f9b34fb"
HSP_HS_UUID = "00001108-0000-1000-8000-00805f9b34fb"
HSP_AG_UUID = "00001112-0000-1000-8000-00805f9b34fb"
HEADSET_HS_UUID = "00001131-0000-1000-8000-00805f9b34fb"

# 16-bit audio service identifiers. They are matched both standalone and as a
# substring of a full 128-bit UUID, so the short form is the single source.
AUDIO_SERVICE_SHORT_UUIDS = (
    "110a", "110b", "110c", "110d", "110e", "110f",
    "1108", "1112", "111e", "111f", "1131",
)

AUDIO_SINK_UUIDS = {
    A2DP_SINK_UUID.lower(),
    A2DP_SOURCE_UUID.lower(),
    ADVANCED_AUDIO_UUID.lower(),
    AVRCP_TARGET_UUID.lower(),
    AVRCP_REMOTE_UUID.lower(),
    AVRCP_UUID.lower(),
    HFP_HF_UUID.lower(),
    HFP_AG_UUID.lower(),
    HSP_HS_UUID.lower(),
    HSP_AG_UUID.lower(),
    HEADSET_HS_UUID.lower(),
    *AUDIO_SERVICE_SHORT_UUIDS,
    *(f"0x{short_uuid}" for short_uuid in AUDIO_SERVICE_SHORT_UUIDS),
}

# Major Device Classes (bits 8-12 of Class of Device)
# CoD & 0x1F00
MAJOR_DEVICE_CLASS_AUDIO_VIDEO = 0x0400

# Minor Audio/Video Classes (bits 2-7 of Class of Device)
MINOR_DEVICE_CLASSES_AUDIO = {
    0x0404: "Wearable Headset",
    0x0408: "Handsfree Device",
    0x0414: "Loudspeaker",
    0x0418: "Headphones",
    0x041C: "Portable Audio",
    0x0420: "Car Audio",
    0x0424: "Set-top Box",
    0x0428: "HiFi Audio Device",
}
