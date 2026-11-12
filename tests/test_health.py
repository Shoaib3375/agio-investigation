"""Health endpoint tests."""

import pytest


async def test_health_liveness(client):
    """Liveness probe is always healthy and never depends on external deps."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "agio-investigation"
    assert body["version"] == "0.1.0"


async def test_readiness_ready_when_db_up(client):
    """Readiness returns 200 when the database is reachable, else skips."""
    response = await client.get("/api/v1/ready")
    if response.status_code == 503:
        pytest.skip("investigation database not reachable")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "up"
