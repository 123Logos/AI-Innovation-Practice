from datetime import UTC, datetime
from uuid import uuid4

from app.integrations.resume_generator import ResumeGenerator
from app.repositories.fake_resume_repository import FakeResumeRepository
from app.schemas.resume import ResumeGenerateRequest


class ResumeService:
    def __init__(
        self,
        repository: FakeResumeRepository,
        generator: ResumeGenerator,
    ) -> None:
        self._repository = repository
        self._generator = generator

    def generate(self, payload: ResumeGenerateRequest) -> dict[str, str]:
        resume_id = str(uuid4())
        created_at = datetime.now(UTC).replace(tzinfo=None)
        content_html = self._generator.generate(
            profile=payload.profile,
            skills=payload.skills,
            experiences=payload.experiences,
            title=payload.title,
        )
        resume = {
            "resume_id": resume_id,
            "title": payload.title,
            "content_html": content_html,
            "created_at": created_at.isoformat(),
        }
        self._repository.save(resume_id, resume)
        return resume
