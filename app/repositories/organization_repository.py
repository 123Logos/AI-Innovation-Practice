"""组织与成员关系的映射与仓储（批次 B5，`docs/adr/ADR-021` §2.1）。

两张表由 `alembic/versions/20261112_0018_organizations_roles_audit.py` 建立，本模块只做映射。

**角色挂在成员关系上，不挂 `users`**：一个人将来可能既是 A 校教师又是 B 企业导师，
把角色写进用户表就是把「身份」钉死成「全局属性」，第一次出现双身份就得改表。

**本模块的方法默认不 commit**（`add_member` / `set_member_role` 都只 flush）：
注册这一笔要「建用户 + 建成员关系 + 写审计」三件事同生共死，
提交点交给 `AuthService` 统一掌握（ADR-021 §2.5）。读方法当然不提交。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.datetime_display import utc_now
from app.repositories.user_repository import Base, User


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: 同时是邀请码：唯一键在库上，落库前额外 `strip().upper()`（见 `normalize_code`）。
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="school")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: `UserRole` 的字符串值。存 str 而不是 Enum 列：MySQL 的 ENUM 加值要 ALTER TABLE，
    #: 而角色清单是本模块上面的常量，改它不该是一次迁移。
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="student")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


def normalize_code(code: str) -> str:
    """组织码的落库形态：去空白 + 全大写。

    大小写不敏感已经由 collation（`utf8mb4_0900_ai_ci`）保证，这里再归一一次是为了
    「查重与落库用同一个值」——否则会先按原样查不到、再按原样插进一条大小写不同的记录，
    和用户名那次（`ADR-005`）是同一个坑。
    """
    return code.strip().upper()


class OrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------- 组织 -------------------------------

    def create(self, *, code: str, name: str, kind: str = "school") -> Organization:
        now = utc_now()
        organization = Organization(
            id=str(uuid4()),
            code=normalize_code(code),
            name=name.strip(),
            kind=kind,
            created_at=now,
            updated_at=now,
        )
        self._session.add(organization)
        self._session.flush()
        return organization

    def get_by_id(self, organization_id: str) -> Organization | None:
        return self._session.get(Organization, organization_id)

    def get_by_code(self, code: str) -> Organization | None:
        return self._session.scalar(
            select(Organization).where(Organization.code == normalize_code(code))
        )

    # ------------------------------ 成员关系 ------------------------------

    def get_member(self, user_id: str) -> OrganizationMember | None:
        return self._session.scalar(
            select(OrganizationMember).where(OrganizationMember.user_id == user_id)
        )

    def get_organization_of(self, user_id: str) -> Organization | None:
        """当前用户的组织；没有成员关系时返回 `None`（不是错误：学生可以暂不绑定组织）。"""
        return self._session.scalar(
            select(Organization)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
        )

    def add_member(self, *, organization_id: str, user_id: str, role: str) -> OrganizationMember:
        now = utc_now()
        member = OrganizationMember(
            id=str(uuid4()),
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        self._session.add(member)
        self._session.flush()
        return member

    def get_member_in_organization(
        self, *, organization_id: str, user_id: str
    ) -> OrganizationMember | None:
        return self._session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )

    def set_member_role(self, member: OrganizationMember, role: str) -> OrganizationMember:
        member.role = role
        member.updated_at = utc_now()
        self._session.flush()
        return member

    def list_members(self, organization_id: str) -> list[tuple[OrganizationMember, User]]:
        """成员列表 + 用户基本信息。

        一次 join 取齐，不在循环里再查用户（那是 N+1 的经典形态：组织 200 人 = 201 次查询）。
        排序用 `username`：管理页要的是稳定顺序，`created_at` 在同一次批量邀请里会撞。
        """
        rows = self._session.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == organization_id)
            .order_by(User.username)
        ).all()
        return [(member, user) for member, user in rows]

    def count_members(self, organization_id: str) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.organization_id == organization_id)
            )
            or 0
        )

    # ------------------------------- 事务 --------------------------------

    def commit(self) -> None:
        """提交点由服务层掌握：注册要「用户 + 成员关系 + 审计」同时生效（ADR-021 §2.5）。"""
        self._session.commit()
