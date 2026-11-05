"""岗位库业务逻辑（Phase 4 · B1，ADR-011 1.F / 2.G）。

本层不接触 HTTP（AGENTS.md §4.1）：**不存在 / 越权 / 已删除** 一律抛同一个领域异常
`JobNotFoundError`，由 `app/api/v1/jobs.py` 翻译成 404。

为什么三者共用一个异常（1.F 的三条硬约束）：
403 会暴露「该 id 存在」，而「存在但无权」本身就是一条信息通道 ——
岗位 id 是 UUID 看似猜不到，但列表接口泄露的 id 会被二次枚举。
收敛成一个异常，api 层就没有「区分两者」的机会，也就没有写错的机会。

三条服务层规则：

1. **过期判定只在这一层用 `datetime.date.today()`**（2.G）。
   不用东八区的 `shanghai_today()`，也不用数据库侧的当日函数：
   全仓时间列都是 `DATETIME(6)` + 应用进程时钟写入，DB 服务器时区一旦与应用
   不一致，`CURDATE()` 给出的「今天」就和写入时用的不是同一个日子，
   而且这种不一致在本地永远测不出来。ADR-011 2.G 定的口径是应用时钟，照此执行。
   过期**不写库、不改状态**：没有 `expired` 状态（1.B），只有读侧判定。
2. **创建恒为 `draft`**：发布是显式动作（1.F #6），不是创建或编辑的副作用。
3. **技能条目的 `origin` / `confidence` / `evidence_text` 不在这一层出现**：它们由仓储写死
   （`origin` 恒 `unverified`，后两列恒 `NULL`，见 ADR-011 1.D 与 1.F 的 v6 补条），
   服务层拿不到、也就不可能采信客户端。
"""

from datetime import date

from app.repositories.job_repository import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    Job,
    JobDraft,
    JobRepository,
    JobSkillDraft,
)
from app.schemas.job import (
    DEFAULT_PAGE_SIZE,
    JobRead,
    JobSkillRead,
    JobSummary,
    JobWriteRequest,
)

#: 列表卡片只展示前 6 条技能（ADR-011 议题 3.B 的卡片规格）。
SUMMARY_SKILL_LIMIT = 6

#: `GET /jobs` 缺省只看已发布（1.F #1：自己的草稿要显式 `?status=draft` 才出现）。
DEFAULT_LIST_STATUS = STATUS_PUBLISHED


class JobNotFoundError(Exception):
    """岗位不存在、不可见，或不属于当前用户。

    三种情况刻意返回同一个异常：对外都是 404，避免拿 id 探测「别人有没有这个岗位」。
    与 `app/services/profile_service.py:47` 的 `ProfileNotFoundError` 同族。
    """

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"job {job_id} not found")


class JobService:
    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    # ------------------------------------------------------------------ 读

    def list_jobs(
        self,
        user_id: str,
        *,
        status: str = DEFAULT_LIST_STATUS,
        keyword: str | None = None,
        include_expired: bool = False,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[JobSummary], int]:
        """广场（`status=published`）或本人的草稿（`status=draft`）列表。

        返回 `(当页条目, 符合筛选的总数)`：`total` 让前端能算页数，
        而筛选（关键词/过期）之后 `total` 必须跟着变，否则翻页会翻进空页。
        """
        today = date.today()
        offset = (page - 1) * page_size
        jobs = self._repository.list_jobs(
            viewer_id=user_id,
            status=status,
            keyword=keyword,
            today=today,
            include_expired=include_expired,
            offset=offset,
            limit=page_size,
        )
        total = self._repository.count_jobs(
            viewer_id=user_id,
            status=status,
            keyword=keyword,
            today=today,
            include_expired=include_expired,
        )
        return [self._to_summary(job, today) for job in jobs], total

    def list_trash(self, user_id: str) -> list[JobSummary]:
        """回收站（1.F #8）：只含**自己**软删除的岗位，过滤在 SQL 里做。"""
        today = date.today()
        return [
            self._to_summary(job, today) for job in self._repository.list_trash(user_id)
        ]

    def get_job(self, user_id: str, job_id: str) -> JobRead:
        """详情（1.F #2）：`published` 任何登录用户可读，`draft` 仅创建者。

        可见性判定在仓储的一个 SQL 谓词里（`get_readable`），这里只把 `None` 翻译成异常。
        """
        job = self._repository.get_readable(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return self._to_read(job)

    # ------------------------------------------------------------------ 写

    def create_job(self, user_id: str, payload: JobWriteRequest) -> JobRead:
        """录入一个岗位：一律落 `status='draft'`，`created_by_user_id` 只来自 JWT。

        请求体里即便塞了 `status` / `created_by_user_id`，它们在 Pydantic
        默认 `extra="ignore"` 那一层就掉了，到不了这里（1.F 的写侧规则）。
        岗位与技能由仓储在**一个事务**里写完（`JobRepository.create_job`）：
        分两次提交时，子表撞唯一键会留下一条「没有技能的岗位」。
        """
        created = self._repository.create_job(
            created_by_user_id=user_id,
            draft=self._to_draft(payload),
            status=STATUS_DRAFT,
            skills=self._to_skill_drafts(payload),
        )
        return self._reload(created)

    def update_job(self, user_id: str, job_id: str, payload: JobWriteRequest) -> JobRead:
        """整体覆盖（1.F #4，仅创建者）：字段覆盖 + 技能子表先删后插，一次提交。

        `status` 原样保留：PUT 改的是内容，发布/下架是独立动作，
        否则「编辑草稿」会顺带把岗位推上广场。
        """
        job = self._repository.get_owned(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._repository.replace_job(job, self._to_draft(payload), self._to_skill_drafts(payload))
        return self._reload(job)

    def publish_job(self, user_id: str, job_id: str) -> JobRead:
        """发布（1.F #6，仅创建者）。非创建者拿到的是 404，不是 403。"""
        return self._set_status(user_id, job_id, STATUS_PUBLISHED)

    def unpublish_job(self, user_id: str, job_id: str) -> JobRead:
        """下架回草稿（1.F #6，仅创建者）。"""
        return self._set_status(user_id, job_id, STATUS_DRAFT)

    def delete_job(self, user_id: str, job_id: str) -> None:
        """软删除进回收站（1.F #5，仅创建者）。

        只打 `deleted_at` 标记，`job_skills` 一行不动 —— 回收站里的岗位必须能原样恢复。
        """
        job = self._repository.get_owned(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._repository.soft_delete(job.id, user_id)

    def restore_job(self, user_id: str, job_id: str) -> JobRead:
        """从回收站恢复（1.F #9，仅创建者；对外是 `PUT /jobs/{job_id}/restore`）。

        两道门都在这里：`get_owned(..., include_deleted=True)` 把非创建者挡成 `None`，
        `job.deleted_at is None` 把「岗位存在但不在回收站」挡成同一个
        `JobNotFoundError` —— 两种情况对外都是 404，不给「存在但无权 / 存在但没被删」
        留任何信息通道（1.F 的 404 而非 403）。

        `status` 原样保留：恢复只是把 `deleted_at` 清回 `NULL`，
        删掉一个已发布岗位再恢复，它必须还是已发布，不该顺手退回草稿。
        """
        job = self._repository.get_owned(job_id, user_id, include_deleted=True)
        if job is None or job.deleted_at is None:
            raise JobNotFoundError(job_id)
        self._repository.restore(job.id, user_id)
        return self._reload(job)

    # ----------------------------------------------------------------- 内部

    def _set_status(self, user_id: str, job_id: str, status: str) -> JobRead:
        job = self._repository.get_owned(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._repository.update_status(job.id, user_id, status)
        return self._reload(job)

    def _reload(self, job: Job) -> JobRead:
        """写完重新读一遍：`create_job()` 会先提交父行，`updated_at` 也要取库里的值。

        用 `get_readable` 而不是 `get_owned`：调用方就是创建者本人，两条谓词对他等价，
        而复用同一个读入口意味着「刚写完的岗位」和「下次打开的岗位」永远长一样。
        """
        reloaded = self._repository.get_readable(job.id, job.created_by_user_id)
        if reloaded is None:  # pragma: no cover - 刚写完的岗位不可能读不到
            raise JobNotFoundError(job.id)
        return self._to_read(reloaded)

    @staticmethod
    def _to_draft(payload: JobWriteRequest) -> JobDraft:
        return JobDraft(
            company_name=payload.company_name,
            title=payload.title,
            location=payload.location,
            employment_type=payload.employment_type,
            description=payload.description,
            salary_min=payload.salary_min,
            salary_max=payload.salary_max,
            experience_required=payload.experience_required,
            deadline=payload.deadline,
        )

    @staticmethod
    def _to_skill_drafts(payload: JobWriteRequest) -> list[JobSkillDraft]:
        """把请求体的技能转成仓储入参：只带 `name` 与 `type`（见模块 docstring 第 3 条）。"""
        return [
            JobSkillDraft(name=skill.name, type=skill.type) for skill in payload.skills
        ]

    @staticmethod
    def is_expired(job: Job, today: date) -> bool:
        """2.G 的唯一判据：`deadline` 非空且**严格早于**今天。

        `deadline = 今天` 还没过期（「今天截止」仍可投递），`deadline IS NULL` 长期有效。
        """
        return job.deadline is not None and job.deadline < today

    def _to_read(self, job: Job) -> JobRead:
        return JobRead(
            id=job.id,
            company_name=job.company_name,
            title=job.title,
            location=job.location,
            employment_type=job.employment_type,
            description=job.description,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            experience_required=job.experience_required,
            deadline=job.deadline,
            status=job.status,  # type: ignore[arg-type]
            created_by_user_id=job.created_by_user_id,
            extract_model=job.extract_model,
            extract_prompt_version=job.extract_prompt_version,
            extracted_at=job.extracted_at,
            deleted_at=job.deleted_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            skills=[
                JobSkillRead.model_validate(skill) for skill in self._repository.list_skills(job.id)
            ],
            is_expired=self.is_expired(job, date.today()),
        )

    def _to_summary(self, job: Job, today: date) -> JobSummary:
        """列表项：不带 `description`（JD 最长 8000 字），技能只给前 6 条。"""
        skills = [
            JobSkillRead.model_validate(skill)
            for skill in self._repository.list_skills(job.id)
        ]
        return JobSummary(
            id=job.id,
            company_name=job.company_name,
            title=job.title,
            location=job.location,
            employment_type=job.employment_type,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            deadline=job.deadline,
            status=job.status,  # type: ignore[arg-type]
            created_by_user_id=job.created_by_user_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            deleted_at=job.deleted_at,
            skills=skills[:SUMMARY_SKILL_LIMIT],
            is_expired=self.is_expired(job, today),
        )
