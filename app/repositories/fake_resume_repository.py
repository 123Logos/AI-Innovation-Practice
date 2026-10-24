from typing import Any


class FakeResumeRepository:
    def __init__(self) -> None:
        self._resumes: dict[str, dict[str, Any]] = {}

    def save(self, resume_id: str, payload: dict[str, Any]) -> None:
        self._resumes[resume_id] = payload

    def get(self, resume_id: str) -> dict[str, Any] | None:
        return self._resumes.get(resume_id)

    def clear(self) -> None:
        self._resumes.clear()


_fake_resume_repository = FakeResumeRepository()


def get_fake_resume_repository() -> FakeResumeRepository:
    return _fake_resume_repository
