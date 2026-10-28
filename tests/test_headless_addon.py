from pathlib import Path

from fastapi.testclient import TestClient

from backend.bl_haos.api.routes import router
from backend.bl_haos.main import app


RETIRED_PATHS = (
    "/api/adapters", "/api/adapters/hci0/power", "/api/scan/start", "/api/scan/stop",
    "/api/devices", "/api/devices/pair", "/api/devices/10:22:33:44:55:66/connect",
    "/api/devices/10:22:33:44:55:66/disconnect", "/api/devices/10:22:33:44:55:66/volume",
    "/api/devices/10:22:33:44:55:66", "/api/devices/10:22:33:44:55:66/queue/command",
    "/api/settings", "/api/settings/speakers/10:22:33:44:55:66",
)


def test_headless_image_and_routes_have_no_ingress_frontend():
    assert not (Path(__file__).parents[1] / "web_ui").exists()
    assert not any(getattr(route, "path", None) == "/" and getattr(route, "name", None) == "static" for route in app.routes)
    paths = {route.path for route in router.routes}
    for retired in RETIRED_PATHS:
        normalized = retired.replace("10:22:33:44:55:66", "{address}").replace("hci0", "{adapter_name}")
        assert normalized not in paths


def test_retired_management_urls_are_not_registered_or_side_effecting():
    with TestClient(app) as client:
        for path in RETIRED_PATHS:
            response = client.get(path) if path in {"/api/adapters", "/api/devices", "/api/settings"} else client.post(path, json={})
            assert response.status_code == 404, (path, response.status_code, response.text)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/native/identity").status_code == 401


def test_root_is_constant_read_only_migration_notice():
    with TestClient(app) as client:
        response = client.get("/", headers={"host": "untrusted.invalid"})
        assert response.status_code == 200
        assert "Settings > Devices & services > BL-HAOS" in response.text
        assert "integration to 0.2.22 before upgrading the add-on" in response.text
        assert "untrusted.invalid" not in response.text
        assert "<script" not in response.text.lower()
        assert "Authorization" not in response.text
