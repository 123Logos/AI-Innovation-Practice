"""教师端看板与批量下载的请求 / 响应模型（批次 B10a / B10b，`ADR-029`）。

**响应里没有姓名、邮箱、手机号、简历正文**：看板回答「这个班整体怎样」，
不回答「某某同学怎样」（§2.2 第 3 条）。文件名里的学生姓名只存在于 ZIP 条目名，
不进 JSON 响应——序列化模型里根本没这个字段，比「记得别填」可靠。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: 当前只支持 PDF。`docx` / 图片不在本批（ADR-029 §5）。
ExportFormat = Literal["pdf"]
#: 与 `ADR-028` 的 `TagStatus` 逐字一致：同一份数据不许两套叫法。
DashboardTagStatus = Literal["verified", "pending", "improving"]


class BatchDownloadRequest(BaseModel):
    """一次最多 50 条（`AnswerBatchUpsert` 同量级）。

    重复 id 由服务层按请求顺序去重：ZIP 里出现两份同样内容的 PDF 只会让人以为
    有两个学生，而它本来就是客户端的拼接 bug。
    """

    resume_ids: list[str] = Field(min_length=1, max_length=50)
    format: ExportFormat = "pdf"


class TagDistributionItem(BaseModel):
    tag: str
    status: DashboardTagStatus
    student_count: int


class JobCoverageItem(BaseModel):
    job_id: str
    title: str
    required_skill_count: int
    student_count: int
    #: 0–100 整数（half-up），不是 `MatchService` 的 AI 分数。
    avg_coverage: int
    #: 岗位没写要求时 `avg_coverage=0`，但要靠这格区分「0 覆盖」与「没要求」。
    no_requirements: bool
    buckets: dict[str, int]


class DashboardRead(BaseModel):
    organization_id: str
    student_total: int
    tagged_student_total: int
    tag_distribution: list[TagDistributionItem]
    match_overview: list[JobCoverageItem]


class SkippedResume(BaseModel):
    resume_id: str
    reason: Literal["not_found_or_not_in_organization"]


class DownloadableResume(BaseModel):
    """看板页的下载清单（**授权后**才返回姓名；聚合区块本身不含 PII）。"""

    resume_id: str
    profile_id: str
    full_name: str
    title: str
    version_no: int


class BatchManifest(BaseModel):
    """ZIP 里的 `manifest.json`：离线也能回答「这一包到底下了什么」。"""

    generated_at: str
    organization_id: str
    actor_user_id: str
    format: ExportFormat
    item_count: int
    items: list[dict[str, object]]
    skipped: list[SkippedResume]
