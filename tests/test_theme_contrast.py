from pathlib import Path


def test_theme_contrast_gate_replaced_by_headless_notice_contract():
    source = Path("backend/bl_haos/main.py").read_text(encoding="utf-8")
    assert "RETIREMENT_NOTICE" in source
    assert "Settings > Devices & services > BL-HAOS" in source
    assert '@app.get("/"' in source
    assert "StaticFiles" not in source
