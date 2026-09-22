# Changelog

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
