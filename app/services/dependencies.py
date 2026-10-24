from app.integrations.resume_generator import TemplateResumeGenerator
from app.repositories.fake_resume_repository import (
    FakeResumeRepository,
    get_fake_resume_repository,
)
from app.services.resume_service import ResumeService


def get_resume_service() -> ResumeService:
    repository: FakeResumeRepository = get_fake_resume_repository()
    return ResumeService(
        repository=repository,
        generator=TemplateResumeGenerator(),
    )
