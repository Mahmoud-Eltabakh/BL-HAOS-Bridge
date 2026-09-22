from pathlib import Path

def test_s6_init_service_exists():
    init_type = Path("rootfs/etc/s6-overlay/s6-rc.d/00-init-environment/type")
    init_up = Path("rootfs/etc/s6-overlay/s6-rc.d/00-init-environment/up")
    bundle_link = Path("rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/00-init-environment")

    assert init_type.exists(), "00-init-environment/type must exist"
    assert init_type.read_text(encoding="utf-8").strip() == "oneshot"

    assert init_up.exists(), "00-init-environment/up script must exist"
    up_content = init_up.read_text(encoding="utf-8")
    assert "/var/run/pipewire" in up_content
    assert "/var/run/user/0" in up_content

    assert bundle_link.exists(), "00-init-environment must be bundled in user/contents.d/"
