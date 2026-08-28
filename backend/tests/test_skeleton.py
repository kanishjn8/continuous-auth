from backend.app.main import app, healthz


def test_health_probe_reports_protocol_version() -> None:
    assert healthz() == {"status": "alive", "protocol_version": "1.0.0"}


def test_docs_are_not_exposed_by_the_foundation_skeleton() -> None:
    assert app.docs_url is None
    assert app.redoc_url is None
