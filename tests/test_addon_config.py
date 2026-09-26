import re
from pathlib import Path

import yaml


def test_config_yaml_syntax_and_fields():
    config_path = Path("config.yaml")
    assert config_path.exists(), "config.yaml must exist"

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["name"] == "BL-HAOS"
    assert data["slug"] == "bl_haos"
    assert "version" in data and len(data["version"].split(".")) >= 3
    assert data["init"] is False, "S6-overlay v3 requires init: false"
    assert data["ingress"] is True, "Ingress must be enabled"
    assert data["ingress_port"] == 8099
    assert "ports" not in data
    assert data["discovery"] == ["bl_haos"]
    assert data["host_dbus"] is True, "Host D-Bus permission required"
    assert data["audio"] is True, "Internal audio access required for PipeWire sinks"
    assert data["full_access"] is True, "Full access required for audio/bluetooth"
    assert data["udev"] is True, "udev required for hardware tracking"
    assert data["image"] == "ghcr.io/mahmoud-eltabakh/{arch}-bl-haos-bridge"
    assert "services" not in data
    assert "map" not in data

    # Options & schema
    assert "options" in data
    assert "schema" in data
    assert data["options"]["log_level"] == "info"

def test_runtime_version_is_sourced_from_the_shared_constant():
    """The daemon version and the add-on manifest must not drift apart.

    ``config.yaml`` is what Home Assistant reads; ``bl_haos.constants.VERSION`` is
    what the running daemon reports. Bumping one without the other used to be
    silent, so this test makes the drift loud.
    """
    from backend.bl_haos import __version__
    from backend.bl_haos.constants import VERSION

    with open(Path("config.yaml"), "r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    assert VERSION == __version__, "bl_haos.__version__ must mirror constants.VERSION"
    assert manifest["version"] == VERSION, "config.yaml version must match constants.VERSION"


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


def test_dependency_and_release_policy_is_explicit():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/builder.yaml").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "npm --prefix web_ui audit --audit-level=high" in workflow
    assert "pip_audit" in workflow
    assert "severity: HIGH,CRITICAL" in workflow
    assert "stable" in readme.lower() and "preview" in readme.lower()
    assert "apt-get install" in dockerfile and "bluez" in dockerfile


# Supervisor validates a *repository* app's watchdog against this pattern
# (supervisor/apps/validate.py); the boolean form is only accepted for local apps.
SUPERVISOR_REPOSITORY_WATCHDOG = re.compile(r"^(?:https?|\[PROTO:\w+\]|tcp):\/\/\[HOST\]:(\[PORT:\d+\]|\d+).*$")

# The same grammar with named groups, so the test can state which host, port and
# path Supervisor will poll.
WATCHDOG_URL_PARTS = re.compile(r"^(?P<scheme>https?)://\[HOST\]:(?P<port>\[PORT:\d+\]|\d+)(?P<path>.*)$")


def test_watchdog_is_a_repository_app_health_check_url():
    """The manifest must declare ``watchdog`` the way a repository app is allowed to.

    ``config.yaml`` published to a GitHub repository is validated against the
    repository-app schema, where ``watchdog`` is a health-check URL matching
    ``SUPERVISOR_REPOSITORY_WATCHDOG``. ``watchdog: true`` is the *local*-app form:
    publishing it makes the manifest invalid and the Supervisor skips the whole
    app, which removes it from the store.
    """
    from backend.bl_haos.api.routes import router
    from backend.bl_haos.constants import API_PREFIX

    with open(Path("config.yaml"), "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    watchdog = data.get("watchdog")
    assert watchdog is not None, "the container must be supervised"
    assert not isinstance(watchdog, bool), (
        "watchdog: true is the local-app form; a repository app needs a health-check URL"
    )
    assert SUPERVISOR_REPOSITORY_WATCHDOG.match(watchdog), f"Supervisor rejects {watchdog!r}"

    # urlsplit() refuses the [HOST]/[PORT:n] placeholders (it validates a bracketed
    # host as an IPv6 literal), so read the parts the way Supervisor does: it
    # substitutes both placeholders before it uses the URL.
    parts = WATCHDOG_URL_PARTS.match(watchdog)
    assert parts is not None, f"unparsable watchdog URL: {watchdog!r}"
    assert parts["scheme"] in {"http", "https"}, "the watchdog must be an HTTP health check"
    assert parts["port"] == f"[PORT:{data['ingress_port']}]", (
        "the watchdog must poll the port the daemon actually listens on"
    )

    # A watchdog polling a path the daemon does not serve fails every check, and
    # Supervisor then restarts a healthy container in a loop.
    health_path = f"{API_PREFIX}/health"
    assert parts["path"] == health_path, f"watchdog must poll {health_path}, not {parts['path']}"
    assert health_path in {route.path for route in router.routes}, f"no route serves {health_path}"
