from pathlib import Path


def test_pipewire_s6_services():
    init_up = Path("rootfs/etc/s6-overlay/s6-rc.d/00-init-environment/up")
    init_content = init_up.read_text(encoding="utf-8")
    assert "/run/audio/pulse.sock" in init_content
    assert "pactl list modules short" in init_content
    assert "module-bluez5|module-bluetooth" in init_content
    assert "pactl unload-module" in init_content

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


def test_wireplumber_writes_the_ldac_quality_choice():
    """LDAC quality is an add-on option, so it has to reach WirePlumber's config.

    WirePlumber reads the bluez5 properties from lua fragments at start, so the
    choice is written into a fragment that sorts after 50-bluez.lua (60-...) and
    therefore wins. An unusable value falls back to the shipped default rather
    than being handed to WirePlumber.
    """
    from backend.bl_haos.constants import DEFAULT_LDAC_QUALITY, LDAC_QUALITY_MODES

    wp_run = Path("rootfs/etc/s6-overlay/s6-rc.d/20-wireplumber/run")
    content = wp_run.read_text(encoding="utf-8")

    # bashio is how an add-on option reaches a container script.
    assert content.startswith("#!/command/with-contenv bashio")
    assert "bashio::config 'ldac_quality'" in content
    assert "60-bl-haos-options.lua" in content
    assert 'bluez5.a2dp.ldac.quality' in content
    # Every accepted mode is one WirePlumber documents, and the fallback is the
    # same value the add-on ships, so the two cannot drift apart.
    for mode in LDAC_QUALITY_MODES:
        assert mode in content
    assert f'ldac_quality="{DEFAULT_LDAC_QUALITY}"' in content
