"""档案业务逻辑：多份简历（D4.1）、默认简历规则、回收站。

本层不接触 HTTP：查不到 / 状态不对一律抛领域异常，由 API 层翻译成 404 / 400。

**D4.1 起一个用户有多份简历**（ADR-006 方案 C）：

- 每份简历 = `student_profiles` 的一行，内容（技能/经历/教育）挂在它下面；
- 「每个用户至少一份默认简历」由这里保证：新建可指定默认、删除默认后自动
  把最新的未删除简历顶上、恢复后若没有默认则把恢复的这份设为默认；
- 删除是两级：`delete_profile()` 进回收站（软删除），`purge_profile()` 才物删，
  且只允许对回收站里的简历执行。
"""

import re
from collections import Counter
from collections.abc import Sequence

from app.core.document_style import DEFAULT_THEME, normalize_theme, resolve_layout
from app.repositories.student_profile_repository import (
    CertificateDraft,
    CityPreferenceDraft,
    EducationDraft,
    ExperienceDraft,
    PortfolioLinkDraft,
    ProfileDraft,
    SkillDraft,
    StudentProfile,
    StudentProfileRepository,
)
from app.schemas.profile import (
    DEFAULT_PAPER_SIZE,
    CertificateRead,
    CityPreferenceRead,
    EducationRead,
    ExperienceRead,
    LayoutConfigRead,
    PortfolioLinkRead,
    ProfileCreateRequest,
    ProfileRead,
    ProfileSummary,
    ProfileUpsertRequest,
    SkillRead,
    normalize_paper_size,
)

#: 新建简历的默认标题前缀：第一份「我的简历」，之后「我的简历 2」「我的简历 3」…
DEFAULT_PROFILE_TITLE = "我的简历"

#: 每人简历上限（ADR-006 §4.4 的建议值）：挡批量建库，超出返回 400。
MAX_PROFILES_PER_USER = 20

_TITLE_PATTERN = re.compile(rf"^{re.escape(DEFAULT_PROFILE_TITLE)}(?: (\d+))?$")


class ProfileNotFoundError(Exception):
    """简历不存在，或不属于当前用户。

    两种情况刻意返回同一个异常：对外都是 404，避免拿 id 探测「别人有没有这份简历」。
    """

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        super().__init__(f"profile {profile_id} not found")


class ProfileStateError(Exception):
    """当前状态不允许该操作（例如彻底删除一份还没进回收站的简历）。"""


class ProfileLimitExceededError(Exception):
    """超过每人简历上限。"""


class ProfileTitleConflictError(Exception):
    """同一用户下的简历重名（D4.3）。

    对外是 400（不是 409）：产品文案固定为「该简历名称已存在」，
    前端只需要展示，不需要按状态码分支。
    """

    def __init__(self, title: str) -> None:
        self.title = title
        super().__init__(f"profile title {title!r} already exists")


class ProfileService:
    def __init__(self, repository: StudentProfileRepository) -> None:
        self._repository = repository

    # ---------------------------------------------------------------- 读

    def list_profiles(
        self,
        user_id: str,
        include_deleted: bool = False,
        order_by: str = "updated_at",
        descending: bool = True,
    ) -> list[ProfileSummary]:
        """档案库列表；默认不含回收站里的记录。

        排序参数由 API 层的 `Literal` 兜住非法值，这一层只做透传（D4.3）。
        """
        return [
            self._to_summary(profile)
            for profile in self._repository.list_by_user(
                user_id,
                include_deleted=include_deleted,
                order_by=order_by,
                descending=descending,
            )
        ]

    def list_trash(self, user_id: str) -> list[ProfileSummary]:
        """回收站列表（只含软删除的，最近删除的在前）。"""
        return [self._to_summary(profile) for profile in self._repository.list_trash(user_id)]

    def get_profile(self, user_id: str) -> ProfileRead | None:
        """取默认简历；一份都没有时返回 `None`（兼容 `GET /profiles/me`）。"""
        profile = self._repository.get_by_user_id(user_id)
        if profile is None:
            return None
        return self._to_read(profile)

    def get_profile_by_id(self, user_id: str, profile_id: str) -> ProfileRead:
        profile = self._repository.get_by_id(user_id, profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        return self._to_read(profile)

    # ---------------------------------------------------------------- 写

    def create_profile(self, user_id: str, payload: ProfileCreateRequest) -> ProfileRead:
        """新建一份简历；`copy_from_id` 非空时服务端深拷贝。

        默认标记的规则：显式 `is_default=True` 或「这是该用户的第一份」时设为默认。
        """
        active = self._repository.list_by_user(user_id)
        if len(active) >= MAX_PROFILES_PER_USER:
            raise ProfileLimitExceededError(f"最多只能保存 {MAX_PROFILES_PER_USER} 份简历")

        title = (payload.title or "").strip() or self._get_next_default_title(user_id)
        # 显式传了重复标题就直接拒（自动递增的标题一定没被占用）。
        # D4.3 立的规矩是「同一用户下不能重名」，创建口子不堵住就形同虚设。
        if self._repository.title_exists(user_id, title):
            raise ProfileTitleConflictError(title)
        if payload.copy_from_id:
            source = self._repository.get_by_id(user_id, payload.copy_from_id)
            if source is None:
                raise ProfileNotFoundError(payload.copy_from_id)
            created = self._repository.duplicate_profile(
                source, user_id=user_id, title=title, is_default=False
            )
        else:
            created = self._repository.create_profile(
                user_id=user_id, title=title, is_default=False
            )

        if payload.is_default or not active:
            self._repository.set_default(user_id, created.id)

        return self._reload(user_id, created.id)

    def save_profile(self, user_id: str, payload: ProfileUpsertRequest) -> ProfileRead:
        """兼容入口（`PUT /profiles/me`）：整体覆盖**默认简历**的内容。

        没有默认简历时先建一份 —— 旧客户端（D4.2 之前的前端）仍只认 `/me`，
        不能因为引入多份简历就让它保存失败。
        """
        profile = self._repository.get_by_user_id(user_id)
        if profile is None:
            profile = self._repository.create_profile(
                user_id=user_id,
                title=self._get_next_default_title(user_id),
                is_default=True,
            )
        return self._save_content(profile, payload)

    def update_profile(
        self, user_id: str, profile_id: str, payload: ProfileUpsertRequest
    ) -> ProfileRead:
        """整体覆盖**指定**简历的内容（`PUT /profiles/{id}`）。"""
        profile = self._repository.get_by_id(user_id, profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        return self._save_content(profile, payload)

    def set_default(self, user_id: str, profile_id: str) -> ProfileRead:
        """把某一份设为默认（回收站里的不能当默认）。"""
        profile = self._repository.get_by_id(user_id, profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        self._repository.set_default(user_id, profile_id)
        return self._reload(user_id, profile_id)

    def rename_profile(self, user_id: str, profile_id: str, title: str) -> ProfileRead:
        """改简历标题；同一用户下不允许重名（D4.3）。

        - 改成和原来一样：幂等成功，**不写库也不推进 `updated_at`**——
          否则用户点一下「保存」列表顺序就跳，看起来像 bug；
        - 回收站里的简历不能改名（`get_by_id` 默认排除回收站）；
        - 重名检查排除自己，否则「原名保存」会被判成冲突。
        """
        profile = self._repository.get_by_id(user_id, profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        normalized = title.strip()
        if normalized == profile.title:
            return self._reload(user_id, profile_id)
        if self._repository.title_exists(user_id, normalized, exclude_id=profile_id):
            raise ProfileTitleConflictError(normalized)
        self._repository.update_title(profile_id, normalized)
        return self._reload(user_id, profile_id)

    # ------------------------------------------------------------ 回收站

    def delete_profile(self, user_id: str, profile_id: str) -> None:
        """软删除（进回收站）。删的是默认简历时，自动把最新的一份顶上。"""
        profile = self._repository.get_by_id(user_id, profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        was_default = profile.is_default
        self._repository.soft_delete(profile_id)
        if was_default:
            self._ensure_default(user_id)

    def restore_profile(self, user_id: str, profile_id: str) -> ProfileRead:
        """从回收站恢复；若恢复后没有任何默认简历，把这份设为默认。"""
        profile = self._repository.get_by_id(user_id, profile_id, include_deleted=True)
        if profile is None or profile.deleted_at is None:
            raise ProfileNotFoundError(profile_id)
        self._repository.restore(profile_id)
        self._ensure_default(user_id)
        return self._reload(user_id, profile_id)

    def purge_profile(self, user_id: str, profile_id: str) -> None:
        """从回收站彻底删除（物理删除父行 + 全部子行）。

        只接受已经软删除的简历：绕过回收站直接物删属于误操作，挡在业务层。
        """
        profile = self._repository.get_by_id(user_id, profile_id, include_deleted=True)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        if profile.deleted_at is None:
            raise ProfileStateError("只能彻底删除回收站中的简历，请先删除再彻底删除。")
        self._repository.hard_delete(profile_id)

    # -------------------------------------------------------------- 内部

    def _save_content(self, profile: StudentProfile, payload: ProfileUpsertRequest) -> ProfileRead:
        draft = ProfileDraft(
            full_name=payload.full_name,
            email=payload.email,
            # 年级列仍在库里但产品已移除该字段：不再写入任何业务值（见仓储）。
            contact=payload.contact,
            target_role=payload.target_role,
            paper_size=self._resolve_paper_size(profile, payload),
            theme=self._resolve_theme(profile, payload),
            layout_config=self._resolve_layout_config(profile, payload),
        )
        skills = [SkillDraft(name=skill.name) for skill in payload.skills]
        experiences = [
            ExperienceDraft(
                title=experience.title,
                start_date=experience.start_date,
                end_date=experience.end_date,
                description=experience.description,
                responsibility=experience.responsibility,
            )
            for experience in payload.experiences
        ]
        educations = [
            EducationDraft(
                school=education.school,
                major=education.major,
                degree=education.degree,
                edu_start=education.edu_start,
                edu_end=education.edu_end,
            )
            for education in payload.educations
        ]
        certificates = [
            CertificateDraft(
                name=certificate.name,
                issuer=certificate.issuer,
                issued_on=certificate.issued_on,
            )
            for certificate in payload.certificates
        ]
        portfolio_links = [
            PortfolioLinkDraft(label=link.label, url=link.url) for link in payload.portfolio_links
        ]
        city_preferences = [
            CityPreferenceDraft(city=city.city) for city in payload.city_preferences
        ]

        # no-op 检测（ADR-018 §5，先例是 `rename_profile` 的「改成原名不写库」）：
        # 内容无变化时**不发任何副作用**——不 bump `updated_at`、不重建子表、不盖
        # `deleted_at`。没有这一条，「全量替换 + 软删除」会把每次无变更保存放大成
        # 历史爆炸：子表行数随保存次数线性增长，而用户什么都没改。
        if self._content_unchanged(
            profile,
            draft,
            skills,
            experiences,
            educations,
            certificates,
            portfolio_links,
            city_preferences,
        ):
            return self._reload(profile.user_id, profile.id)

        self._repository.replace_profile_content(
            profile,
            draft,
            skills,
            experiences,
            educations,
            certificates,
            portfolio_links,
            city_preferences,
        )
        return self._reload(profile.user_id, profile.id)

    def _content_unchanged(
        self,
        profile: StudentProfile,
        draft: ProfileDraft,
        skills: Sequence[SkillDraft],
        experiences: Sequence[ExperienceDraft],
        educations: Sequence[EducationDraft],
        certificates: Sequence[CertificateDraft],
        portfolio_links: Sequence[PortfolioLinkDraft],
        city_preferences: Sequence[CityPreferenceDraft],
    ) -> bool:
        """逐格比对「解析后的本次提交」与「库里的活跃内容」。

        三条口径：

        - 父表字段比的是**三态解析后**的值（`_resolve_*`）：「没传 paper_size」解析成
          库里原值，自然判等；「显式 null」解析成默认值，与原值不同才算变更。
        - 技能 / 证书 / 作品 / 城市按**保存顺序**比（读侧按 created_at 排，顺序即内容：
          用户在表单里拖动排序是一次真实变更）。
        - 经历 / 教育按**多重集**比：读侧与渲染都按开始时间倒序，提交顺序不改变任何
          用户可见物，重排不算变更；同内容两条（多重集计重数）也算一致。
        - 子表只与**活跃行**比：`list_*` 已在仓储层过滤软删除，这里不自比 `deleted_at`。
        """
        if (
            profile.full_name != draft.full_name
            or profile.email != draft.email
            or profile.contact != draft.contact
            or profile.target_role != draft.target_role
            or profile.paper_size != draft.paper_size
            or profile.theme != draft.theme
            or profile.layout_config != draft.layout_config
        ):
            return False
        if [item.name for item in self._repository.list_skills(profile.id)] != [
            item.name for item in skills
        ]:
            return False
        if Counter(
            ExperienceDraft(
                title=item.title,
                start_date=item.start_date,
                end_date=item.end_date,
                description=item.description,
                responsibility=item.responsibility,
            )
            for item in self._repository.list_experiences(profile.id)
        ) != Counter(experiences):
            return False
        if Counter(
            EducationDraft(
                school=item.school,
                major=item.major,
                degree=item.degree,
                edu_start=item.edu_start,
                edu_end=item.edu_end,
            )
            for item in self._repository.list_educations(profile.id)
        ) != Counter(educations):
            return False
        if [
            CertificateDraft(name=item.name, issuer=item.issuer, issued_on=item.issued_on)
            for item in self._repository.list_certificates(profile.id)
        ] != list(certificates):
            return False
        if [
            PortfolioLinkDraft(label=item.label, url=item.url)
            for item in self._repository.list_portfolio_links(profile.id)
        ] != list(portfolio_links):
            return False
        return [
            CityPreferenceDraft(city=item.city)
            for item in self._repository.list_city_preferences(profile.id)
        ] == list(city_preferences)

    @staticmethod
    def _resolve_paper_size(profile: StudentProfile, payload: ProfileUpsertRequest) -> str:
        """把 `paper_size` 的三态语义解析成一个具体值（ADR-007 §10.2）。

        | 请求体 | 结果 |
        |---|---|
        | 不含该键 | 库里原值（旧客户端安全：它压根不知道有这列） |
        | `null` | 回到默认 A4 |
        | `"Letter"` | Letter |

        判据是 `model_fields_set` 而不是「值是否为 None」：后者会把
        「没传」和「显式传 null」压成同一种语义，旧前端一次保存就清空用户选择。
        """
        if "paper_size" not in payload.model_fields_set:
            return profile.paper_size
        return payload.paper_size or DEFAULT_PAPER_SIZE

    @staticmethod
    def _resolve_theme(profile: StudentProfile, payload: ProfileUpsertRequest) -> str:
        """把 `theme` 的三态语义解析成一个具体值（D5.2，与纸张完全同构）。

        | 请求体 | 结果 |
        |---|---|
        | 不含该键 | 库里原值（旧客户端安全） |
        | `null` | 回到默认 `forest` |
        | 预设 id | 该主题 |

        同样用 `model_fields_set` 判定：把「没传」当成「重置」，
        会让任何一个还没升级的前端标签页在保存时把用户挑的配色清掉。
        """
        if "theme" not in payload.model_fields_set:
            return normalize_theme(profile.theme)
        return payload.theme or DEFAULT_THEME

    @staticmethod
    def _resolve_layout_config(
        profile: StudentProfile, payload: ProfileUpsertRequest
    ) -> dict[str, object] | None:
        """把 `layout_config` 的三态语义解析成「要写进库里的那份快照」。

        | 请求体 | 结果 |
        |---|---|
        | 不含该键 | 库里原值（旧客户端安全：它压根不知道有这列） |
        | `null` | `None` → 库里写 NULL，读侧按默认顺序解析 |
        | 对象 | 校验过的完整快照（`model_dump()` 成纯 dict） |

        为什么「对象」要 `model_dump()` 而不是把 pydantic 实例塞进 JSON 列：
        SQLAlchemy 的 `JSON` 类型用 `json.dumps` 序列化，pydantic 实例不是
        JSON 原生类型，会抛 `TypeError`。这里转一次，写入的永远是纯 dict/list/str。

        另外「传对象」这一支返回的是 `model_dump()` 出来的**新 dict**：仓储只做整体赋值
        （ADR-007 §6.8(5)），新对象才会被 SQLAlchemy 判定为变更并写进 UPDATE。
        反过来说，「不含该键」这一支刻意返回原对象——同一对象赋值等于「没有变更」，
        不需要（也不应该）为此写库或推进 `updated_at`。
        """
        if "layout_config" not in payload.model_fields_set:
            return profile.layout_config
        if payload.layout_config is None:
            return None
        return payload.layout_config.model_dump()

    def _ensure_default(self, user_id: str) -> None:
        """保证「每个用户至少一份默认简历」。

        只在「当前没有未删除的默认简历」时动手，取**创建时间最新**的一份顶上。
        这里显式指定 `created_at DESC`（D4.3 起列表默认按 `updated_at` 排，
        不能再拿列表第一行当「最新创建的」）。
        """
        if self._repository.has_default(user_id):
            return
        remaining = self._repository.list_by_user(user_id, order_by="created_at", descending=True)
        if remaining:
            self._repository.set_default(user_id, remaining[0].id)

    def _get_next_default_title(self, user_id: str) -> str:
        """给新简历起名：「我的简历」→「我的简历 2」→「我的简历 3」…

        只在**未删除**的简历里找重名：回收站里那份不占名字，恢复时可能出现同名，
        这是可接受的（列表靠 `updated_at` 与默认标记区分）。
        """
        existing = {profile.title.strip() for profile in self._repository.list_by_user(user_id)}
        if DEFAULT_PROFILE_TITLE not in existing:
            return DEFAULT_PROFILE_TITLE
        used = [
            int(match.group(1) or 1)
            for title in existing
            if (match := _TITLE_PATTERN.match(title)) is not None
        ]
        return f"{DEFAULT_PROFILE_TITLE} {max(used, default=1) + 1}"

    def _reload(self, user_id: str, profile_id: str) -> ProfileRead:
        """写完后重新读一遍：`replace_profile_content()` 提交后实体已过期。"""
        profile = self._repository.get_by_id(user_id, profile_id, include_deleted=True)
        if profile is None:  # pragma: no cover - 刚写完的简历不可能查不到
            raise ProfileNotFoundError(profile_id)
        return self._to_read(profile)

    def _to_read(self, profile: StudentProfile) -> ProfileRead:
        return ProfileRead(
            id=profile.id,
            user_id=profile.user_id,
            title=profile.title,
            is_default=profile.is_default,
            deleted_at=profile.deleted_at,
            paper_size=normalize_paper_size(profile.paper_size),
            theme=normalize_theme(profile.theme),
            # 读侧给**解析后**的结果：脏配置在这里就退化为默认顺序，
            # 前端拿到的顺序与生成器用的是同一份（不需要前端再实现一遍降级矩阵）。
            layout_config=LayoutConfigRead.from_sections(resolve_layout(profile.layout_config)),
            full_name=profile.full_name,
            email=profile.email,
            grade=profile.grade,
            contact=profile.contact,
            target_role=profile.target_role,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            skills=[
                SkillRead.model_validate(skill)
                for skill in self._repository.list_skills(profile.id)
            ],
            experiences=[
                ExperienceRead.model_validate(experience)
                for experience in self._repository.list_experiences(profile.id)
            ],
            educations=[
                EducationRead.model_validate(education)
                for education in self._repository.list_educations(profile.id)
            ],
            certificates=[
                CertificateRead.model_validate(certificate)
                for certificate in self._repository.list_certificates(profile.id)
            ],
            portfolio_links=[
                PortfolioLinkRead.model_validate(link)
                for link in self._repository.list_portfolio_links(profile.id)
            ],
            city_preferences=[
                CityPreferenceRead.model_validate(city)
                for city in self._repository.list_city_preferences(profile.id)
            ],
        )

    def _to_summary(self, profile: StudentProfile) -> ProfileSummary:
        return ProfileSummary(
            id=profile.id,
            title=profile.title,
            is_default=profile.is_default,
            full_name=profile.full_name,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            deleted_at=profile.deleted_at,
        )
