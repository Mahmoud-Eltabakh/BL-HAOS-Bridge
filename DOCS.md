# Home Assistant Add-on: BL-HAOS (Bluetooth Audio Adapter)

BL-HAOS turns your Home Assistant OS device into a Bluetooth audio transmitter. It enables streaming Home Assistant TTS announcements, local music files, web radio, and Music Assistant directly to any paired Bluetooth speakers with low latency, audiophile-grade codecs, and rock-solid background reconnection.

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
3. Home Assistant discovers the add-on automatically. Confirm the **BL-HAOS Bluetooth Audio** setup prompt.

The integration connects through Home Assistant's private add-on network before creating entities for trusted Bluetooth speakers. It is not installed automatically, but it is discovered once installed.

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

### Multiple Speakers
- Every connected trusted speaker is an independent `media_player` entity and can stream its own media at its own volume.
- To play the same source on several speakers, group those entities in Home Assistant (a media player group helper) or drive them from Music Assistant.
- Bluetooth A2DP is a point-to-point link, so this add-on does not provide sample-accurate synchronized multi-room playback. If you need phase-aligned rooms, run the community Snapcast add-on alongside Music Assistant instead.
- Speakers sharing one Bluetooth adapter also share that controller's radio bandwidth; several concurrent streams may exceed what a single adapter can carry reliably.

---

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Logging verbosity (`trace`, `debug`, `info`, `warning`, `error`). |
| `default_codec` | `auto` | Preferred Bluetooth A2DP audio codec. `auto` prioritizes highest fidelity supported by speaker: LDAC -> aptX HD -> aptX -> AAC -> SBC-XQ -> SBC. |

### Collecting Logs

For an incident, temporarily set the add-on `log_level` to `debug`, restart the add-on, reproduce the issue once, and export its log with the Home Assistant Terminal & SSH add-on:

```sh
ha addons logs bl_haos > /config/bl-haos-addon.log
```

The native Home Assistant integration runs in Home Assistant Core and therefore has a separate log. When the integration was discovered through the add-on, its logger level follows this same add-on setting through Supervisor discovery. For a manually configured entry, or to temporarily override the level, enable its logger in `configuration.yaml`, restart Home Assistant Core, reproduce the issue once, and export the core log:

```yaml
logger:
  default: warning
  logs:
    custom_components.bl_haos: debug
```

```sh
ha core logs > /config/bl-haos-integration.log
```

The add-on log identifies BlueZ and PipeWire state; the Home Assistant log identifies native integration commands and entity state transitions. Remove the temporary debug logger after collecting evidence. Redact access tokens, media URLs, and complete Bluetooth addresses before sharing logs.

## Native Integration Verification

1. Take a Home Assistant backup before upgrading.
2. Install the HACS integration using the steps above, then restart Home Assistant Core and confirm the discovery prompt.
3. Verify the Ingress **Native integration** panel reports the bridge as ready.
4. Confirm a trusted connected speaker appears as a native `media_player` entity.
5. Remove any old legacy-discovery entities manually after the native entity is working; BL-HAOS no longer publishes compatibility state or commands.

`query_ha.py` is read-only by default. It requires `HA_URL` and `HA_TOKEN` from the local process environment and never accepts a token as a command argument. After rotating any prior token, run `python query_ha.py` for preflight. Add-on lifecycle actions and `play-media` require their named command plus `--apply`; playback also requires an explicit native entity, media identifier, and media type. Do not record tokens, media URLs, raw WebSocket frames, or full device addresses in tickets or source control.

## Operational Status and Demo Mode

The Ingress dashboard keeps the operator workflow focused on adapter state, speaker discovery, connection, and native Home Assistant integration readiness. The standalone Diagnostics and Guided recovery panels are not part of the dashboard, and their operator-only routes are not exposed by the add-on. Runtime health telemetry remains available through the health endpoint for automated validation and support tooling.

For offline demonstrations and SIL validation, set `BLHAOS_DEMO_MODE=true` and choose one of `healthy`, `pairing_failure`, `sink_missing`, `reconnect_exhausted`, `native_integration_unavailable`, or `restart_degraded` with `BLHAOS_DEMO_SCENARIO`. Demo mode is off by default and injects fixed adapters, speakers, events, timestamps, and recovery outcomes before live D-Bus, PipeWire, or Home Assistant clients are initialized. Unknown scenarios are rejected.

---

## Hardware Requirements
- **Platform**: Home Assistant OS (HAOS) on Raspberry Pi 3/4/5, Intel NUC, x86_64 Mini PCs, or ODROID.
- **Bluetooth Controller**: Built-in onboard Bluetooth adapter (`hci0`) or external USB Bluetooth 5.0/5.3 dongles (`hci1..N`). Multiple adapters are automatically detected and managed.
