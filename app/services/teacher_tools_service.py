"""教师端学情看板与批量导出的编排层（FR-08 / B10a、B10b，`ADR-029`）。

**本文件最重要的一条是「第一句代码」**（第 19 轮硬约束）：每个公开方法的第一条语句
都是 `self._require_organization(...)`，而 `_require_organization` 的第一条语句就是
`app/core/action_gate.py::require_organization` 的真调用。散人的请求因此**读不到任何
业务表**：拦截发生在 `student_profile_ids()` / `documents()` 之前，只写一条拒绝审计。
`tests/test_teacher_tools.py` 有一条源码级用例把这两层位置钉住，防止后续重构把它挪到
「先查再判」而看不出差别。

分工：范围判定在仓储的 SQL 里，数字口径在 `app/core/teacher_tools_rules.py`（纯函数），
PDF 字节来自可注入的 `PdfRenderer`（测试换假实现），审计是**同一张 `audit_logs`**
（`ADR-029` §2.4），提交点全在本层。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from typing import Any

from app.core.action_gate import (
    ActionRequiresOrganization,
    OrganizationAction,
    require_organization,
)
from app.core.datetime_display import format_shanghai_datetime, utc_now
from app.core.pdf_errors import PdfRendererUnavailableError
from app.core.teacher_tools_rules import (
    PaperStylesUnavailableError,
    build_export_html,
    coverage_bucket,
    coverage_percent,
    empty_buckets,
    safe_zip_filename,
    skill_confirm_audit_detail,
    tag_distribution,
    teacher_audit_detail,
)
from app.integrations.pdf_renderer import PdfRenderer
from app.repositories.audit_log_repository import (
    AuditAction,
    AuditLogRepository,
    AuditResult,
)
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.teacher_tools_repository import TeacherToolsRepository
from app.schemas.teacher_tools import (
    BatchManifest,
    DashboardRead,
    DownloadableResume,
    JobCoverageItem,
    SkillConfirmation,
    SkippedResume,
    TagDistributionItem,
)
from app.services.auth_service import CurrentUserContext

logger = logging.getLogger(__name__)


class TeacherOrganizationRequiredError(Exception):
    """散人试图使用教师端工具；对外 400 + 「请先绑定组织」。"""


class ResumeExportNotFoundError(Exception):
    """单份导出找不到 / 不属于本组织；对外 404，不区分原因。"""


class NoDownloadableResumeError(Exception):
    """批量请求里一个可下载项都没剩下；对外 400。"""


class SkillConfirmProfileNotFoundError(Exception):
    """档案不在本组织 / 已删除；对外 404，不区分两种原因。"""


class SkillConfirmNameInvalidError(Exception):
    """技能名为空或超长；对外 400（模型层也有 `min_length`/`max_length` 兜一次）。"""


@dataclass(frozen=True)
class DownloadResult:
    filename: str
    content: bytes


class TeacherToolsService:
    def __init__(
        self,
        *,
        repository: TeacherToolsRepository,
        audit: AuditLogRepository,
        organizations: OrganizationRepository,
        renderer: PdfRenderer,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._organizations = organizations
        self._renderer = renderer

    # ------------------------------------------------------------------ 闸

    def _require_organization(
        self,
        context: CurrentUserContext,
        *,
        action: OrganizationAction,
        audit_action: AuditAction,
        resource_ids: list[str],
        ip: str | None,
    ) -> str:
        """所有入口的第一站：**第一条语句就是动作闸**，之前不读任何业务表。

        拒绝也要留账（B5 `AuditResult.DENIED` 的既有纪律）：「谁在试图导出却没有组织」
        是唯一证据。审计与业务同事务，`commit()` 在这里，因为拒绝路径不会再走到
        调用方末尾的提交点。
        """
        try:
            return require_organization(
                context.organization.id if context.organization is not None else None,
                action=action,
            )
        except ActionRequiresOrganization as error:
            self._write_audit(
                context=context,
                organization_id=None,
                action=audit_action,
                target_type="organization",
                target_id=None,
                result=AuditResult.DENIED,
                resource_ids=resource_ids,
                item_count=0,
                bytes_count=0,
                reason="no_organization",
                ip=ip,
            )
            self._audit.commit()
            raise TeacherOrganizationRequiredError(context.user.id) from error

    # ------------------------------------------------------------------ 看板

    def dashboard(self, context: CurrentUserContext, *, ip: str | None = None) -> DashboardRead:
        """标签分布 + 岗位技能覆盖率概况（只读、不调 AI、不返回个人信息）。"""
        organization_id = self._require_organization(
            context,
            action=OrganizationAction.VIEW_DASHBOARD,
            audit_action=AuditAction.DASHBOARD_VIEW,
            resource_ids=[],
            ip=ip,
        )

        profile_ids = self._repository.student_profile_ids(organization_id)
        tag_rows = self._repository.tag_rows(profile_ids)
        distribution = tag_distribution(tag_rows)
        tagged_total = len({profile_id for _, _, profile_id in tag_rows})

        skills = self._repository.skills_by_profile(profile_ids)
        overview: list[JobCoverageItem] = []
        for job in self._repository.published_jobs_with_skills(organization_id):
            required = [str(name) for name in job["required_skills"]]
            percents = [
                coverage_percent(required, skills.get(profile_id, [])) for profile_id in profile_ids
            ]
            buckets = empty_buckets()
            for percent in percents:
                buckets[coverage_bucket(percent)] += 1
            average = (sum(percents) * 2 + len(percents)) // (2 * len(percents)) if percents else 0
            overview.append(
                JobCoverageItem(
                    job_id=str(job["job_id"]),
                    title=str(job["title"]),
                    required_skill_count=len(set(required)),
                    student_count=len(percents),
                    avg_coverage=average,
                    no_requirements=bool(job["no_requirements"]),
                    buckets=buckets,
                )
            )

        self._write_audit(
            context=context,
            organization_id=organization_id,
            action=AuditAction.DASHBOARD_VIEW,
            target_type="organization",
            target_id=organization_id,
            result=AuditResult.OK,
            resource_ids=[],
            item_count=0,
            bytes_count=0,
            reason=None,
            ip=ip,
        )
        self._audit.commit()
        return DashboardRead(
            organization_id=organization_id,
            student_total=len(profile_ids),
            tagged_student_total=tagged_total,
            tag_distribution=[TagDistributionItem(**row) for row in distribution],  # type: ignore[arg-type]
            match_overview=overview,
        )

    # ------------------------------------------------------------------ 清单

    def list_downloadable(
        self, context: CurrentUserContext, *, ip: str | None = None
    ) -> list[DownloadableResume]:
        """教师看板页的下载清单。与聚合读同一把动作闸，但**不写 ok 审计**：
        它只是页面加载时的一次读，审计记的是「看了一眼看板」和「真的带走了文件」
        （`ADR-029` §2.4），给每次列表刷新都记一条只会把有效证据淹掉。
        """
        organization_id = self._require_organization(
            context,
            action=OrganizationAction.VIEW_DASHBOARD,
            audit_action=AuditAction.DASHBOARD_VIEW,
            resource_ids=[],
            ip=ip,
        )

        return [
            DownloadableResume(
                resume_id=item.resume_id,
                profile_id=item.profile_id,
                full_name=item.full_name,
                title=item.title,
                version_no=item.version_no,
            )
            for item in self._repository.all_documents(organization_id)
        ]

    # ------------------------------------------------------------------ 导出

    def export_pdf(
        self, context: CurrentUserContext, resume_id: str, *, ip: str | None = None
    ) -> DownloadResult:
        """单份 PDF 导出：打印 HTML → renderer → 审计。"""
        organization_id = self._require_organization(
            context,
            action=OrganizationAction.EXPORT_RESUME,
            audit_action=AuditAction.RESUME_EXPORT_PDF,
            resource_ids=[resume_id],
            ip=ip,
        )

        document = self._repository.one_document(organization_id, resume_id)
        if document is None:
            raise ResumeExportNotFoundError(resume_id)

        try:
            content = self._render(document.content_html, document.paper_size)
        except PdfRendererUnavailableError:
            self._write_audit(
                context=context,
                organization_id=organization_id,
                action=AuditAction.RESUME_EXPORT_PDF,
                target_type="resume",
                target_id=document.resume_id,
                result=AuditResult.DENIED,
                resource_ids=[document.resume_id],
                item_count=0,
                bytes_count=0,
                reason="renderer_unavailable",
                ip=ip,
            )
            self._audit.commit()
            raise
        filename = safe_zip_filename(1, document.full_name, document.version_no)
        self._write_audit(
            context=context,
            organization_id=organization_id,
            action=AuditAction.RESUME_EXPORT_PDF,
            target_type="resume",
            target_id=document.resume_id,
            result=AuditResult.OK,
            resource_ids=[document.resume_id],
            item_count=1,
            bytes_count=len(content),
            reason=None,
            ip=ip,
        )
        self._audit.commit()
        return DownloadResult(filename=filename, content=content)

    def batch_download(
        self,
        context: CurrentUserContext,
        resume_ids: list[str],
        *,
        ip: str | None = None,
    ) -> DownloadResult:
        """打包下载：ZIP 结构 / manifest / 审计一次完成（ADR-029 §2.3、§2.4）。"""
        organization_id = self._require_organization(
            context,
            action=OrganizationAction.BULK_DOWNLOAD,
            audit_action=AuditAction.RESUME_BATCH_DOWNLOAD,
            resource_ids=list(resume_ids),
            ip=ip,
        )

        # 去重但保序：同一 id 出现两次只出一份 PDF，顺序仍按用户勾选的顺序。
        ordered_ids = list(dict.fromkeys(resume_ids))
        documents, skipped = self._repository.documents(organization_id, ordered_ids)
        if not documents:
            self._write_audit(
                context=context,
                organization_id=organization_id,
                action=AuditAction.RESUME_BATCH_DOWNLOAD,
                target_type="resume_batch",
                target_id=None,
                result=AuditResult.DENIED,
                resource_ids=ordered_ids,
                item_count=0,
                bytes_count=0,
                reason="no_downloadable_resume",
                ip=ip,
            )
            self._audit.commit()
            raise NoDownloadableResumeError(ordered_ids)

        try:
            items, files = self._build_archive_files(documents)
        except (PaperStylesUnavailableError, PdfRendererUnavailableError):
            self._write_audit(
                context=context,
                organization_id=organization_id,
                action=AuditAction.RESUME_BATCH_DOWNLOAD,
                target_type="resume_batch",
                target_id=None,
                result=AuditResult.DENIED,
                resource_ids=ordered_ids,
                item_count=0,
                bytes_count=0,
                reason="renderer_unavailable",
                ip=ip,
            )
            self._audit.commit()
            raise

        generated_at = format_shanghai_datetime(utc_now()) or ""
        organization = self._organizations.get_by_id(organization_id)
        organization_name = organization.name if organization is not None else "未知组织"
        manifest = BatchManifest(
            generated_at=generated_at,
            organization_id=organization_id,
            actor_user_id=context.user.id,
            format="pdf",
            item_count=len(items),
            items=items,
            skipped=[
                SkippedResume(resume_id=resume_id, reason="not_found_or_not_in_organization")
                for resume_id in skipped
            ],
        )
        readme = (
            "简历批量导出\n"
            f"导出时间：{generated_at}\n"
            f"组织：{organization_name}\n"
            f"发起人：{context.user.username}\n"
            f"条目数：{len(items)}（跳过 {len(skipped)} 条）\n"
            "格式：PDF\n"
            "本包只含已授权导出到本组织的简历版本；逐条校验和见 manifest.json。\n"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("README.txt", readme)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            for name, content in files:
                archive.writestr(f"resumes/{name}", content)
        content = buffer.getvalue()

        self._write_audit(
            context=context,
            organization_id=organization_id,
            action=AuditAction.RESUME_BATCH_DOWNLOAD,
            target_type="resume_batch",
            target_id=None,
            result=AuditResult.OK,
            resource_ids=[item["resume_id"] for item in items],  # type: ignore[misc]
            item_count=len(items),
            bytes_count=len(content),
            reason=None,
            ip=ip,
        )
        self._audit.commit()
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        return DownloadResult(filename=f"resumes_{stamp}.zip", content=content)

    # ------------------------------------------------------ 教师确认（B7）

    def confirm_skill(
        self,
        context: CurrentUserContext,
        profile_id: str,
        skill_name: str,
        *,
        ip: str | None = None,
    ) -> SkillConfirmation:
        """教师确认一个学生的技能（B7 证据链，`ADR-027` §5 第 3 条）。

        三件事按顺序发生，顺序本身是契约：

        1. **动作闸**（第一句）：没有组织 → 400「请先绑定组织」，且**在读写任何行之前**；
        2. **归属判定在 SQL 里**：档案必须属于本组织的 `role='student'` 成员且未删除，
           否则 404（与「档案不存在」同一个响应，不告诉外人它存在）；
        3. **写两处、只升不建**：`profile_tags` 写一条 `source='teacher'` 的标签；
           若学生简历里**已有**同名技能，把它的来源升成 `teacher_confirmed`，
           没有就**不新建**（简历里写哪些技能是学生自己的声明，见 `ADR-028` §2.6）。
        """
        organization_id = self._require_organization(
            context,
            action=OrganizationAction.CONFIRM_SKILL,
            audit_action=AuditAction.SKILL_TEACHER_CONFIRM,
            resource_ids=[profile_id],
            ip=ip,
        )

        normalized = skill_name.strip()
        if not normalized or len(normalized) > 100:
            raise SkillConfirmNameInvalidError(profile_id)

        profile = self._repository.student_profile_in_organization(organization_id, profile_id)
        if profile is None:
            raise SkillConfirmProfileNotFoundError(profile_id)

        now = utc_now()
        tag_row, previous_source = self._repository.confirm_tag(
            student_profile_id=profile.id, tag=normalized, now=now
        )
        previous_skill_source = self._repository.upgrade_skill_source(
            student_profile_id=profile.id, skill_name=normalized
        )

        self._write_audit(
            context=context,
            organization_id=organization_id,
            action=AuditAction.SKILL_TEACHER_CONFIRM,
            target_type="student_profile",
            target_id=profile.id,
            result=AuditResult.OK,
            resource_ids=[profile.id],
            item_count=1,
            bytes_count=0,
            reason=None,
            ip=ip,
            # 覆盖会把原来源冲掉，所以「原来是什么」只能在这条审计里留痕。
            detail=skill_confirm_audit_detail(
                normalized,
                previous_tag_source=previous_source,
                previous_skill_source=previous_skill_source,
            ),
        )
        self._repository.commit()
        self._audit.commit()

        return SkillConfirmation(
            profile_id=profile.id,
            tag=str(tag_row.tag),
            status=str(tag_row.status),  # type: ignore[arg-type]
            source="teacher",
            score=int(tag_row.score),
            evidence_count=int(tag_row.evidence_count),
            previous_tag_source=previous_source,
            upgraded_skill_source=previous_skill_source is not None,
        )

    # ------------------------------------------------------------------ 内部

    def _render(self, content_html: str, paper_size: str) -> bytes:
        """纸面 CSS 读不到与浏览器不可用归到同一个对外语义（503）。"""
        try:
            html = build_export_html(content_html, paper_size)
        except PaperStylesUnavailableError as error:
            raise PdfRendererUnavailableError(str(error)) from error
        return self._renderer.render(html)

    def _build_archive_files(
        self, documents: list[Any]
    ) -> tuple[list[dict[str, object]], list[tuple[str, bytes]]]:
        items: list[dict[str, object]] = []
        files: list[tuple[str, bytes]] = []
        for index, document in enumerate(documents, start=1):
            pdf = self._render(document.content_html, document.paper_size)
            name = safe_zip_filename(index, document.full_name, document.version_no)
            files.append((name, pdf))
            items.append(
                {
                    "resume_id": document.resume_id,
                    "profile_id": document.profile_id,
                    "version_no": document.version_no,
                    "file": f"resumes/{name}",
                    "sha256": hashlib.sha256(pdf).hexdigest(),
                    "bytes": len(pdf),
                }
            )
        return items, files

    def _write_audit(
        self,
        *,
        context: CurrentUserContext,
        organization_id: str | None,
        action: AuditAction,
        target_type: str,
        target_id: str | None,
        result: AuditResult,
        resource_ids: list[str],
        item_count: int,
        bytes_count: int,
        reason: str | None,
        ip: str | None,
        detail: str | None = None,
    ) -> None:
        """`audit_logs` 的行形状见 `ADR-029` §2.4 那张表；一处写，不在调用点拼字段。

        `detail` 只有「教师确认技能」会传：它的事件形状与下载不同（没有文件、没有字节数），
        强行套下载那一份 `format/bytes` 会写出一条自相矛盾的审计。
        默认路径仍然只走 `teacher_audit_detail()`，避免字段名在调用点分叉。
        """
        self._audit.record(
            action=action,
            actor_user_id=context.user.id,
            organization_id=organization_id,
            target_type=target_type,
            target_id=target_id,
            result=result,
            detail=detail
            if detail is not None
            else teacher_audit_detail(resource_ids, item_count, bytes_count, reason=reason),
            ip=ip,
        )


__all__ = [
    "DownloadResult",
    "NoDownloadableResumeError",
    "ResumeExportNotFoundError",
    "SkillConfirmNameInvalidError",
    "SkillConfirmProfileNotFoundError",
    "TeacherOrganizationRequiredError",
    "TeacherToolsService",
]
