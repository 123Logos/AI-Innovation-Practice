"""用户表的 SQLAlchemy 映射与仓储实现（真实 MySQL）。

表结构已在 `alembic/versions/20261023_0001_initial_phase1.py` 中建立，
本模块只做映射，不新增表、不新增迁移。
连接串来自 `settings.database_url`，引擎懒加载，便于测试注入 `TEST_DATABASE_URL`。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core.config import get_settings
from app.core.datetime_display import utc_now


class Base(DeclarativeBase):
    """T7.1 只映射 `users`；其他表仍由 Alembic 迁移维护。"""


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    #: 登录标识之一的手机号（`ADR-025` §2.1 / §2.6，迁移 `20261113_0023`）。
    #: 可空：存量账号都没有，登录标识的**第一个**仍然是用户名。
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """返回进程级单例引擎；未配置 `database_url` 时直接失败，不静默降级。"""
    global _engine
    if _engine is None:
        database_url = get_settings().database_url
        if not database_url:
            raise RuntimeError("database_url is required to access user storage")
        _engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
    return _engine


def dispose_engine() -> None:
    """释放连接池并清空单例，供测试切换数据库使用。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """请求级会话；异常回滚，正常退出只关闭，提交由仓储方法显式执行。"""
    session = _get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_user(
        self,
        username: str,
        password_hash: str,
        email: str | None = None,
        phone: str | None = None,
        *,
        commit: bool = True,
    ) -> User:
        """建用户。

        `commit=False` 供注册那一笔用（`ADR-021` §2.5）：注册要「用户 + 成员关系 + 审计」
        三件事同生共死，提交点必须由服务层掌握。默认 `True` 保持其它调用方的老行为。
        """
        now = utc_now()
        user = User(
            id=str(uuid4()),
            username=username,
            email=email,
            phone=phone,
            password_hash=password_hash,
            created_at=now,
            updated_at=now,
        )
        self._session.add(user)
        if commit:
            self._session.commit()
            self._session.refresh(user)
        else:
            self._session.flush()
        return user

    def commit(self) -> None:
        """「用户 + 审计（+ 令牌撤销）」同生共死的提交点（`ADR-021` §2.5 / `ADR-025` §2.4）。

        单独给一个方法而不是让服务层去摸 `self._session`：本仓的分层是
        「仓储掌握提交点，服务层掌握业务顺序」，服务层拿不到 session 才写不出
        「先提交一半」的代码。
        """
        self._session.commit()

    def get_by_username(self, username: str) -> User | None:
        return self._session.scalar(select(User).where(User.username == username))

    def get_by_email(self, email: str) -> User | None:
        """按登录邮箱查一行（`ADR-025` §2.3 的第二优先级）。

        `email` 为 `NULL` 的存量行不会命中任何入参：SQL 的 `NULL = 'x'` 是 `UNKNOWN`，
        这正是要的——「没填邮箱」不该被当成「邮箱是空串」。
        """
        return self._session.scalar(select(User).where(User.email == email))

    def get_by_phone(self, phone: str) -> User | None:
        """按登录手机号查一行（`ADR-025` §2.3 的第三优先级）。"""
        return self._session.scalar(select(User).where(User.phone == phone))

    def get_by_id(self, user_id: str) -> User | None:
        return self._session.get(User, user_id)

    def update_password(self, user: User, password_hash: str) -> None:
        """改密：只写哈希与 `updated_at`，**不提交**。

        提交点由服务层掌握：改密要「换哈希 + 撤销全部 refresh token + 写审计」
        三件事同生共死（`ADR-025` §2.4）。这里若自己 commit，
        中途失败就会留下「密码换了、别处会话还活着」的半截状态。
        """
        user.password_hash = password_hash
        user.updated_at = utc_now()
        self._session.flush()
