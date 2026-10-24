from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.responses import success_response
from app.integrations.resume_generator import ResumeGenerator, TemplateResumeGenerator
from app.repositories.fake_resume_repository import (
    FakeResumeRepository,
    get_fake_resume_repository,
)
from app.schemas.resume import ResumeGenerateRequest
from app.services.resume_service import ResumeService

router = APIRouter(prefix="/resumes", tags=["resumes"])


def get_resume_generator() -> ResumeGenerator:
    return TemplateResumeGenerator()


def get_resume_service(
    repository: Annotated[FakeResumeRepository, Depends(get_fake_resume_repository)],
    generator: Annotated[ResumeGenerator, Depends(get_resume_generator)],
) -> ResumeService:
    return ResumeService(repository=repository, generator=generator)


@router.post("/generate")
def generate_resume(
    request: Request,
    payload: ResumeGenerateRequest,
    service: Annotated[ResumeService, Depends(get_resume_service)],
) -> dict:
    resume = service.generate(payload)
    return success_response(request, resume)
