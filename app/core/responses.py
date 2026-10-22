from typing import Any

from fastapi import Request


def request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID", "req-local")


def success_response(request: Request, data: Any, **meta: Any) -> dict[str, Any]:
    return {"data": data, "meta": {"request_id": request_id(request), **meta}}


def error_response(
    request: Request,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "retryable": retryable,
        },
        "meta": {"request_id": request_id(request)},
    }
