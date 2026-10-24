from fastapi import APIRouter, Request

from app.api.v1 import router as v1_router
from app.core.responses import success_response

router = APIRouter()
router.include_router(v1_router)


@router.get("/health", tags=["system"])
async def health(request: Request) -> dict:
    return success_response(request, {"status": "ok", "service": "api"})


@router.get("/me", tags=["auth"])
async def current_user(request: Request) -> dict:
    return success_response(
        request,
        {
            "authenticated": False,
            "message": "认证模块将在下一阶段接入。",
        },
    )


@router.get("/jobs", tags=["jobs"])
async def list_jobs(request: Request) -> dict:
    return success_response(request, [], page=1, page_size=20, total=0)
