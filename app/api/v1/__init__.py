from fastapi import APIRouter

from app.api.v1.resumes import router as resumes_router

router = APIRouter()
router.include_router(resumes_router)
