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

Pairing is answered only while the add-on is doing it. Clicking **Pair & Trust**
opens a 90-second window for that one address: until it closes, the adapter is
pairable and the BlueZ agent answers pairing prompts (using the PIN you supplied,
or `0000`); everything outside the window is refused with
`org.bluez.Error.Rejected`, and the adapter stops accepting pairing requests when
the window closes or expires. If pairing fails, put the speaker back into pairing
mode and try again — the window is per attempt, not permanent. The dashboard's
native status shows the address that may currently pair (`pairing_authorized_address`).

A speaker becomes a Home Assistant entity only once it is **Trusted** (what
**Pair & Trust** records). A device that merely pairs or connects — which any
device in radio range can arrange — is not published and is refused commands.

### A Speaker That Is Switched Off
A trusted speaker that is switched off or out of range stays where you left it:
its card remains on the dashboard with an **Offline** badge and its Home
Assistant entity reads *unavailable* instead of disappearing. BlueZ stops
reporting the device when it goes away, but the bridge keeps the last known
record — name, alias, adapter and trust — for exactly this reason, and the same
entity comes back by itself (with its history and automations intact) when the
speaker returns. **Remove** is what forgets a speaker for good, and it works
while the speaker is offline too.

### Stability: Disconnects, Reconnects and Quality
The bridge deliberately does not fight for a link:

- A dropout has to survive a **5-second grace window** before it is acted on. A link
  that returns inside it is a *flap*: nothing is reconnected and the event is counted.
- A speaker that drops **6 times in 2 minutes** gets a **60-second cooldown** instead
  of a reconnect loop.
- A link that has just come up is left alone for **15 seconds**, so a presence
  advertisement cannot fast-track a retry while the A2DP transport is still being
  negotiated.
- **Connect** (the button, and the recovery after a failed play) resets a stale A2DP
  transport on purpose. The automatic reconnect never does: a speaker that is already
  connected is reported as success instead of being disconnected and reconnected.

Every one of those decisions is observable, so a software flap can be told from a
radio problem:

- The log names the codec on every playback (`... using A2DP codec ldac`). Because
  every reconnect re-negotiates the codec, a quality drop that coincides with a
  reconnect is usually a codec change rather than a bad link.
- `GET /api/health` reports per speaker: `connects`, `disconnects`,
  `suppressed_flaps`, `codec`, `link_reason`. A rising `suppressed_flaps` with a low
  `disconnects` means the bridge was right not to act; a high `disconnects` means the
  link really is going away (range, another device taking the speaker, or the
  speaker's own power saving).
- To pin a codec, set the per-speaker codec in the dashboard. The bridge applies it
  on the next playback and warns if the speaker does not offer it.

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

## Security

The add-on is deliberately privileged — it runs as root with `full_access` and
host D-Bus access, because driving host Bluetooth and audio is what it does — so the
release is built around keeping anything remote away from those privileges: pairing is
operator-gated, media targets are constrained, and the native credential is never
exposed by the API.

**Pairing** is operator-gated, as described above.

**The native credential.** The integration authenticates with a 32-byte token
generated on first start and stored at `/data/bl_haos_config.json` (mode `0600`).
It is never returned by the API. To rotate it:

1. Stop the add-on.
2. Delete `/data/bl_haos_config.json`, or set a new value in the add-on's
   `BLHAOS_NATIVE_TOKEN` environment variable (take a backup of the file first if
   you want to keep aliases, volumes and adapter pins — they live in the same
   file; deleting it resets them).
3. Start the add-on. On boot it publishes the new credential through Supervisor
   discovery; the integration picks it up and reloads itself. If it does not,
   reopen the integration's configuration and re-add the entry (Settings →
   Devices & Services → BL-HAOS → **Reconfigure**/delete and re-add).
4. Confirm the change: `GET /api/native/identity` (with the new token) reports
   `credential_fingerprint`, the first 8 hex characters of the credential's
   SHA-256 digest — the same value before and after means nothing rotated. The
   integration also checks the endpoint identifies itself as `BL-HAOS` on
   `/api/health` *before* it sends the token anywhere.

Treat a token that has ever left the host — a screenshot, a chat message, a
support bundle, a repository — as compromised and rotate it.

**Media targets.** The add-on refuses to fetch audio from loopback, link-local
(including the cloud metadata address), multicast, reserved or unspecified
addresses, and pins the decoder to network protocols so a hostile playlist cannot
read local files. Private LAN addresses stay usable because Home Assistant serves
TTS and local media from one.

**Reporting.** Use GitHub's private vulnerability reporting on the module
repositories (**Security → Report a vulnerability**) rather than a public issue.

---

## Hardware Requirements
- **Platform**: Home Assistant OS (HAOS) on Raspberry Pi 3/4/5, Intel NUC, x86_64 Mini PCs, or ODROID.
- **Bluetooth Controller**: Built-in onboard Bluetooth adapter (`hci0`) or external USB Bluetooth 5.0/5.3 dongles (`hci1..N`). Multiple adapters are automatically detected and managed.
