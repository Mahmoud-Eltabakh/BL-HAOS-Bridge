# Home Assistant Add-on: BL-HAOS (Bluetooth Audio Adapter)

## Phases 24-26 Local Media, Playlists, and Queue Behavior

The integrated 0.2.69 release, including the earlier reconnect fix, is undeployed.
Local Core 2026.10.3 tests do not prove real provider layout, session expiry or
physical audio. Deployment and further restarts require separate authorization;
the accepted temporary probe was removed after its one permitted restart.

After authorized installation choose **Open Media Catalog** beside Home Assistant
Native integration in Ingress. The link only opens HA's `/bl-haos-media` panel.
**Native Browse Media** is also available on the native speaker entity. In
**Play on**, select exactly one connected BL-HAOS speaker before browsing or
starting playback. HA owns provider authentication and reference resolution.
For a saved custom playlist, Home Assistant resolves every saved reference
before replacing the current queue. The bridge owns one in-memory queue per
speaker; custom items keep their saved order and provider shortcuts remain one
atomic queue item. The resolved playable request is transient between HA and
the bridge, not a catalog proxy request.

The authenticated Library panel launches saved playlists, while the Ingress
SpeakerCard and capability-enabled native media-player entity control the same
bridge queue. Queue start, Next, and Previous are each sent once. A lost
acknowledgement is ambiguous, so check the current queue before another explicit
action. Failed items are retained until the user explicitly retries or skips;
there is no automatic skip or retry. Stop, replacement, and speaker loss clear
the old session. Queues do not resume after an add-on restart.

Resolved playback URLs are never sent to the browser or written to the playlist
library. Local/mock verification does not establish production or audible
playback acceptance; this candidate remains undeployed and release/deployment
require separate authorization.

The panel does not store browser/add-on HA/provider credentials, use direct
provider APIs, or accept raw URLs. Existing native direct-URL support is not a
catalog fallback. The local Phase 25 candidate stores custom playlists as ordered
Home Assistant media references and one-reference provider-playlist shortcuts;
provider tracks are not copied. Explicit availability checks browse the exact HA
media reference only; they do not resolve or play media and never rewrite saved
IDs. Phase 26 queue behavior has local SIL/mock evidence only, not production
acceptance. T-24-15 remains OPEN/HIGH and T-24-16 remains OPEN/MEDIUM. Release
and deployment are not approved. Root TESTING.md separates local SIL from
authorized HAOS acceptance. Retired Docker E2E and fixture screenshots are not
host proof.

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
its card remains on the dashboard with an **Offline** badge, and BlueZ keeps
reporting the device because a paired device is not temporary. The bridge keeps
the last known record — name, alias, adapter and trust — for exactly this
reason, and the same entity comes back by itself (with its history and
automations intact) when the speaker returns.

The native record reports `connected`, `paired` and `detached`, so the Home
Assistant integration can tell the two offline cases apart: a paired speaker
that is switched off keeps its entity but is disabled (out of the state machine,
identity intact), while a speaker whose pairing is gone - BlueZ withdrew the
device, which it does for anything it treats as temporary - has its entity
removed completely. **Remove** in the dashboard is what forgets a speaker for
good, and it works while the speaker is offline too.

### Stability: Disconnects, Reconnects and Quality
The bridge deliberately does not fight for a link:

- A dropout has to survive a **5-second grace window** before it is acted on. A link
  that returns inside it is a *flap*: nothing is reconnected and the event is counted.
- A speaker that drops **6 times in 2 minutes** gets a **60-second cooldown** instead
  of a reconnect loop.
- A link that has just come up is left alone for **15 seconds**. Scheduled retries
  cannot start during this settle window.
- Each scheduled retry makes **one connection attempt**, bounded to **30 seconds**
  for the complete manager operation, including device resolution. Automatic and
  operator connects share ownership of the actual adapter; a competing request is
  deferred as busy rather than queued. Only an `AlreadyConnected` response may
  additionally activate the A2DP profile once; busy and other errors do not.
- After the grace window, retries start with a **10-second delay**, doubling after
  each failure (20, 40, 80, then 120 seconds). Up to **15% non-negative jitter**
  lengthens the delay, within the final **120-second cap**. Busy operations retain
  failure history and wait at least the current backoff or 15 seconds, whichever is
  longer. Neither busy nor RSSI/presence advertisements shorten a pending deadline
  or bypass flap cooldowns.
- **Eight failures** open the circuit breaker for **300 seconds**. Expiry allows
  one half-open scheduled attempt without clearing failure history; another failure
  reopens it for 300 seconds. Only a successful connection or an authoritative
  connected-device event clears failure pacing. Retry history is process-local.
- Automatic reconnect never disconnects/resets an existing link or removes pairing.
  It preserves the known speaker record and bond after failure; a specifically
  stale BlueZ object is resolved on a later request, not retried immediately.
  **Connect** (the button, and existing recovery after a failed play) still requests
  `reset_existing=True` under the same manager ownership and timeout, deliberately
  disconnecting/reconnecting a live link to reset a stale A2DP transport. If BlueZ
  has actually lost the pairing, use **Pair & Trust** with the speaker in pairing mode.

Hardware/HAOS reconnect soak is **pending authorized deployment**. Local fake-BlueZ
and SIL tests verify bounded calls and pacing, not that the observed live fault is
fixed. Power loss/return, busy BlueZ, repeated failures, operator reset, pairing
retention and playback stability still require that separate hardware validation.

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
- To pin a codec, set it per speaker in that speaker's **Settings → Audio Codec** in the
  dashboard (LDAC, aptX HD, aptX, AAC, SBC-XQ, SBC, or the default). The bridge applies
  it on the next playback and warns if the speaker does not offer it. For an LDAC link
  that changes quality as it plays, see
  [Audio stutters, flutters, or the quality changes](#audio-stutters-flutters-or-the-quality-changes).

### Audio stutters, flutters, or the quality changes

Audio that plays for a moment and then breaks up, or that quietly sounds worse as a
song goes on, is answered by three things: the codec, the buffer, and the host.

**The quality that changes mid-song is LDAC.** LDAC is the only A2DP codec here whose
bitrate moves while it plays, and its own default is Adaptive Bitrate: when the link
is marginal it lowers the bitrate (990 -> 660 -> 330 kbps) instead of dropping out, so
the same song sounds different as you listen. The add-on ships a fixed LDAC quality
instead (`sq`, a steady 660 kbps, still far above SBC) and exposes the choice:

| `ldac_quality` | Bitrate | When |
|---|---|---|
| `hq` | 990/909 kbps | A clean link and spare CPU; the highest quality LDAC offers. |
| `sq` | 660/606 kbps | The shipped default: steady, and the CPU headroom that encoding wants. |
| `mq` | 330/303 kbps | A weak link that still stutters at `sq`. |
| `auto` | adaptive | Upstream's default: the bitrate follows the link, so the quality changes. |

**Pin a codec per speaker** in that speaker's **Settings → Audio Codec** in the
dashboard. This is the strongest lever: LDAC at any quality needs both radio and CPU
headroom that a Pi-class board and a busy 2.4 GHz band do not always have, and a link
that is marginal for LDAC is usually fine on **SBC-XQ** (steady, good) or **SBC** (most
compatible). The bridge applies the pin on the next playback and warns if the speaker
does not offer it.

**The buffer** is the other half. The bridge gives both player clients the same
explicit buffer (500 ms by default) because an A2DP link delivers in bursts and the
speaker drains its own buffer at a fixed rate. Raise it with the **Playback Buffer
(ms)** add-on option (Settings &rarr; Add-ons &rarr; BL-HAOS &rarr; Configuration), or
with the `BLHAOS_PLAYBACK_BUFFER_MS` environment variable when the image is run
outside the Supervisor. The daemon re-reads it at start, so a restart applies it.

The value is bounded to 50-2000 ms on purpose. More buffer also means more audio
queued ahead of the speaker, so a pause takes that much longer to fall silent; that is
the trade-off, and the default is where both ends stop hurting.

**Check the host.** Decoding and resampling compete with everything else on the same
board; a stutter that starts with a Home Assistant backup or a CPU-heavy automation is
not a Bluetooth problem at all.

Every playback logs what the link is running, so a report does not have to be guessed
at:

```text
Streaming to aa:bb:cc:dd:ee:01 over pipewire using A2DP codec ldac, at 48000 Hz, buffer 500 ms
```

`at 44100 Hz (resampling from 48000 Hz)` would mean the speaker negotiated a rate the
player is not feeding, so something in between converts every sample - that case is
covered by `resample.quality` on the stream, but knowing it is happening is what makes
the difference diagnosable.

### Playing Audio from Home Assistant
- Each connected trusted speaker appears as a native `media_player` entity after the BL-HAOS integration is configured.
- Use standard Home Assistant Lovelace media cards, automation actions (`media_player.play_media`, `tts.speak`), or Music Assistant to send audio directly to your speaker.
- Volume adjustments in Home Assistant are written straight to the speaker's A2DP sink, which `bluez5.enable-hw-volume` maps onto the speaker's own AVRCP volume. The dashboard's slider reads the same value, so it follows Home Assistant instead of keeping whatever it last set itself; see [Volume](#volume).

### Saved Playlists and Queue Playback

- Start a saved custom playlist or provider shortcut from the authenticated **BL-HAOS Media** Library panel after selecting one connected speaker. HA resolves all saved references before one queue start; a provider shortcut stays one atomic item.
- The Ingress SpeakerCard and native Home Assistant media-player controls operate on the same per-speaker bridge queue. Native queue controls require the bridge to advertise queue version 1; older bridges keep their standard single-item playback behavior.
- Queue start, Next, and Previous are each sent once and are not automatically retried. After an uncertain response, check the selected speaker's current queue before another explicit action. Playback failure retains the current item; Play is an explicit retry and Next is an explicit skip.
- Queue state is held in memory and is cleared by Stop, replacement, or speaker loss. Queues do not resume after an add-on restart. Resolved playback URLs are transient between Home Assistant and the bridge; they never enter the browser or playlist library.
- Local SIL and mock verification do not establish live provider access, audible playback, or production acceptance. Deployment and release remain separately gated.

### Volume
- Home Assistant, the dashboard and the speaker share one volume level: `media_player.set_volume` and the dashboard slider both write the speaker's A2DP sink, and both read back the level the audio server reports for it. A change on either surface moves the other.
- The speaker owns that level too. AVRCP absolute volume moves the sink when you press the speaker's own buttons or use its own app, so the bridge reads it back: once when the speaker connects and every 30 seconds after that. A change made on the speaker reaches Home Assistant and the dashboard within one poll interval instead of staying invisible.
- While a poll is failing (a speaker that went away, a sink that is not published) the last known level is kept rather than replaced with a guess.
- Each speaker's volume in the dashboard settings is applied to the speaker when it connects and again when you save it, so the slider is a level to be set, not just a number in a file. The last level set from either surface survives a restart.
- A sink that is muted in the audio server still reports its level: the sliders show the level, and the add-on log names the mute at `debug`, so silence behind a non-zero slider is explicable rather than mysterious.


- Every connected trusted speaker is an independent `media_player` entity and can stream its own media at its own volume.
- To play the same source on several speakers, group those entities in Home Assistant (a media player group helper) or drive them from Music Assistant.
- Bluetooth A2DP is a point-to-point link, so this add-on does not provide sample-accurate synchronized multi-room playback. If you need phase-aligned rooms, run the community Snapcast add-on alongside Music Assistant instead.
- Speakers sharing one Bluetooth adapter also share that controller's radio bandwidth; several concurrent streams may exceed what a single adapter can carry reliably.

---

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Logging verbosity (`trace`, `debug`, `info`, `warning`, `error`). |
| `incident_monitor` | `false` | Record local incident observations continuously while the add-on runs; see [Persistent incident recording](#persistent-incident-recording). |
| `default_codec` | `auto` | Preferred Bluetooth A2DP audio codec. `auto` prioritizes highest fidelity supported by speaker: LDAC -> aptX HD -> aptX -> AAC -> SBC-XQ -> SBC. |
| `playback_buffer_ms` | `500` | Audio held ahead of the speaker, 50-2000 ms. Raise it if playback breaks up; more buffer also means audio keeps playing slightly longer after a pause. See [Audio stutters, flutters, or the quality changes](#audio-stutters-flutters-or-the-quality-changes). |
| `ldac_quality` | `sq` | LDAC encoding quality: `hq` 990 kbps, `sq` 660 kbps (steady), `mq` 330 kbps, `auto` adaptive (upstream's default, which changes the bitrate while a song plays). See the same section. |

### Collecting Logs

For an incident, temporarily set the add-on `log_level` to `debug`, restart the add-on, reproduce the issue once, and export its log with the Home Assistant Terminal & SSH add-on:

At `debug` (or `trace`), named backend functions emit `call enter` followed by
`call exit`, `call error`, or `call cancel`. Each line names the function, its
`span`, and its `parent` span; completion lines include `elapsed_ms`, and failures
include only `error_type`. Match the span identifiers within the same runtime
to follow nested calls through API handlers, BlueZ operations, reconnects,
audio-sink probes, playback, and background callbacks. A cancelled async task
is logged as cancellation, not as a successful return or an ordinary failure.
Long-running functions complete their span only when they return or stop.
These traces cover project functions, not every internal call inside third-party
libraries, and identifiers are not shared across the add-on and Home Assistant.
Tracing excludes arguments, return values, exception text, and tracebacks.
It adds no trace lines at `info` or higher. DEBUG tracing can be verbose and add
overhead, especially for audio-processing callbacks; enable it only while
collecting evidence.

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

The add-on log identifies BlueZ and PipeWire state; the Home Assistant log identifies native integration commands and entity state transitions. Integration 0.2.19 and later also traces named integration functions using the same call-span format, excluding the logger plumbing itself to prevent recursive logging. Remove the temporary debug logger and restore the add-on's `info` level after collecting evidence. Existing operational messages can still contain media URLs or device identifiers: redact access tokens, media URLs, and complete Bluetooth addresses before sharing logs.

Native playback command and execution logs omit resolved URLs. Playback errors
use controlled messages rather than raw provider or process exceptions. Child
stderr is still drained, but only an error/diagnostic category is logged, not
its contents; background failures report an exception type. Metadata logs omit
titles because fallback titles can derive from the URL path. Operation, speaker,
volume, duration, and codec/rate/buffer diagnostics remain available. The exact
validated URL is still passed internally as one decoder argument. This boundary
does not globally sanitize third-party or unrelated operational logging.

### Persistent incident recording

Enable `incident_monitor` in the add-on options and restart the add-on. The
recorder runs on the Home Assistant host independently of a browser, this chat,
or a Windows PC. It works at INFO level; DEBUG tracing is not required.

Every five seconds it aggregates backend WARNING/ERROR messages into fixed
categories (reconnect exhaustion, busy connection, unreachable link, sink,
playback, or other backend warning) and correlates them with canonical
component/speaker health and the native integration client count. These are
rule-based observations, not AI diagnoses or proof of root cause. Health is
sampled, not actively probed: a stale health observation stays stale, and brief
transitions between samples may only appear in warning counts.

Changed state and warning batches are recorded, with an hourly heartbeat and
startup/shutdown observations. No notifications are sent; no pairing, playback,
restart, or recovery action is performed. Raw log messages, tokens, URLs,
arguments, exception details and Bluetooth addresses are not stored. Speakers
use stable pseudonymous identifiers (not anonymous against a known address).

Daily JSONL files live under `/data/incidents/` in the add-on's persistent
storage. Keep the current UTC date and preceding 29 dates, pruning on writes.
Each day is capped at 2 MiB (at most approximately 60 MiB across 30 days).
Further observations that day are dropped, counted, and reported in the add-on
log. Thirty days is a retention maximum, not a promise to retain every event.
Storage errors are logged explicitly and retried at the next sample.

`GET /api/incidents`, through the authenticated Home Assistant Ingress session,
returns `enabled`, recorder `status`, and at most 100 recent observations from
the bounded tail of the latest daily file. Status includes running state,
storage error type, lifetime dropped-observation count, sample interval and
storage limits. It does not export the full archive.

The recorder stops when the add-on or host stops and resumes on add-on startup
when enabled. It cannot analyse Home Assistant Core or host Bluetooth journal
logs directly, or explain outages while it was stopped. Native client counts
can indicate an integration connection loss but do not identify its cause.
Use the existing Core/host logs alongside recorded incidents for deeper analysis.

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
