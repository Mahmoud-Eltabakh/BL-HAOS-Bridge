# Changelog

## 0.2.50

- Simplify the Ingress dashboard by removing the Diagnostics and Guided recovery panels and their operator-only backend routes. Native Home Assistant integration status remains available.

## 0.2.49

- Report a playback timeline so Home Assistant can draw a progress bar. The bridge now tracks elapsed playing time per speaker (pausing the clock while paused, clearing it on stop or when the stream ends) and probes the media duration in the background with `ffprobe`, so playback is never delayed while the length is resolved. The position/duration envelope is published in the native `playback` payload and mapped to `media_position`, `media_position_updated_at` and `media_duration`.

## 0.2.48

- Playback commands no longer wait for a previous stream's decoder to disappear. On this hardware `ffmpeg` can survive even `SIGKILL` while blocked in uninterruptible I/O, so `play_media`/`stop` were spending the whole teardown window (about 3s) before answering. The player is still given a short window to release the A2DP sink, then any leftover child is escalated and collected in the background, which keeps repeated play/pause/stop snappy.

## 0.2.47

- Fix "the first play does nothing, the second attempt works": stopping a previous stream could block forever. `_stop_processes` awaited `process.wait()` without a bound after `SIGKILL`, so a child stuck on a wedged A2DP sink made the *next* `play_media` never spawn its decoder/player. Every teardown wait is now bounded, and the caller always proceeds.
- Stop/pause now react immediately: a `SIGSTOP`ped (paused) child never receives a queued `SIGTERM`, so every child is resumed with `SIGCONT` before being signalled. Previously each stopped process cost a full 5 second grace window.
- Commands for one speaker are now serialized, so overlapping play/stop requests can no longer race on the same process pair.
- `paplay` now runs with `--latency-msec=250`. The default buffer kept several seconds of audio queued downstream, which is why pause in particular appeared to be ignored for a while.

## 0.2.46

- Fix Bluetooth discovery returning nothing: the bridge registered a D-Bus message handler but never a match rule, so the bus daemon never delivered BlueZ's `InterfacesAdded`/`InterfacesRemoved`/`PropertiesChanged` signals. Discovered devices were silently dropped, `GET /api/devices` stayed empty and the scan list never updated in the UI. The bridge now registers explicit match rules for BlueZ's ObjectManager and PropertiesChanged signals.

## 0.2.45

- Fix "Add Speaker" dialog crashing the whole web UI: the auto-scan `useRef`/`useEffect` were declared below the `if (!isOpen) return null` early return, so the first open threw "Rendered more hooks than during the previous render" and nothing could be paired from the GUI.
- Surface the bounded, redacted BlueZ reason for pairing and connection failures instead of a generic message, so the dialog can explain why a speaker refused to connect.
- Give device-row actions distinct accessible names (`Connect to <device>`, `Disconnect <device>`, `Remove <device>`) so they are unambiguous for assistive tech and tests.

## 0.2.44

- Fail fast when PipeWire or PulseAudio sink discovery stalls, preventing Home Assistant media commands from timing out while waiting for the speaker.

## 0.2.43

- Fix PulseAudio paplay raw PCM invocation: omit '-' filename argument which caused paplay/pacat to fail with 'open(): No such file or directory' instead of streaming from stdin.
- Surface playback process failure exit codes at warning level for easier operational troubleshooting.

## 0.2.42

- Accept Home Assistant content types (such as "music", "video", "channel", etc.) in media type validation to prevent 422 errors during playback.

## 0.2.41

- Enable authenticated Ingress recovery and live volume control while preserving the private native transport boundary.
- Persist per-speaker multi-room latency offsets and prevent playback subprocesses from stalling on undrained diagnostics output.

## 0.2.40

- Keep add-on and discovered Home Assistant integration log levels synchronized through Supervisor discovery.
- Report Bluetooth pairing and connection collisions honestly instead of publishing false connected state.

## 0.2.39

- Fix Bluetooth connection and pairing collision by disarming active discovery scan and preventing redundant profile connection races on single-antenna Broadcom radios.

## 0.2.38

- Fix auto-reconnect and sink-wait retry loop in REST/native command handler to recognize generic Bluetooth audio sink exceptions.
- Add host PulseAudio BlueZ transport disarm hook on container startup and fallback sink resolution.

## 0.2.37

- Enable Supervisor API access so native integration discovery is refreshed automatically after app reinstalls and container address changes.

## 0.2.36

- Refresh disconnected cached BlueZ device proxies before re-pairing, preventing Connect failures after the device object was removed and recreated.

## 0.2.35

- Handle both compact and spaced BlueZ `InProgress` responses during A2DP reconnects.

## 0.2.34

- Add a bounded settle-and-retry loop when replacing active playback, avoiding transient BlueZ reconnect failures after resetting a stale A2DP transport.

## 0.2.33

- Force a clean Bluetooth disconnect before reconnecting an already-connected speaker, clearing stale PipeWire A2DP transports.

## 0.2.32

- Explicitly reconnect the A2DP profile after pairing so a removed and re-paired speaker is immediately usable.

## 0.2.31

- Grant the add-on Home Assistant's internal audio access so PipeWire can create Bluetooth A2DP sinks after BlueZ reconnects.

## 0.2.30

- Include the PipeWire command-line tools required to discover Bluetooth sinks and apply volume changes inside the add-on container.

## 0.2.29

- Preserve actionable playback failure details in native API responses, including when `media_player.play` is requested without a remembered media URL.

## 0.2.28

- Explicitly activate the A2DP sink profile when BlueZ reports that the Bluetooth device is already connected, preventing playback failures where the link is restored but PipeWire never receives an audio sink.

## 0.2.27

- Wait up to 15 seconds for PipeWire to publish a reconnected speaker's A2DP sink before failing playback, allowing WirePlumber profile negotiation to complete on slower Home Assistant OS hardware.

## 0.2.26

- Harden Bluetooth device lookup and auto-reconnect against malformed or unpopulated cached device addresses, preventing `ValueError: Invalid Bluetooth address` exceptions when iterating over discovered devices.
- Configure WirePlumber to explicitly use `[ a2dp_sink a2dp_source ]` roles and disable the unused HFP/HSP telephony backend (`bluez5.hfphsp-backend = "none"`). Prevents BlueZ `RegisterProfile() failed: org.bluez.Error.NotPermitted` and SCO socket initialization errors on Home Assistant OS that blocked A2DP audio sink registration.
- Strengthen device reconnection to explicitly connect the A2DP audio profile (`A2DP_SINK_UUID`) if base ACL connection is already established or returns `AlreadyConnected`/`InProgress`.
- Make PipeWire sink discovery inspect both top-level and nested node property dictionaries in `pw-dump` output.
- Auto-reconnect failures during playback now report which stage failed instead of a generic "Auto-reconnect failed" message: whether Bluetooth itself couldn't reconnect, or it reconnected but no PipeWire audio sink appeared afterward.
- Increased the post-reconnect settle time before retrying playback (2.0s -> 3.0s) to give PipeWire more time to negotiate the A2DP audio profile after a fresh Bluetooth reconnect.
- Separate Web UI diagnostics and recovery fetching into independent handlers so operator and native diagnostics load cleanly even when unauthenticated to recovery endpoints.

## 0.2.25

- Distinguish auto-reconnect error stages during playback to clearly identify whether BlueZ connection or PipeWire audio sink discovery failed.

## 0.2.24

- The add-on now pushes its native bridge token to Supervisor's Discovery API on startup, so the Home Assistant integration connects automatically with zero manual token entry when discovery succeeds. Manual endpoint/token entry remains available as a fallback.

## 0.2.23

- Prevent connected Bluetooth audio adapters from auto-sleeping (and dropping the link) by sending a 1-second silent pulse every 4 minutes while idle. Skipped whenever real audio is already playing, so battery use stays minimal.

## 0.2.22

- Tune the auto-reconnect engine for faster recovery from real Bluetooth link drops: lower initial backoff (2.0s -> 1.0s), gentler backoff growth (2.0x -> 1.5x), lower backoff cap (60s -> 20s), more retries before the circuit breaker trips (5 -> 8), and a shorter breaker cooldown (30s -> 10s).

## 0.2.21

- Fix `media_player.media_play` failing with "No active playback to resume" after a stop or add-on restart by replaying the last played URL instead of requiring an already-running stream.

## 0.2.20

- Enhance Bluetooth audio sink recognition to detect connected and paired audio devices (such as Bluetooth adapters and speakers) and publish native speaker state updates immediately.

## 0.2.19

- Strip `libwireplumber-module-logind` directly from `/usr/share/wireplumber/wireplumber.conf` during Docker build and initialize systemd seats runtime directory to permanently resolve the container logind crash loop.

## 0.2.18

- Add PipeWire socket readiness wait loop to WirePlumber service and clean stale lock files on PipeWire launch.

## 0.2.17

- Explicitly pass `-c /etc/wireplumber/wireplumber.conf` to WirePlumber to ensure custom container profile is loaded.

## 0.2.16

- Correct WirePlumber 0.4 `wireplumber.conf` schema placing `libwireplumber-module-lua-scripting` in `wireplumber.components` as a module component.

## 0.2.15

- Include `libwireplumber-module-lua-scripting` in container `wireplumber.conf` to properly load Lua components for Bluetooth audio.

## 0.2.14

- Provide dedicated container `wireplumber.conf` tailored for Bluetooth audio and stream policy, permanently bypassing desktop seat and logind monitors.

## 0.2.13

- Remove systemd-logind monitor scripts at container build time and map DBUS_SESSION_BUS_ADDRESS to system bus socket to prevent WirePlumber crash loop in container.

## 0.2.12

- Mask `20-logind.lua` in WirePlumber to permanently prevent systemd-logind crash and disconnection loop in container environment.

## 0.2.11

- Unset DBUS_SESSION_BUS_ADDRESS to prevent WirePlumber from triggering desktop session bus portal and logind lookups in headless container.

## 0.2.10

- Use stock distribution PipeWire configuration to prevent session manager proxy activation aborts.

## 0.2.9

- Enable detailed PipeWire and WirePlumber debug diagnostics for container audio server inspection.

## 0.2.8

- Fix WirePlumber 0.4 startup crash by removing incompatible WirePlumber 0.5 `.conf` file and standardizing on `bluetooth.lua.d/50-bluez.lua`.

## 0.2.7

- Enhance volume setting reliability and PipeWire Bluetooth sink resolution; persist volume state immediately and apply hardware volume asynchronously.

## 0.2.6

- Gracefully handle in-progress discovery scans without raising unhandled HTTP 500 exceptions.

## 0.2.5

- Add automatic BlueZ adapter ConnectDevice fallback for unbonded / temporary device handles during pairing and reconnection.

## 0.2.4

- Fix WirePlumber D-Bus session bus mapping and disable container logind monitors to achieve rock-solid PipeWire daemon stability.

## 0.2.3

- Fix WirePlumber crashing and disconnecting in container environment by preserving default PipeWire modules and disabling X11 session D-Bus autolaunch.
- Add WirePlumber 0.4.x Lua BlueZ configuration for reliable A2DP sink monitoring.
- Add adapter-level Direct D-Bus connection helper (`ConnectDevice`) for devices not yet present in BlueZ device cache.

## 0.2.2

- Remove the user-managed bridge token and host port mapping; the HACS integration now discovers BL-HAOS through Supervisor on the private add-on network.

## 0.2.1

- Ensure S6 restarts recreate the PipeWire runtime directory before PipeWire and WirePlumber launch, preventing the audio-service restart loop on HAOS.

## 0.2.0

- Remove the legacy broker-based runtime support, Supervisor service access, compatibility options, diagnostics, and dependencies.
- Publish architecture-specific public GHCR add-on images through GitHub Actions at `ghcr.io/mahmoud-eltabakh/{arch}-bl-haos`.
- Move the Home Assistant custom integration into the standalone `BL-HAOS-Integration` HACS repository.
- Require a user-configured `bridge_token` for the authenticated native REST/WebSocket bridge; the add-on no longer writes into Home Assistant configuration.

## 0.1.8

- Retire legacy media-player discovery; native REST/WebSocket integration is now the supported entity path.
- Add sanitized native bridge diagnostics to Ingress and Home Assistant diagnostics exports.
- Replace the embedded Home Assistant helper credential with environment-only `HA_URL` and `HA_TOKEN` configuration; rotate the prior credential before use.

## 0.1.7

- Historical: bundle a native `bl_haos` custom integration that creates Home Assistant `media_player` entities from Bluetooth speaker state.
- Historical: publish retained speaker metadata and install the custom integration through the writable Home Assistant configuration mount.
## 0.1.5

- Install Uvicorn's standard extras so the Ingress dashboard WebSocket endpoint can upgrade successfully.

## 0.1.4

- Remove the duplicate legacy service launcher that repeatedly started competing PipeWire, Snapserver, and Uvicorn processes.

## 0.1.3

- Replay Bluetooth speaker discovery after the legacy client confirms its broker connection.
- Add legacy transport configuration and connection status to the Ingress health endpoint.
- Load Supervisor service credentials in both S6 daemon paths.

## 0.1.2

- Remove the unavailable GHCR image reference so Home Assistant Supervisor builds the add-on from repository source.

## 0.1.1

- Publish retained discovery, availability, state, and volume messages using Supervisor-provided service credentials.
- Register trusted Bluetooth audio sinks during startup so auto-reconnect is armed before the first disconnect.
- Document the required broker and integration setup for `media_player` discovery.

## 0.1.0 - Initial Release (Home Assistant OS Add-on)

- **Official Home Assistant Add-on Packaging**:
  - Supervisor S6-Overlay v3 service hierarchy with multi-arch Debian 12 Bookworm base (`aarch64`, `amd64`, `armv7`).
  - Home Assistant Ingress web dashboard integration on port `8099`.
  - Host D-Bus (`/var/run/dbus/system_bus_socket`), `full_access`, and `udev` device passthrough.
- **PipeWire & WirePlumber Audio Engine**:
  - Low-latency buffer tuning (`default.clock.quantum = 1024` ~21.3ms).
  - Audiophile codec negotiation priority: LDAC -> aptX HD -> aptX -> AAC -> SBC-XQ -> SBC.
  - Bidirectional AVRCP hardware and software volume synchronization.
- **BlueZ D-Bus Bluetooth Controller**:
  - Non-blocking `dbus-fast` client with live RSSI signal telemetry.
  - Interactive pairing agent supporting PIN authentication and SSP numeric confirmation.
  - Multi-adapter support for onboard and USB Bluetooth dongles.
- **Aggressive Auto-Reconnect Engine**:
  - Background state machine with exponential backoff and discovery fast-tracking for sleeping speakers.
  - Adapter concurrency locks and 5-failure circuit breaker.
- **Home Assistant Media Player Bridge**:
  - Automatic discovery registering native `media_player` entities for TTS, local media, and web radio.
- **Snapcast Multi-Room Sync**:
  - Integrated Snapserver and dynamic Snapclient pipelines with acoustic latency offset calibration.
