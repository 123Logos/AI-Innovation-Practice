from typing import NoReturn

from fastapi.testclient import TestClient

from app.api.v1.resumes import get_resume_service
from app.main import app


def test_not_found_uses_unified_error_response() -> None:
    response = TestClient(app).get("/api/v1/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["meta"]["request_id"]


def test_validation_error_uses_unified_error_response() -> None:
    response = TestClient(app).post("/api/v1/resumes/generate", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]
    assert body["meta"]["request_id"]


def test_method_not_allowed_uses_unified_error_response() -> None:
    response = TestClient(app).get("/api/v1/resumes/generate")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert response.json()["meta"]["request_id"]


def test_internal_error_uses_unified_error_response(monkeypatch) -> None:
    def raise_error() -> NoReturn:
        raise RuntimeError("test failure")

    app.dependency_overrides[get_resume_service] = raise_error
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/resumes/generate",
            json={
                "title": "x",
                "profile": {
                    "full_name": "张三",
                    "major": "计算机",
                    "grade": "大三",
                    "contact": "13800000000",
                },
                "skills": [
                    {"name": "Python", "self_rating": 3},
                    {"name": "SQL", "self_rating": 3},
                    {"name": "React", "self_rating": 3},
                ],
                "experiences": [
                    {
                        "title": "项目",
                        "start_date": "2024-01-01",
                        "description": "描述",
                        "responsibility": "职责",
                    }
                ],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["meta"]["request_id"]
