# Home Assistant Add-on: BL-HAOS (Bluetooth Audio Adapter)

BL-HAOS turns your Home Assistant OS device into a Bluetooth audio transmitter and multi-room broadcasting hub. It enables streaming Home Assistant TTS announcements, local music files, web radio, and Music Assistant directly to any paired Bluetooth speakers with low latency, audiophile-grade codecs, and rock-solid background reconnection.

---

## Installation & Setup in Home Assistant OS

### 1. Add this Repository to Home Assistant
1. In Home Assistant, navigate to **Settings** -> **Add-ons** -> **Add-on Store**.
2. Click the three dots in the top-right corner and select **Repositories**.
3. Add the repository URL: `https://github.com/Mahmoud-Eltabakh/BL-HAOS-Bridge`
4. Click **Add** and then **Close**.

### 2. Install the Add-on
1. Find **BL-HAOS Bluetooth Audio Adapter** in the Add-on Store list.
2. Click **Install** and wait for the build/installation to complete.
3. Enable **Start on boot** and **Show in sidebar** (for instant Ingress UI access).
4. Click **Start**.

### 3. Install the Native Media Player Integration
1. In HACS, add `https://github.com/Mahmoud-Eltabakh/BL-HAOS-Integration` as a custom **Integration** repository and install **BL-HAOS Bluetooth Audio**.
2. Restart Home Assistant Core.
3. Generate a long random value, save it as the add-on `bridge_token`, and restart BL-HAOS.
4. Go to **Settings** -> **Devices & services** -> **Add integration**, then add **BL-HAOS Bluetooth Audio**. Enter the add-on local HTTP endpoint and the same bridge token.

The integration authenticates the local REST/WebSocket bridge before creating entities for trusted Bluetooth speakers. It is not installed or discovered automatically by the add-on.

---

## How to Use

### Pairing Bluetooth Speakers
1. Click **BL-HAOS** in your Home Assistant sidebar.
2. Click **Add Speaker** to open the Bluetooth Discovery Scanner.
3. Put your Bluetooth speaker into pairing mode.
4. When your speaker appears in the list with its signal strength (RSSI), click **Pair & Trust**.
5. Once paired, click **Connect** on the speaker card.

### Playing Audio from Home Assistant
- Each connected trusted speaker appears as a native `media_player` entity after the BL-HAOS integration is configured.
- Use standard Home Assistant Lovelace media cards, automation actions (`media_player.play_media`, `tts.speak`), or Music Assistant to send audio directly to your speaker.
- Volume adjustments in Home Assistant automatically synchronize with physical speaker hardware buttons via AVRCP.

### Multi-Room Audio Synchronization
- Group multiple Bluetooth speakers in the BL-HAOS Ingress dashboard.
- Integrated Snapcast synchronization ensures sample-accurate, acoustic phase-aligned playback across all grouped speakers simultaneously.
- Fine-tune individual speaker latency offsets (+/- ms) in the settings modal if room distance or Bluetooth buffer differences occur.

---

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Logging verbosity (`trace`, `debug`, `info`, `warning`, `error`). |
| `default_codec` | `auto` | Preferred Bluetooth A2DP audio codec. `auto` prioritizes highest fidelity supported by speaker: LDAC -> aptX HD -> aptX -> AAC -> SBC-XQ -> SBC. |
| `bridge_token` | `""` | Required long random credential for the separate HACS integration. Configure the same value in its config flow; do not put it in logs or source control. |

## Native Integration Verification

1. Take a Home Assistant backup before upgrading.
2. Install the HACS integration using the steps above, configure the local endpoint and bridge token, then restart Home Assistant Core.
3. Verify the Ingress **Native integration** panel reports the bridge and credential as ready.
4. Confirm a trusted connected speaker appears as a native `media_player` entity.
5. Remove any old legacy-discovery entities manually after the native entity is working; BL-HAOS no longer publishes compatibility state or commands.

`query_ha.py` is read-only by default. It requires `HA_URL` and `HA_TOKEN` from the local process environment and never accepts a token as a command argument. After rotating any prior token, run `python query_ha.py` for preflight. Add-on lifecycle actions and `play-media` require their named command plus `--apply`; playback also requires an explicit native entity, media identifier, and media type. Do not record tokens, media URLs, raw WebSocket frames, or full device addresses in tickets or source control.

---

## Hardware Requirements
- **Platform**: Home Assistant OS (HAOS) on Raspberry Pi 3/4/5, Intel NUC, x86_64 Mini PCs, or ODROID.
- **Bluetooth Controller**: Built-in onboard Bluetooth adapter (`hci0`) or external USB Bluetooth 5.0/5.3 dongles (`hci1..N`). Multiple adapters are automatically detected and managed.
