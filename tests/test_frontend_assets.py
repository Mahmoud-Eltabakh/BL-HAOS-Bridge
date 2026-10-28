from pathlib import Path


def test_retired_ingress_frontend_sources_are_absent():
    assert not Path("web_ui").exists()
    constants = Path("backend/bl_haos/constants.py").read_text(encoding="utf-8")
    main = Path("backend/bl_haos/main.py").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "STATIC_DIRECTORY_CANDIDATES" not in constants + main
    assert "StaticFiles" not in main
    assert "web_ui_build" not in dockerfile
    assert "COPY --from=web_ui_build" not in dockerfile
