from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_healthz_shape_when_broker_and_db_unreachable() -> None:
    # No broker/db running in this test process -- checks the response shape
    # degrades to false/false rather than raising. D1 adds a docker-compose
    # based test that hits a real stack (see dashboard-plan.md "Testing strategy").
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.routers.health._check_broker", new=AsyncMock(return_value=False)):
            response = await client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["api"] is True
    assert body["broker"] is False
    assert body["healthy"] is False
