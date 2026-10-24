from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.responses import success_response
from app.schemas.resume import ResumeGenerateRequest
from app.services.dependencies import get_resume_service
from app.services.resume_service import ResumeService

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.post("/generate")
def generate_resume(
    request: Request,
    payload: ResumeGenerateRequest,
    service: Annotated[ResumeService, Depends(get_resume_service)],
) -> dict:
    resume = service.generate(payload)
    return success_response(request, resume)
