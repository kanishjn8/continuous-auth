from pathlib import Path

import pytest

from backend.app.compose import create_app


def test_compose_app_requires_dashboard_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CA_DASHBOARD_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="CA_DASHBOARD_SECRET must be set"):
        create_app()


def test_compose_app_loads_authenticated_api_and_ml_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CA_DASHBOARD_SECRET", "synthetic-compose-secret")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    app = create_app()

    assert app.title == "Continuous Authentication Backend"
    assert app.state.api_context.settings.protocol_version == "1.0.0"
    assert any(getattr(route, "path", None) == "/v1/health" for route in app.routes)
