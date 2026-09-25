ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm

# Stage 1: build the Ingress web UI from source (dist/ is not committed).
FROM node:22-bookworm-slim AS web_ui_build
WORKDIR /build
COPY web_ui/package.json web_ui/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web_ui/ ./
RUN npm run build

# Stage 2: assemble the add-on runtime.
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install base dependencies and audio/bluetooth packages
# Note: libfdk-aac2 is in debian non-free; libspa-0.2-bluetooth and libldacbt-enc2 provide standard high-res Bluetooth audio
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bluez \
        bluez-tools \
        dbus \
        pipewire \
        pipewire-bin \
        pipewire-audio-client-libraries \
        pipewire-pulse \
        wireplumber \
        libspa-0.2-bluetooth \
        python3 \
        python3-pip \
        python3-venv \
        python3-dbus-fast \
        ffmpeg \
        pulseaudio-utils \
        libfreeaptx0 \
        libldacbt-enc2 \
        curl \
        jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i '/libwireplumber-module-logind/d' /usr/share/wireplumber/wireplumber.conf 2>/dev/null || true \
    && rm -f /usr/lib/*/wireplumber-0.4/libwireplumber-module-logind.so 2>/dev/null || true

# Copy root filesystem overlay and application code; the web UI comes from the
# build stage so the image always matches the committed sources.
COPY rootfs /
COPY backend /backend
COPY --from=web_ui_build /build/dist /var/www/bl-haos

# Install python dependencies for backend
RUN pip3 install --no-cache-dir --prefer-binary --break-system-packages \
    --requirement /backend/requirements.txt

# Normalize line endings and set executable permissions on rootfs scripts
RUN find /etc/ /usr/bin/ /backend/ -type f -exec sed -i 's/\r$//' {} + 2>/dev/null || true \
    && chmod -R 755 /etc/s6-overlay/s6-rc.d/ 2>/dev/null || true \
    && chmod -R 755 /etc/services.d/ 2>/dev/null || true \
    && chmod +x /usr/bin/bl-haos-probe

ENV PYTHONPATH="/backend"

WORKDIR /
