import importlib.machinery
import importlib.util
from pathlib import Path


def load_probe_module():
    probe_path = Path("rootfs/usr/bin/bl-haos-probe").resolve()
    loader = importlib.machinery.SourceFileLoader("bl_haos_probe", str(probe_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

import importlib.machinery
import importlib.util


def load_probe_module():
    probe_path = Path("rootfs/usr/bin/bl-haos-probe").resolve()
    loader = importlib.machinery.SourceFileLoader("bl_haos_probe", str(probe_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

def test_dbus_probe_execution():
    probe = load_probe_module()
    result = probe.probe_bluetooth_adapters()
    assert "status" in result
    assert "dbus_connected" in result
    assert isinstance(result["adapters"], list)

def test_dbus_probe_missing_socket_handling(tmp_path):
    probe = load_probe_module()
    fake_socket = str(tmp_path / "non_existent.sock")
    assert probe.probe_dbus_socket(fake_socket) is False
