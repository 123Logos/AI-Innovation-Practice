"""`resumes` 表的 SQLAlchemy 映射与仓储（真实 MySQL）。

与 `student_profile_repository` 共用 `ProfileBase` registry，避免同一张库出现多套映射。

**B8（ADR-015 §2.3）起这张表有读路径了**：此前它只写不读（全仓 `select(Resume)` 零命中），
「最新」全靠 `created_at` 猜。现在版本指针是显式外键：

- `version_no`：档案内递增的版本号；
- `current_version_id`：FK → `resumes.id`，`ON DELETE SET NULL`——**全仓唯一非 RESTRICT
  的外键**，偏离是刻意的：链头行本身可能被删，RESTRICT 会变成「有指针就删不掉行」的死锁；
- `is_head`：STORED 生成列 `IF(id = current_version_id, 1, NULL)`，配
  `UNIQUE(student_profile_id, is_head)` 保证「每个档案至多一条链头」（MySQL 唯一索引
  允许多个 NULL）。

语义写死：一个档案的所有简历行 `current_version_id` 都指向同一条，且该行自指即链头。
**「取 `created_at` 最大的一条」这个写法从此不许出现在任何地方**——指针落空时
读侧必须显式报错（服务层 `ResumePointerBrokenError`），不许回落到「挑一条看起来最新的」。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Computed, DateTime, Integer, String, Text, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.datetime_display import utc_now
from app.repositories.student_profile_repository import ProfileBase, StudentProfile


class Resume(ProfileBase):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # DDL 为 LONGTEXT；映射用 Text，只为读写绑定，不参与 DDL 生成。
    content_html: Mapped[str] = mapped_column(Text, nullable=False)
    #: 档案内版本号，从 1 递增（ADR-015 §2.3）。
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 链头指针：全档案所有行都指向同一条；链头行自指。NULL = 指针落空
    #: （链头被物理删除时由 ON DELETE SET NULL 置空），读侧必须显式报错。
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: 生成列：自指即链头。由数据库计算，不参与 INSERT/UPDATE。
    #: **VIRTUAL 而非 ADR-015 字面的 STORED**（实测 MySQL 8.0.43）：STORED 生成列
    #: 的基列不允许挂 ON DELETE SET NULL 外键（errno 1215），而 SET NULL 是 ADR 的
    #: 硬契约；虚拟列上的唯一索引同样被 InnoDB 强制，链头语义逐字不变。
    #: 理由与实测记录见迁移 `20261110_0016` 的 docstring。
    is_head: Mapped[int | None] = mapped_column(
        Integer, Computed("IF(id = current_version_id, 1, NULL)", persisted=False), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResumeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_resume(
        self,
        student_profile_id: str,
        title: str,
        content_html: str,
    ) -> Resume:
        """插新行 + 旧链头指针指向新行 + 新行自指，**同一事务**（ADR-015 §2.3）。

        三步的顺序是被两条约束逼出来的，不能颠倒：

        1. 新行先以 `current_version_id=NULL` 插入——此时它的 `is_head` 是 NULL，
           不会与旧链头撞 `UNIQUE(student_profile_id, is_head)`；
        2. 把该档案**其余所有行**的指针指向新行（旧链头的 `is_head` 由 1 变 NULL）——
           新行已存在，外键满足；这一步没有任何行变成链头，也不会撞唯一键；
        3. 新行自指，成为唯一链头。

        若先自指再改旧行，第 1 步就会双链头撞唯一键；若先改旧行再插新行，
        外键会指向一条不存在的行。

        `version_no` **取自档案上的计数器 `student_profiles.resume_version_seq`**，
        不在 `resumes` 表里现算 `max(version_no)+1`（ADR-015 §6）。两条理由都是实测过的：

        - 链头被物理删除后，`max()` 会退回到剩下那条的号——同一个版本号被发两次，
          而用户手上可能还留着第一份（「删除后的下一版」与历史版同号，对比无从谈起）；
        - 两个请求并发进来时，`max()+1` 会同时算出同一个数，随后一个撞唯一键变成 500。

        所以先 `SELECT … FOR UPDATE` 锁住档案行，再在这个事务里把计数器 +1 并用它当版本号。
        计数器只由这里递增，**不因为删除回退**；它也不是「当前版」指针，读当前版仍只认
        `current_version_id`。
        """
        now = utc_now()
        allocated = self._session.execute(
            select(StudentProfile.resume_version_seq)
            .where(StudentProfile.id == student_profile_id)
            .with_for_update()
        ).scalar_one_or_none()
        version_no = (allocated or 0) + 1
        self._session.execute(
            update(StudentProfile)
            .where(StudentProfile.id == student_profile_id)
            .values(resume_version_seq=version_no)
        )
        resume = Resume(
            id=str(uuid4()),
            student_profile_id=student_profile_id,
            title=title,
            content_html=content_html,
            version_no=version_no,
            current_version_id=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(resume)
        self._session.flush()
        self._session.execute(
            update(Resume)
            .where(
                Resume.student_profile_id == student_profile_id,
                Resume.id != resume.id,
            )
            .values(current_version_id=resume.id)
        )
        self._session.execute(
            update(Resume).where(Resume.id == resume.id).values(current_version_id=resume.id)
        )
        self._session.commit()
        self._session.refresh(resume)
        return resume

    def get_head(self, student_profile_id: str) -> Resume | None:
        """「当前简历」**只准读指针**：取链头行（`is_head = 1`），没有则 None。

        这里刻意不按 `created_at` 排序挑「看起来最新的一条」——那是 ADR-015 §2.3
        明令禁止的隐性契约。指针落空（有行但无链头）与从未生成（一行都没有）
        是两种状态，由服务层用 `count_by_profile` 区分后分别报错。
        """
        statement = select(Resume).where(
            Resume.student_profile_id == student_profile_id,
            Resume.is_head == 1,
        )
        return self._session.scalar(statement)

    def count_by_profile(self, student_profile_id: str) -> int:
        """该档案的简历行数（含指针落空的历史行），只用来区分「没生成过」与「链头丢了」。"""
        statement = (
            select(func.count())
            .select_from(Resume)
            .where(Resume.student_profile_id == student_profile_id)
        )
        return int(self._session.scalar(statement) or 0)
