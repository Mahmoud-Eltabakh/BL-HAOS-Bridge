from pathlib import Path


def test_pipewire_s6_services():
    pw_type = Path("rootfs/etc/s6-overlay/s6-rc.d/10-pipewire/type")
    pw_run = Path("rootfs/etc/s6-overlay/s6-rc.d/10-pipewire/run")
    pw_dep = Path("rootfs/etc/s6-overlay/s6-rc.d/10-pipewire/dependencies.d/00-init-environment")
    pw_bundle = Path("rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/10-pipewire")

    assert pw_type.exists(), "10-pipewire/type must exist"
    assert pw_type.read_text(encoding="utf-8").strip() == "longrun"
    assert pw_run.exists(), "10-pipewire/run script must exist"
    assert pw_dep.exists(), "10-pipewire must depend on 00-init-environment"
    assert pw_bundle.exists(), "10-pipewire must be bundled into user/contents.d/"

    pw_run_content = pw_run.read_text(encoding="utf-8")
    assert "PIPEWIRE_RUNTIME_DIR=/var/run/pipewire" in pw_run_content
    assert 'mkdir -p "$PIPEWIRE_RUNTIME_DIR"' in pw_run_content
    assert "exec pipewire" in pw_run_content

    wp_type = Path("rootfs/etc/s6-overlay/s6-rc.d/20-wireplumber/type")
    wp_run = Path("rootfs/etc/s6-overlay/s6-rc.d/20-wireplumber/run")
    wp_dep = Path("rootfs/etc/s6-overlay/s6-rc.d/20-wireplumber/dependencies.d/10-pipewire")
    wp_bundle = Path("rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/20-wireplumber")

    assert wp_type.exists(), "20-wireplumber/type must exist"
    assert wp_type.read_text(encoding="utf-8").strip() == "longrun"
    assert wp_run.exists(), "20-wireplumber/run script must exist"
    assert wp_dep.exists(), "20-wireplumber must depend on 10-pipewire"
    assert wp_bundle.exists(), "20-wireplumber must be bundled into user/contents.d/"

    wp_run_content = wp_run.read_text(encoding="utf-8")
    assert "PIPEWIRE_RUNTIME_DIR=/var/run/pipewire" in wp_run_content
    assert 'mkdir -p "$PIPEWIRE_RUNTIME_DIR"' in wp_run_content
    assert "exec wireplumber" in wp_run_content
