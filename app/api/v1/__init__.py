from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.profiles import router as profiles_router
from app.api.v1.resumes import router as resumes_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(jobs_router)
router.include_router(organizations_router)
router.include_router(profiles_router)
router.include_router(resumes_router)
