from typing import Any

from fastapi import Request

from app.core.responses import request_id


def error_response(
    request: Request,
    code: str,
    message: str,
    details: Any = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details if details is not None else {},
            "retryable": retryable,
        },
        "meta": {"request_id": request_id(request)},
    }
