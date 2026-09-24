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
        "pipewire-bin",
        "wireplumber",
        "libspa-0.2-bluetooth",
        "snapserver",
        "snapclient",
        "python3",
        "pulseaudio-utils",
    ]
    for pkg in expected_pkgs:
        assert pkg in content, f"Dockerfile must install {pkg}"

    requirements = Path("backend/requirements.txt").read_text(encoding="utf-8")
    assert "uvicorn==" in requirements, "Uvicorn must be pinned in backend requirements"
    assert "wsproto==" in requirements, "Uvicorn must include a pinned pure-Python WebSocket implementation"
    assert '"uvicorn[standard]"' not in content, "Standard extras require native armv7 builds"
    assert "apt-get install -y --no-install-recommends" in content
    assert "python3-pip" in content, "Dockerfile must install pip before backend requirements"
    assert "--requirement /backend/requirements.txt" in content
    requirement_lines = requirements.splitlines()
    assert requirement_lines and all("==" in line for line in requirement_lines if line.strip())


def test_dockerfile_builds_frontend_from_source():
    """The web UI must be built inside the image; dist/ is never committed."""
    content = Path("Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:" in content, "Dockerfile must have a Node build stage for the web UI"
    assert "npm ci" in content, "Web build stage must install from the lockfile"
    assert "npm run build" in content, "Web build stage must run the production build"
    assert "COPY --from=web_ui_build" in content, "Runtime stage must copy the built UI from the build stage"
    assert "COPY web_ui/dist" not in content, "Do not copy committed dist; it is no longer in git"

def test_build_yaml_structure():
    build_path = Path("build.yaml")
    assert build_path.exists(), "build.yaml must exist"

    content = build_path.read_text(encoding="utf-8")
    assert "build_from:" in content
    assert "aarch64:" in content
    assert "amd64:" in content
    assert "armv7:" in content
