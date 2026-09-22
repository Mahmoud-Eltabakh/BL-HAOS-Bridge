import yaml
from pathlib import Path

def test_config_yaml_syntax_and_fields():
    config_path = Path("config.yaml")
    assert config_path.exists(), "config.yaml must exist"

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["name"] == "BL-HAOS"
    assert data["slug"] == "bl_haos"
    assert data["version"] == "0.2.5"
    assert data["init"] is False, "S6-overlay v3 requires init: false"
    assert data["ingress"] is True, "Ingress must be enabled"
    assert data["ingress_port"] == 8099
    assert "ports" not in data
    assert data["discovery"] == ["bl_haos"]
    assert data["host_dbus"] is True, "Host D-Bus permission required"
    assert data["full_access"] is True, "Full access required for audio/bluetooth"
    assert data["udev"] is True, "udev required for hardware tracking"
    assert data["image"] == "ghcr.io/mahmoud-eltabakh/{arch}-bl-haos-bridge"
    assert "services" not in data
    assert "map" not in data

    # Options & schema
    assert "options" in data
    assert "schema" in data
    assert data["options"]["log_level"] == "info"

def test_repository_yaml_and_documentation():
    repo_path = Path("repository.yaml")
    assert repo_path.exists(), "repository.yaml must exist at root of add-on repository"

    with open(repo_path, "r", encoding="utf-8") as f:
        repo_data = yaml.safe_load(f)
    assert "name" in repo_data
    assert "url" in repo_data
    assert "maintainer" in repo_data

    assert Path("DOCS.md").exists(), "DOCS.md must exist for Home Assistant Add-on presentation"
    assert Path("README.md").exists(), "README.md must exist"
    assert Path("CHANGELOG.md").exists(), "CHANGELOG.md must exist"

    trans_path = Path("translations/en.yaml")
    assert trans_path.exists(), "translations/en.yaml must exist for configuration UI strings"
    with open(trans_path, "r", encoding="utf-8") as f:
        trans_data = yaml.safe_load(f)
    assert "configuration" in trans_data
    assert "log_level" in trans_data["configuration"]
    assert "bridge_token" not in trans_data["configuration"]
