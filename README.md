# BL-HAOS
## Bluetooth Audio Adapter for Home Assistant OS

[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io)
[![Architecture](https://img.shields.io/badge/arch-aarch64%20%7C%20amd64%20%7C%20armv7-green.svg)](build.yaml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-39%20passed-emerald.svg)](tests/)

**BL-HAOS** is a Home Assistant OS Add-on that enables Home Assistant to stream media, TTS voice announcements (Piper, Cloud TTS), web radio, and synchronized multi-room music directly to Bluetooth speakers with low latency, high-fidelity codecs (LDAC, aptX HD, AAC, SBC-XQ), and rock-solid background auto-reconnection.

---

## Key Features

- **Native Home Assistant OS Add-on**: Packages PipeWire, WirePlumber, BlueZ, and Snapcast into a container supervised by S6-Overlay v3 with Ingress support.
- **Embedded Ingress Web Dashboard**: Visual discovery scanner with real-time RSSI signal strength meters, one-click PIN/SSP pairing, speaker control cards, and volume sliders directly inside the Home Assistant sidebar.
- **Native `media_player` Integration**: Trusted connected speakers are exposed through the separate [BL-HAOS HACS integration](https://github.com/Mahmoud-Eltabakh/BL-HAOS-Integration) over the private Supervisor add-on network.
- **Audiophile Codec Priority**: Automatically negotiates the highest quality codec supported by your speaker: `LDAC` &rarr; `aptX HD` &rarr; `aptX` &rarr; `AAC` &rarr; `SBC-XQ` &rarr; `SBC`.
- **Bidirectional AVRCP Volume Sync**: Physical volume buttons on the speaker update Home Assistant in real time, and Home Assistant automations adjust physical speaker volume.
- **Aggressive Auto-Reconnect Engine**: Background daemon actively monitors connection health and instantly reconnects speakers when they wake from sleep or re-enter range.
- **Multi-Room Synchronization (Snapcast)**: Group multiple Bluetooth speakers into synchronized whole-home audio zones with millisecond latency calibration.
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
repository-level [release gate contract](../../tests/integration/RELEASE-GATES.md)
and [regression matrix](../../tests/integration/REGRESSION-MATRIX.md). They
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
│  │                         │     │  • HA Bridge API  • Snapcast Sync      │  │
│  └─────────────────────────┘     └──────┬──────────────────┬──────────────┘  │
│                                         │                  │                 │
│  ┌──────────────────────────────────────▼──────┐  ┌────────▼──────────────┐  │
│  │        PipeWire & WirePlumber Audio         │  │ Snapcast Multi-Room   │  │
│  │   • SPA Bluetooth (LDAC, aptX, AAC, SBC-XQ) │◄─┤ • Sample-Accurate Sync│  │
│  │   • AVRCP Hardware Volume Sync              │  │ • Latency Calibration │  │
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
