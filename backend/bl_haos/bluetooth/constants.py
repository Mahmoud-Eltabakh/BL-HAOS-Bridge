"""BlueZ D-Bus Interfaces, UUIDs, and Constants."""

BLUEZ_SERVICE = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

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
AVRCP_REMOTE_UUID = "0000110e-0000-1000-8000-00805f9b34fb"
AVRCP_TARGET_UUID = "0000110c-0000-1000-8000-00805f9b34fb"
HFP_HF_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HSP_HS_UUID = "00001108-0000-1000-8000-00805f9b34fb"

AUDIO_SINK_UUIDS = {
    A2DP_SINK_UUID.lower(),
    AVRCP_TARGET_UUID.lower(),
    AVRCP_REMOTE_UUID.lower(),
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
