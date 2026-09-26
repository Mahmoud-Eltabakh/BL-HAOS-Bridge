"""Single source of truth for bridge-wide literals.

Identity strings, filesystem locations, environment variable names, event
vocabulary, protocol vocabulary, limits, and timeouts are declared here once and
referenced everywhere else. Nothing in this module may import from the rest of
the package, so it stays safe to import from any layer.
"""

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------
APP_NAME = "BL-HAOS"
APP_TITLE = "BL-HAOS Bluetooth Audio Adapter"
APP_DESCRIPTION = "High-fidelity Bluetooth Audio Adapter for Home Assistant OS"
APP_SLUG = "bl_haos"
ADDON_SLUG = "bl-haos"
# Single source of truth for the runtime version. ``backend/tests/test_addon_config.py``
# asserts it matches ``config.yaml`` so the add-on manifest and the daemon cannot
# drift apart.
VERSION = "0.2.58"

# Logger namespace shared by every bridge module and the log format the daemon
# installs at startup.
LOGGER_NAME = APP_SLUG
LOG_FORMAT = "[bl-haos] %(asctime)s %(levelname)s [%(name)s.%(funcName)s]: %(message)s"
LOG_LEVEL_INFO = "INFO"

# ---------------------------------------------------------------------------
# HTTP / WebSocket contract
# ---------------------------------------------------------------------------
API_PREFIX = "/api"
NATIVE_API_PREFIX = f"{API_PREFIX}/native"
ROOT_PATH = ""
WS_PUBLIC_PATH = "/ws"
WS_NATIVE_PATH = f"{WS_PUBLIC_PATH}/native"
BEARER_PREFIX = "Bearer "
WS_POLICY_VIOLATION_CODE = 1008
WS_HEALTH_DEFAULT_VERSION = 1
WS_PING = "ping"
WS_PONG = "pong"

# ---------------------------------------------------------------------------
# Native transport identity
# ---------------------------------------------------------------------------
NATIVE_BRIDGE_ID = f"{APP_SLUG}_native_bridge"
NATIVE_BRIDGE_VERSION = 1
NATIVE_COMMAND_VERSION = 1

# ---------------------------------------------------------------------------
# Playback vocabulary
# ---------------------------------------------------------------------------
PLAYBACK_IDLE = "idle"
PLAYBACK_PLAYING = "playing"
PLAYBACK_PAUSED = "paused"
PLAYBACK_STOPPED = "stopped"

# Command verbs accepted on the native transport.
COMMAND_PLAY = "play"
COMMAND_PAUSE = "pause"
COMMAND_STOP = "stop"
COMMAND_SET_VOLUME = "set_volume"
COMMAND_PLAY_MEDIA = "play_media"

# Legacy Home Assistant service-command vocabulary (``PLAY``, ``VOLUME:0.5``).
HA_COMMAND_PLAY = "PLAY"
HA_COMMAND_PAUSE = "PAUSE"
HA_COMMAND_STOP = "STOP"
HA_COMMAND_PLAY_MEDIA_PREFIX = "PLAY_MEDIA:"
HA_COMMAND_VOLUME_PREFIX = "VOLUME:"

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------
EVENT_HEALTH = "health"
EVENT_TELEMETRY = "telemetry"
EVENT_SPEAKER_UPDATED = "speaker_updated"
EVENT_HEALTH_OBSERVATION = "health_observation"
EVENT_DEMO_SCENARIO = "demo_scenario"
EVENT_DEVICE_UPDATED = "device_updated"
EVENT_DEVICE_DISCOVERED = "device_discovered"
EVENT_DBUS_DISCONNECTED = "dbus_disconnected"
RECOVERY_HEALTHY = "healthy"
RECOVERY_PENDING = "pending"

# ---------------------------------------------------------------------------
# Filesystem locations
# ---------------------------------------------------------------------------
CONFIG_DIRECTORY = "/data"
CONFIG_FALLBACK_DIRECTORY = "/tmp"
CONFIG_FILE_NAME = f"{APP_SLUG}_config.json"
DEFAULT_CONFIG_PATH = f"{CONFIG_DIRECTORY}/{CONFIG_FILE_NAME}"
FALLBACK_CONFIG_PATH = f"{CONFIG_FALLBACK_DIRECTORY}/{CONFIG_FILE_NAME}"
CONFIG_TEMP_SUFFIX = ".tmp"
CONFIG_FILE_MODE = 0o600
STATIC_INDEX_FILE = "index.html"
STATIC_DIRECTORY_CANDIDATES = ("web_ui/dist", "/var/www/bl-haos", "frontend/dist")
BLUEZ_ROOT_PATH = "/org/bluez"
BLUEZ_ROOT_PATH_TRAILER = "/"
SUPERVISOR_DISCOVERY_URL = "http://supervisor/discovery"

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
ENV_LOG_LEVEL = "LOG_LEVEL"
ENV_SUPERVISOR_TOKEN = "SUPERVISOR_TOKEN"
ENV_NATIVE_TOKEN = "BLHAOS_NATIVE_TOKEN"
ENV_DEMO_MODE = "BLHAOS_DEMO_MODE"
ENV_DEMO_SCENARIO = "BLHAOS_DEMO_SCENARIO"
TRUTHY_FLAGS = frozenset({"1", "true", "yes", "on"})

# ---------------------------------------------------------------------------
# Settings defaults and bounds
# ---------------------------------------------------------------------------
DEFAULT_LOG_LEVEL = "info"
DEFAULT_CODEC = "auto"
SUPPORTED_CODECS = frozenset({"auto", "sbc", "sbc_xq", "aac", "aptx", "aptx_hd", "ldac"})
DEFAULT_DEMO_SCENARIO = "healthy"
VOLUME_MIN_PERCENT = 0
VOLUME_MAX_PERCENT = 100
DEFAULT_VOLUME_PERCENT = 70
VOLUME_MIN_RATIO = 0.0
VOLUME_MAX_RATIO = 1.0
DEFAULT_VOLUME_RATIO = DEFAULT_VOLUME_PERCENT / VOLUME_MAX_PERCENT
MAX_ALIAS_LENGTH = 128
TOKEN_ENTROPY_BYTES = 32

# ---------------------------------------------------------------------------
# Input validation limits
# ---------------------------------------------------------------------------
MAX_DETAIL_LENGTH = 256
MAX_MEDIA_URL_LENGTH = 2048
MAX_MEDIA_TYPE_LENGTH = 128
MAX_IDENTIFIER_LENGTH = 64
OMITTED_PLACEHOLDER = "[omitted]"
BOUNDED_STRING_LENGTH = 256
MAX_DIAGNOSTICS_DEPTH = 4
MAX_ADDRESS_LENGTH = 17

# ---------------------------------------------------------------------------
# Health component identifiers
# ---------------------------------------------------------------------------
COMPONENT_BLUETOOTH = "bluetooth"
COMPONENT_PIPEWIRE = "pipewire"
COMPONENT_NATIVE_BRIDGE = "native_bridge"
COMPONENT_HEALTH = "health"
COMPONENT_LIFECYCLE = "lifecycle"
COMPONENT_SPEAKER = "speaker"
COMPONENT_DEMO = "demo"

# ---------------------------------------------------------------------------
# Health observation sources
# ---------------------------------------------------------------------------
SOURCE_BLUEZ = "bluez"
SOURCE_STARTUP = "startup"
SOURCE_DEMO = "demo"
SOURCE_LIFECYCLE = "lifecycle"

# ---------------------------------------------------------------------------
# Supervised operations
# ---------------------------------------------------------------------------
SUPERVISOR_DISCOVERY_TIMEOUT_SECONDS = 5
A2DP_SINK_RETRY_ATTEMPTS = 20
A2DP_SINK_RETRY_INTERVAL = 0.5
DEFAULT_PIN = "0000"
PIN_DIGITS_MIN = 4
PIN_DIGITS_MAX = 8
# Bluetooth pairing is answered only inside this window, and only for the device
# the operator explicitly asked to pair. Anything else is refused, so a device in
# radio range cannot pair by itself (see THREAT-MODEL.md, T3).
PAIRING_WINDOW_SECONDS = 90

# ---------------------------------------------------------------------------
# Reconnect policy
# ---------------------------------------------------------------------------
RECONNECT_INITIAL_BACKOFF_SECONDS = 1.0
RECONNECT_BACKOFF_MULTIPLIER = 1.5
RECONNECT_MIN_BACKOFF_SECONDS = 0.5
RECONNECT_MAX_BACKOFF_SECONDS = 20.0
RECONNECT_POLL_INTERVAL_SECONDS = 0.5
RECONNECT_MAX_FAILURES_BEFORE_BREAKER = 8
RECONNECT_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 10.0
RECONNECT_SELF_HEAL_FAILURE_THRESHOLD = 2
RECONNECT_MIN_FAILURES_AFTER_HEAL = 3
RECONNECT_BACKOFF_JITTER = 0.15

# ---------------------------------------------------------------------------
# Redaction vocabulary
# ---------------------------------------------------------------------------
SECRET_TOKENS = ("token", "password", "secret", "authorization", "bearer")
SENSITIVE_QUERY_KEY_TOKENS = (*SECRET_TOKENS, "api[_-]?key", "key")
# Characters of the credential digest reported by diagnostics. A short prefix is
# enough to tell one credential from another (and to see that it rotated) without
# publishing anything that shortens a guess of the 32-byte secret.
TOKEN_FINGERPRINT_CHARS = 8
REDACTED_PLACEHOLDER = "[redacted]"
URL_PATTERN = r"[a-z]+://[^\s]+"
URL_REDACTION_PLACEHOLDER = "[url redacted]"

# ---------------------------------------------------------------------------
# Protocol limits
# ---------------------------------------------------------------------------
HTTP_SCHEMES = frozenset({"http", "https"})
MIN_TCP_PORT = 1
MAX_TCP_PORT = 65535
# Media URLs the bridge refuses to fetch. Loopback, link-local (which includes
# the cloud metadata address), multicast, reserved and unspecified targets are
# never a legitimate audio source - and 127.0.0.1 is the bridge's own
# unauthenticated API, which is otherwise reachable from inside the container
# even though the Supervisor refuses unauthenticated Ingress requests.
# Private LAN ranges stay allowed on purpose: Home Assistant itself serves TTS
# and local media from a private address.
MEDIA_BLOCKED_HOST_NAMES = (
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
)
MEDIA_LOCALHOST_SUFFIX = ".localhost"
# Protocols the media decoder may use. `file`, `concat`, `subfile`, `pipe` and
# `fd` are deliberately absent: an attacker-supplied manifest must not be able
# to pull the bridge's own filesystem into a stream.
PLAYBACK_PROTOCOL_WHITELIST = "http,https,tcp,tls,crypto,data,httpproxy"
MIN_PORTABLE_CODEPOINT = 32
DELETE_CODEPOINT = 127
BROADCAST_ADDRESS_OCTET = 0xFF
MULTICAST_ADDRESS_BIT = 0x01

# ---------------------------------------------------------------------------
# Contract versions
# ---------------------------------------------------------------------------
HEALTH_CONTRACT_VERSION = 1
DIAGNOSTICS_CONTRACT_VERSION = 1
DIAGNOSTICS_EVENT_VERSION = 1

# ---------------------------------------------------------------------------
# Bluetooth vocabulary
# ---------------------------------------------------------------------------
ADDRESS_TYPE_PUBLIC = "public"
AGENT_CAPABILITY = "DisplayYesNo"
ADAPTER_NAME_FALLBACK = "hci0"
DEVICE_PATH_PREFIX = "dev_"
BLUETOOTH_ADAPTER_LABEL = "Bluetooth Adapter"
BLUETOOTH_DEVICE_LABEL = "Bluetooth Device"
AUDIO_DEVICE_LABEL = "Audio Device"
UNKNOWN_DEVICE_TYPE = "Unknown"
SPEAKER_DEVICE_TYPE = "Speaker"
HEADPHONES_DEVICE_TYPE = "Headphones"
HEADSET_DEVICE_TYPE = "Headset"
CLASS_OF_DEVICE_MAJOR_MASK = 0x1F00
CLASS_OF_DEVICE_MINOR_MASK = 0x1FFC
AUDIO_ICON_HINTS = ("audio", "sound", "speaker", "headphone", "headset")
NON_AUDIO_ICON_HINTS = ("input-keyboard", "input-mouse", "input-gaming")
AUDIO_NAME_KEYWORDS = (
    "speaker", "sound", "audio", "headphone", "headset", "earbuds", "airpods",
    "receiver", "adapter", "logitech", "soundbar", "soundlink", "jbl", "bose",
    "sony", "anker", "soundcore", "echo", "nest", "marshall", "sonos",
)
ICON_TO_DEVICE_TYPE = (
    ("speaker", SPEAKER_DEVICE_TYPE),
    ("headphone", HEADPHONES_DEVICE_TYPE),
    ("headset", HEADSET_DEVICE_TYPE),
)

