"""Health routes: /api/health (prefixed) and /health (bare, for infra probes).

Regression: infra probes hit GET /health and got 404 — the health router is
mounted under the /api prefix (app/api/app.py) while its routes only existed
at /api/health (/api/health/ready). See incident log 2026-09-24T20:31
(GET /health 404 x2 from 127.0.0.1).
"""

from __future__ import annotations


def test_health_available_under_api_prefix() -> None:
    from draftly.app.api.app import create_api_app

    app = create_api_app()
    paths = set(app.openapi()["paths"])
    assert "/api/health" in paths
    assert "/api/health/ready" in paths


def test_health_available_bare_for_infra_probes() -> None:
    from draftly.app.api.app import create_api_app

    app = create_api_app()
    paths = set(app.openapi()["paths"])
    assert "/health" in paths
    assert "/health/ready" in paths


def test_health_get_returns_ok_bare() -> None:
    from fastapi.testclient import TestClient

    from draftly.app.api.app import create_api_app

    client = TestClient(create_api_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
