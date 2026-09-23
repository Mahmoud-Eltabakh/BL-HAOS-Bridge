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

    requirements = Path("backend/requirements.txt").read_text(encoding="utf-8")
    assert "uvicorn==" in requirements, "Uvicorn must be pinned in backend requirements"
    assert "wsproto==" in requirements, "Uvicorn must include a pinned pure-Python WebSocket implementation"
    assert '"uvicorn[standard]"' not in content, "Standard extras require native armv7 builds"
    assert "bluez=" in content, "APT runtime dependencies must be version-pinned"
    assert "--requirement /backend/requirements.txt" in content
    requirement_lines = requirements.splitlines()
    assert requirement_lines and all("==" in line for line in requirement_lines if line.strip())

def test_build_yaml_structure():
    build_path = Path("build.yaml")
    assert build_path.exists(), "build.yaml must exist"

    content = build_path.read_text(encoding="utf-8")
    assert "build_from:" in content
    assert "aarch64:" in content
    assert "amd64:" in content
    assert "armv7:" in content
