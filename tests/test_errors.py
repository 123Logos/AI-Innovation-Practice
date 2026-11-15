from datetime import UTC, datetime
from typing import NoReturn

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.resumes import get_resume_service
from app.core.error_messages import PROSE_MAX_LENGTH, ErrorMessage, is_human_prose
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


# --------------------------------------------------------------------------- #
# 2026-11-15 第 26 轮：4xx 的中文 detail 原样放行（5xx 绝不放行）
# --------------------------------------------------------------------------- #


def _raise_dependency(exc: HTTPException):
    """把任意 HTTPException 从依赖里抛出来：走的就是全局处理器那条路。"""

    def raiser() -> NoReturn:
        raise exc

    return raiser


def _request_with_detail(detail: object, status_code: int = 400):
    """发一个必然经过 `http_exception` 处理器的请求，返回响应。"""
    app.dependency_overrides[get_current_user] = _raise_dependency(
        HTTPException(status_code=status_code, detail=detail)
    )
    try:
        return TestClient(app).get("/api/v1/profiles/me")
    finally:
        app.dependency_overrides.clear()


def test_chinese_400_detail_is_passed_through() -> None:
    """表里没有、但写着「下一步做什么」的中文句子必须原样上屏。

    压成「请求未能完成，暂时无法确认原因」等于把一句用户能自己修的话换成
    「去问维护人员」——这正是第 26 轮要修的那件事。
    """
    sentence = "还没保存过档案：请先在简历工作台填好基本信息再生成。"

    response = _request_with_detail(sentence)

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["message"] == sentence
    # 原句直传不是「表里少了一条」：不该再记 UNMAPPED。
    assert (body["error"].get("details") or {}).get("reason") is None


def test_english_400_detail_still_falls_back_to_generic() -> None:
    """英文 detail 依然不进上屏文案（F1c 收口的那道洞）。"""
    response = _request_with_detail("There was an error parsing the body")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["message"] == str(ErrorMessage.CLIENT)
    assert body["error"]["details"]["reason"] == "UNMAPPED"


def test_multiline_chinese_detail_falls_back_to_generic() -> None:
    """多行 detail 通常是把 traceback / 异常文本塞进来，不许上屏。"""
    response = _request_with_detail("文件读不到：\nTraceback (most recent call last):")

    assert response.json()["error"]["message"] == str(ErrorMessage.CLIENT)
    assert response.json()["error"]["details"]["reason"] == "UNMAPPED"


def test_overlong_chinese_detail_falls_back_to_generic() -> None:
    """整段 payload 被当 detail 时不许上屏。"""
    response = _request_with_detail("这" * (PROSE_MAX_LENGTH + 1))

    assert response.json()["error"]["message"] == str(ErrorMessage.CLIENT)


def test_template_braced_detail_falls_back_to_generic() -> None:
    """`{bound}` 这类占位符原样上屏会直接露在用户面前。"""
    response = _request_with_detail("请填写不少于 {bound} 个字符。")

    assert response.json()["error"]["message"] == str(ErrorMessage.CLIENT)


def test_5xx_never_passes_detail_through() -> None:
    """5xx 只有一句固定文案：放行只针对 4xx，堆栈里的话一个字都不许出去。"""
    sentence = "数据库连接失败：root@127.0.0.1 拒绝连接"

    body = _request_with_detail(sentence, status_code=502).json()

    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == str(ErrorMessage.INTERNAL)
    assert sentence not in body["error"]["message"]


def test_mapped_detail_keeps_its_table_sentence() -> None:
    """表里认得的 detail 仍然换成表里那句（不能被「原文放行」抢了先）。"""
    body = _request_with_detail(str(ErrorMessage.RESUME_POINTER_BROKEN)).json()

    assert body["error"]["message"] == str(ErrorMessage.RESUME_POINTER_BROKEN)
    assert body["error"]["details"]["reason"] == ErrorMessage.RESUME_POINTER_BROKEN.name


def test_is_human_prose_boundaries() -> None:
    """判据四条逐条钉住：这是放行口的唯一闸，松一格就是一次泄露。"""
    assert is_human_prose("请先保存档案。") is True
    assert is_human_prose("") is False
    assert is_human_prose(None) is False
    assert is_human_prose(400) is False
    assert is_human_prose("Not Found") is False
    assert is_human_prose("下一行\n也是中文") is False
    assert is_human_prose("占位 {bound}") is False
    assert is_human_prose("这" * PROSE_MAX_LENGTH) is True
    assert is_human_prose("这" * (PROSE_MAX_LENGTH + 1)) is False
