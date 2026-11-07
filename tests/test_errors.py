from datetime import UTC, datetime
from typing import NoReturn

from fastapi.testclient import TestClient

from app.api.v1.resumes import get_resume_service
from app.main import app
from app.schemas.auth import UserRead
from app.services.dependencies import get_current_user


def _fake_current_user() -> UserRead:
    """T7.2 起 /resumes/generate 需要登录态；依赖先于请求体校验，故需绕过鉴权才能测 422。"""
    return UserRead(id="test-user", username="tester", email=None, created_at=datetime.now(UTC))


def test_not_found_uses_unified_error_response() -> None:
    response = TestClient(app).get("/api/v1/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["meta"]["request_id"]


def test_validation_error_uses_unified_error_response() -> None:
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        response = TestClient(app).post("/api/v1/resumes/generate", json={})
    finally:
        app.dependency_overrides.clear()

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
                    {"name": "Python"},
                    {"name": "SQL"},
                    {"name": "React"},
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
