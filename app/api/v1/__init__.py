from fastapi import APIRouter

from app.api.v1.assessments import router as assessments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.interviews import router as interviews_router
from app.api.v1.invitations import router as invitations_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.profiles import router as profiles_router
from app.api.v1.resumes import router as resumes_router
from app.api.v1.teacher_tools import export_router as resume_export_router
from app.api.v1.teacher_tools import router as teacher_tools_router

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
# B10a / B10b（`ADR-029`）：`/teacher/*` 两个端点 + `/resumes/{id}/export`。
# 后者必须**排在 `resumes_router` 之后**：它带动态段，而 resumes 组里的
# `/versions/diff` 等静态段靠声明顺序避开被当成 id。
router.include_router(teacher_tools_router)
router.include_router(resume_export_router)
