from pathlib import Path


def test_backend_s6_service():
    srv_type = Path("rootfs/etc/s6-overlay/s6-rc.d/40-bl-haos-daemon/type")
    srv_run = Path("rootfs/etc/s6-overlay/s6-rc.d/40-bl-haos-daemon/run")
    srv_dep = Path("rootfs/etc/s6-overlay/s6-rc.d/40-bl-haos-daemon/dependencies.d/00-init-environment")
    srv_bundle = Path("rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/40-bl-haos-daemon")

    assert srv_type.exists(), "40-bl-haos-daemon/type must exist"
    assert srv_type.read_text(encoding="utf-8").strip() == "longrun"
    assert srv_run.exists(), "40-bl-haos-daemon/run must exist"
    assert srv_dep.exists(), "40-bl-haos-daemon must depend on 00-init-environment"
    assert srv_bundle.exists(), "40-bl-haos-daemon must be bundled in user/contents.d/"

    service_path = Path("rootfs/etc/s6-overlay/s6-rc.d/40-bl-haos-daemon/run")
    assert service_path.exists(), "Backend S6-rc service run script must exist"
    content = service_path.read_text(encoding="utf-8")
    assert "uvicorn bl_haos.main:app" in content
    assert "--port 8099" in content
    # The daemon must keep idle keep-alive sockets open longer than any client's
    # pool holds them; otherwise a command can be written to a connection the
    # daemon has just closed, which reaches the user as "Server disconnected".
    assert "--timeout-keep-alive" in content
    assert not Path("rootfs/etc/services.d/bl-haos/run").exists()
