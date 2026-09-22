from pathlib import Path

def test_ingress_relative_paths():
    vite_conf = Path("web_ui/vite.config.ts")
    assert vite_conf.exists(), "web_ui/vite.config.ts must exist"

    content = vite_conf.read_text(encoding="utf-8")
    assert 'base: "./"' in content or "base: './'" in content, "Vite base must be relative './' for Ingress support"

    html = Path("web_ui/index.html")
    assert html.exists(), "web_ui/index.html must exist"
    html_content = html.read_text(encoding="utf-8")
    assert "./src/main.tsx" in html_content, "index.html script reference must be relative './src/main.tsx'"

def test_ui_components_exist():
    expected_components = [
        "web_ui/src/App.tsx",
        "web_ui/src/components/SpeakerCard.tsx",
        "web_ui/src/components/DiscoveryModal.tsx",
        "web_ui/src/components/AdapterStatus.tsx",
        "web_ui/src/components/SettingsModal.tsx",
        "web_ui/src/api/client.ts",
        "web_ui/src/hooks/useBluetoothEvents.ts",
    ]
    for comp in expected_components:
        p = Path(comp)
        assert p.exists(), f"Component {comp} must exist"
        assert len(p.read_text(encoding="utf-8")) > 50, f"Component {comp} must not be empty"

def test_dynamic_ws_url_calculation():
    client_ts = Path("web_ui/src/api/client.ts").read_text(encoding="utf-8")
    assert "getWebSocketUrl" in client_ts
    assert "window.location" in client_ts
    assert "window.location.pathname" in client_ts


def test_ingress_shows_native_diagnostics_and_relative_api_error_state():
    client_ts = Path("web_ui/src/api/client.ts").read_text(encoding="utf-8")
    app = Path("web_ui/src/App.tsx").read_text(encoding="utf-8")

    assert "getNativeDiagnostics" in client_ts
    assert "getApiUrl('/api/diagnostics/native')" in client_ts
    assert "Native diagnostics are unavailable" in client_ts
    assert "Native integration" in app
    assert "diagnosticsError" in app
    assert "refreshDiagnostics" in app
