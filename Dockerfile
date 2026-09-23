ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install base dependencies and audio/bluetooth packages
# Note: libfdk-aac2 is in debian non-free; libspa-0.2-bluetooth and libldacbt-enc2 provide standard high-res Bluetooth audio
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bluez=5.66-1+deb12u2 \
        bluez-tools=0.2.0~20170911.gitag16f8e-4 \
        dbus=1.14.10-1~deb12u1 \
        pipewire=0.3.65-3+deb12u1 \
        pipewire-audio-client-libraries=0.3.65-3+deb12u1 \
        pipewire-pulse=0.3.65-3+deb12u1 \
        wireplumber=0.4.14-1 \
        libspa-0.2-bluetooth=0.3.65-3+deb12u1 \
        snapserver=0.27.0-1 \
        snapclient=0.27.0-1 \
        python3=3.11.2-1+b1 \
        python3-pip=23.0.1+dfsg-1 \
        python3-venv=3.11.2-1+b1 \
        python3-dbus-fast=2.21.1-1 \
        ffmpeg=7:5.1.6-0+deb12u1 \
        libfreeaptx0=0.1.1-1 \
        libldacbt-enc2=2.0.2.3+git20200429-1 \
        curl=7.88.1-10+deb12u8 \
        jq=1.6-2.1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i '/libwireplumber-module-logind/d' /usr/share/wireplumber/wireplumber.conf 2>/dev/null || true \
    && rm -f /usr/lib/*/wireplumber-0.4/libwireplumber-module-logind.so 2>/dev/null || true

# Copy root filesystem overlay and application code
COPY rootfs /
COPY backend /backend
COPY web_ui/dist /var/www/bl-haos

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
