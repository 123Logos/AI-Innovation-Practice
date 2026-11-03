"""`refresh_tokens` 的表映射与仓储（Phase 2 · D2.1，方案 A-1）。

令牌只以 SHA-256 哈希落库，因此「查一条令牌」永远按 `token_hash` 查，
不存在「先按用户查、再在应用层逐个比对明文」的写法。

仓储只负责持久化语义，不判断业务是否放行：
「这个 token 能不能换新」的判定（签名、类型、过期、已撤销）全部在
`AuthService.refresh` 里，保持「仓储薄、服务厚」的分层。

与 `UserRepository` 共用同一个 `Base`（`users` 的 declarative base）：
两者在同一事务里操作，且 `refresh_tokens.user_id` 的外键必须让
SQLAlchemy 的 metadata 知道 `users` 的存在（测试里 `create_all` 依赖这点）。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import ForeignKey, String, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.datetime_display import utc_now
from app.repositories.user_repository import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_refresh_tokens_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), nullable=False)


class RefreshTokenRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(
        self,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> RefreshToken:
        """插入一条新的有效令牌记录（登录、刷新各调用一次）。"""
        record = RefreshToken(
            id=str(uuid4()),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked_at=None,
            created_at=now or utc_now(),
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return record

    def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    def rotate(
        self,
        current: RefreshToken,
        token_hash: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> RefreshToken:
        """令牌轮换：旧记录置 `revoked_at`，同时插入新记录。

        两步必须在**同一个 commit** 里：先 commit 撤销、再 commit 插入的话，
        中间若失败，用户手上就只剩一个已作废的 refresh token，
        表现为「刷新一次就掉线」，而且是间歇性的、极难复现。
        """
        issued_at = now or utc_now()
        current.revoked_at = issued_at
        record = RefreshToken(
            id=str(uuid4()),
            user_id=current.user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked_at=None,
            created_at=issued_at,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return record

    def revoke(self, current: RefreshToken, now: datetime | None = None) -> None:
        """登出：把这条令牌置为已撤销（幂等，已撤销的不会被改写时间）。"""
        if current.revoked_at is not None:
            return
        current.revoked_at = now or utc_now()
        self._session.commit()
