from fastapi import APIRouter, Request

from app.api.v1 import router as v1_router
from app.core.responses import success_response

router = APIRouter()
router.include_router(v1_router)


@router.get("/health", tags=["system"])
async def health(request: Request) -> dict:
    return success_response(request, {"status": "ok", "service": "api"})
