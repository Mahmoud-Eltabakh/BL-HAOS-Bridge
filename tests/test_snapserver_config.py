from pathlib import Path


def test_snapserver_config():
    conf_path = Path("rootfs/etc/snapcast/snapserver.conf")
    assert conf_path.exists(), "snapserver.conf must exist"

    content = conf_path.read_text(encoding="utf-8")
    assert "port = 1705" in content
    assert "port = 1704" in content
    assert "sampleformat = 48000:16:2" in content
    assert "stream = pipe:///tmp/snapcast/snapfifo" in content

def test_snapserver_s6_service():
    srv_type = Path("rootfs/etc/s6-overlay/s6-rc.d/30-snapserver/type")
    srv_run = Path("rootfs/etc/s6-overlay/s6-rc.d/30-snapserver/run")
    srv_dep = Path("rootfs/etc/s6-overlay/s6-rc.d/30-snapserver/dependencies.d/10-pipewire")
    srv_bundle = Path("rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/30-snapserver")

    assert srv_type.exists(), "30-snapserver/type must exist"
    assert srv_type.read_text(encoding="utf-8").strip() == "longrun"
    assert srv_run.exists(), "30-snapserver/run must exist"
    assert srv_dep.exists(), "30-snapserver must depend on 10-pipewire"
    assert srv_bundle.exists(), "30-snapserver must be bundled in user/contents.d/"

    content = srv_run.read_text(encoding="utf-8")
    assert "snapserver -c /etc/snapcast/snapserver.conf" in content
