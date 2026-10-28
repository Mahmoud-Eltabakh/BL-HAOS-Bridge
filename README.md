# BL-HAOS
## Bluetooth Audio Adapter for Home Assistant OS

### Phase 24 Media Catalog

Integrated release 0.2.69 is undeployed, including the pre-existing reconnect
fix. Local Core 2026.10.3 checks do not establish actual provider layout,
session expiry or physical playback. Deployment and any further restart need
separate authorization.

After authorized installation, **Open Media Catalog** beside Home Assistant
Native integration opens HA's `/bl-haos-media` panel; it is navigation-only.
Alternatively use **Native Browse Media** on a native speaker. In **Play on**,
select exactly one connected BL-HAOS speaker, browse audio/provider playlists
and press **Play**. The opaque reference reaches native `media_player.play_media`
unchanged; HA owns provider authentication and reference resolution. The bridge
receives only the resolved playable request for decoding. A provider playlist
stays one reference, not a local queue; submission does not prove audible playback.

The panel does not store browser/add-on HA/provider credentials, use direct
provider APIs, or accept raw URLs. Existing native direct-URL support is not a
catalog fallback. The local Phase 25 candidate uses the authenticated panel and
Home Assistant-owned storage for custom playlists of ordered Home Assistant
media references and one-reference provider-playlist shortcuts; provider tracks
are not copied. Explicit availability checks browse the exact HA media reference
only; they do not resolve or play media and never rewrite saved IDs. This is
local/mock behavior, not production acceptance. Phase 24 production requirements
remain pending, T-24-15 remains OPEN/HIGH, and T-24-16 remains OPEN/MEDIUM.
Phase 26 owns launch, playback, and queue controls. Release and deployment are not
approved. Root TESTING.md separates local SIL from authorized HAOS acceptance.
Retired Docker E2E and fixture screenshots are not host proof.

[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io)
[![Architecture](https://img.shields.io/badge/arch-aarch64%20%7C%20amd64%20%7C%20armv7-green.svg)](build.yaml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-144%20passed-emerald.svg)](tests/)

**BL-HAOS** is a Home Assistant OS Add-on that enables Home Assistant to stream media, TTS voice announcements (Piper, Cloud TTS), web radio, and music directly to Bluetooth speakers with low latency, high-fidelity codecs (LDAC, aptX HD, AAC, SBC-XQ), and rock-solid background auto-reconnection.

---

## Key Features

- **Native Home Assistant OS Add-on**: Packages PipeWire, WirePlumber, and BlueZ into a container supervised by S6-Overlay v3 with Ingress support.
- **Embedded Ingress Web Dashboard**: Visual discovery scanner with real-time RSSI signal strength meters, one-click PIN/SSP pairing, speaker control cards, and volume sliders directly inside the Home Assistant sidebar.
- **Native `media_player` Integration**: Trusted connected speakers are exposed through the separate [BL-HAOS HACS integration](https://github.com/Mahmoud-Eltabakh/BL-HAOS-Integration) over the private Supervisor add-on network.
- **Audiophile Codec Priority**: Automatically negotiates the highest quality codec supported by your speaker: `LDAC` &rarr; `aptX HD` &rarr; `aptX` &rarr; `AAC` &rarr; `SBC-XQ` &rarr; `SBC`.
- **Volume control that stays in sync**: `media_player.set_volume` writes straight to the speaker's A2DP sink, which `bluez5.enable-hw-volume` maps onto the speaker's own AVRCP volume. The bridge also reads the sink's real level back, so a change made with the speaker's own buttons reaches Home Assistant and the dashboard - see [Volume](DOCS.md#volume).
- **Stutter-free playback**: the player clients are given an explicit buffer (500 ms by default, `BLHAOS_PLAYBACK_BUFFER_MS` to change it) instead of the small client default, because a Bluetooth link delivers in bursts and the speaker drains its own buffer at a fixed rate.
- **A codec choice that does not drift**: LDAC is pinned to a steady bitrate by default (`sq`, 660 kbps) because its own `auto` mode adapts the bitrate while a song plays - heard as the quality changing mid-song. `hq`/`mq`/`auto` are one add-on option away, and the codec itself is pinnable per speaker in the dashboard.
- **Conservative Scheduled Reconnection**: One bounded connection attempt at a time per adapter, with exponential retry delays and a five-minute cooldown after eight failures. Busy responses and presence advertisements preserve pacing; automatic attempts never reset a live link or remove pairing. Hardware/HAOS reconnect soak is pending authorized deployment; local tests do not prove the live fault is fixed.
- **Multi-Adapter Support**: Run multiple Bluetooth USB dongles alongside onboard Bluetooth controllers with independent speaker assignments.

---

## Installation in Home Assistant OS

1. Open your Home Assistant instance and navigate to **Settings** &rarr; **Add-ons** &rarr; **Add-on Store**.
2. Click the three dots in the top-right corner and select **Repositories**.
3. Add repository URL:
   ```text
   https://github.com/Mahmoud-Eltabakh/BL-HAOS-Bridge
   ```
4. Find **BL-HAOS Bluetooth Audio Adapter** in the Add-on Store, click **Install**, then click **Start**.
5. Enable **Show in sidebar** to access the dashboard directly from your Home Assistant menu.

See [DOCS.md](DOCS.md) for complete configuration options and troubleshooting guidance.

## Release and Dependency Security Policy

Stable releases use the exact Debian package versions in `Dockerfile`, the pinned
Python versions in `backend/requirements.txt`, and the npm lockfile. Preview builds
use the same pins and may be promoted only after the same high/critical audit gate
passes. GitHub Actions runs `pip-audit`, `npm audit --audit-level=high`, and a Trivy
filesystem scan; any high or critical finding fails the build. Vulnerability
exceptions must be documented with an owner and expiry date in the pull request and
must not be implemented as a default audit bypass.

The complete release checklist and scenario matrix are maintained in the
repository-level [release gate contract](../../.planning/RELEASE-GATES.md)
and [regression matrix](../../.planning/REGRESSION-MATRIX.md). They
define stable and preview support, HAOS and native integration compatibility,
architecture coverage, redacted evidence, and environment-qualified hardware
validation.

## GHCR Publishing

The add-on manifest pulls architecture-specific images from:

```text
ghcr.io/mahmoud-eltabakh/{arch}-bl-haos-bridge
```

After the first GitHub Actions build, set each package visibility to **Public** in GitHub Packages. Home Assistant OS cannot pull a private GHCR package anonymously and reports `403 denied` or `401` during installation.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        Home Assistant Core & Frontend                        │
│   • Lovelace Cards  • Automations / Scripts  • Voice TTS (Piper/Cloud)       │
└──────────────────────────┬────────────────────────────┬──────────────────────┘
                           │ Ingress Web UI (HTTP/WS)   │ Native Home Assistant bridge
┌──────────────────────────▼────────────────────────────▼──────────────────────┐
│                     BL-HAOS Add-on Container Layer (HAOS)                    │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────┐     ┌────────────────────────────────────────┐  │
│  │   Ingress Web Dashboard │     │         FastAPI Backend Daemon         │  │
│  │  (Scan / Pair / Volume) │◄───►│  • BlueZ Manager  • Auto-Reconnect     │  │
│  │                         │     │  • HA Bridge API  • Speaker Registry   │  │
│  └─────────────────────────┘     └──────┬──────────────────┬──────────────┘  │
│                                         │                  │                 │
│  ┌──────────────────────────────────────▼──────┐  ┌────────▼──────────────┐  │
│  │        PipeWire & WirePlumber Audio         │  │ Independent Streams   │  │
│  │   • SPA Bluetooth (LDAC, aptX, AAC, SBC-XQ) │◄─┤ • Per-Speaker Sink    │  │
│  │   • AVRCP Hardware Volume (write)           │  │ • Bluetooth A2DP Link │  │
│  └───────────────────┬─────────────────────────┘  └───────────────────────┘  │
│                      │ D-Bus / Audio PCM                                     │
├──────────────────────┴───────────────────────────────────────────────────────┤
│                             Host System BlueZ (HAOS)                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Testing & Verification

Run the test suite across all 8 modules:

```bash
pytest tests/ -v
```

The test suite covers Home Assistant Add-on schema compliance, S6 service supervision, PipeWire audio configuration, BlueZ D-Bus controllers, native transport, and Ingress assets.

---

## Local Multi-Arch Builds (faster than GitHub Actions)

One-time setup per clone:

```bash
docker login ghcr.io          # PAT with write:packages scope
git config core.hooksPath .githooks
docker buildx create --name bl-haos-builder --driver docker-container --use
docker run --privileged --rm tonistiigi/binfmt --install all
```

After setup, images build and push automatically:

- **On commit**: `.githooks/post-commit` rebuilds only when Dockerfile, `build.yaml`, `config.yaml`, `backend/`, `rootfs/`, or `web_ui/dist/` changed in that commit.
- **On any file change**: run `pwsh scripts/watch-and-build.ps1` for a continuous watch loop (debounced).
- **Manually**: run `pwsh scripts/build-and-push.ps1`.

All three commands build `aarch64`, `amd64`, and `armv7` and push `{arch}-bl-haos-bridge:{version}` and `:latest` to GHCR using the version in `config.yaml`.
