import os
from pathlib import Path

def test_dockerfile_structure():
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.exists(), "Dockerfile must exist"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "ARG BUILD_FROM=" in content, "Dockerfile should accept ARG BUILD_FROM"
    assert "FROM ${BUILD_FROM}" in content, "Dockerfile should build from BUILD_FROM"

    # Required core packages
    expected_pkgs = [
        "bluez",
        "pipewire",
        "wireplumber",
        "libspa-0.2-bluetooth",
        "snapserver",
        "snapclient",
        "python3",
    ]
    for pkg in expected_pkgs:
        assert pkg in content, f"Dockerfile must install {pkg}"

    assert '"uvicorn[standard]"' in content, "Uvicorn must include WebSocket support"

def test_build_yaml_structure():
    build_path = Path("build.yaml")
    assert build_path.exists(), "build.yaml must exist"

    content = build_path.read_text(encoding="utf-8")
    assert "build_from:" in content
    assert "aarch64:" in content
    assert "amd64:" in content
    assert "armv7:" in content
