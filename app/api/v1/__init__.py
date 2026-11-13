from fastapi import APIRouter

from app.api.v1.assessments import router as assessments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.interviews import router as interviews_router
from app.api.v1.invitations import router as invitations_router
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
router.include_router(interviews_router)
router.include_router(invitations_router)
# B9（`ADR-028`）：标准化题库测评与画像。放在最后只是排版，路由前缀 `/assessments`
# 与其它组不重叠；组内静态段（`/profile`）已声明在 `/sessions/{id}` 之前。
router.include_router(assessments_router)
