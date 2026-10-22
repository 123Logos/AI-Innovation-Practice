from fastapi.testclient import TestClient

from app.main import app


def test_health_response() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert "request_id" in response.json()["meta"]


def test_root_and_health_aliases() -> None:
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok", "service": "api"}


def test_docs_loads_swagger_assets() -> None:
    response = TestClient(app).get("/docs")

    assert response.status_code == 200
    assert "unpkg.com/swagger-ui-dist@5" in response.text
