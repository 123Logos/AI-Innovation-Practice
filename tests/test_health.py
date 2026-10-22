from fastapi.testclient import TestClient

from app.main import app


def test_health_response() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert "request_id" in response.json()["meta"]
