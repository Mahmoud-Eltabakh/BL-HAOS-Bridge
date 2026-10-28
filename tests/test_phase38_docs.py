from pathlib import Path
import re

import yaml


def test_phase38_candidate_versions_protocol_and_migration_contract():
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    from backend.bl_haos.constants import MINIMUM_INTEGRATION_VERSION, NATIVE_BRIDGE_VERSION, VERSION
    assert VERSION == config["version"] == "0.2.71"
    assert MINIMUM_INTEGRATION_VERSION == "0.2.22"
    assert NATIVE_BRIDGE_VERSION == 1
    docs = Path("DOCS.md").read_text(encoding="utf-8")
    for claim in ("0.2.71", "0.2.22", "0.2.70", "0.2.69", "0.2.21", "integration first", "Repairs", "unknown_legacy", "back up", "rollback", "Phase 39", "no host-port mapping", "does not resume"):
        assert claim.lower() in docs.lower(), f"missing migration claim: {claim}"


def test_current_bridge_docs_do_not_direct_users_to_retired_ui_or_build():
    for name in ("DOCS.md", "README.md"):
        text = Path(name).read_text(encoding="utf-8")
        for forbidden in ("Show in sidebar", "BL-HAOS in your Home Assistant sidebar", "Ingress SpeakerCard", "web_ui/dist", "Ingress Web Dashboard"):
            assert forbidden not in text, f"{name} contains retired UI/build instruction: {forbidden}"
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    release = re.search(r"^## 0\.2\.71.*?(?=^## |\Z)", changelog, re.M | re.S)
    assert release and "local" in release.group().lower() and "undeployed" in release.group().lower()
