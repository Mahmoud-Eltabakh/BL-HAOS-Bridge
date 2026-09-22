ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
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
        pipewire-audio-client-libraries \
        pipewire-pulse \
        wireplumber \
        libspa-0.2-bluetooth \
        snapserver \
        snapclient \
        python3 \
        python3-pip \
        python3-venv \
        python3-dbus-fast \
        ffmpeg \
        libfreeaptx0 \
        libldacbt-enc2 \
        curl \
        jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/share/wireplumber/scripts/monitors/*logind* \
    && rm -rf /usr/share/wireplumber/main.lua.d/*logind*

# Copy root filesystem overlay and application code
COPY rootfs /
COPY backend /backend
COPY web_ui/dist /var/www/bl-haos

# Install python dependencies for backend
RUN pip3 install --no-cache-dir --prefer-binary --break-system-packages \
    fastapi \
    uvicorn \
    wsproto \
    pydantic \
    httpx

# Normalize line endings and set executable permissions on rootfs scripts
RUN find /etc/ /usr/bin/ /backend/ -type f -exec sed -i 's/\r$//' {} + 2>/dev/null || true \
    && chmod -R 755 /etc/s6-overlay/s6-rc.d/ 2>/dev/null || true \
    && chmod -R 755 /etc/services.d/ 2>/dev/null || true \
    && chmod +x /usr/bin/bl-haos-probe

ENV PYTHONPATH="/backend"

WORKDIR /
