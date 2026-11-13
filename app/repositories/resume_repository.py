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

#: 两种来源（`ADR-026` §2.1）：由模板生成，或按岗位定制。
#: 取值即数据库 CHECK 约束 `ck_resumes_origin` 的字面量，改一处就要改迁移。
ORIGIN_GENERATED = "generated"
ORIGIN_CUSTOMIZED = "customized"


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
    #: 这一行怎么来的（`ADR-026` §2.1，迁移 `20261113_0024`）：`generated` / `customized`。
    #: **不许靠标题前缀或 HTML 内容推断**——那是会在下一次改文案时静默失效的隐式契约。
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default=ORIGIN_GENERATED)
    #: 定制版面向哪条岗位。可空；岗位被删时由 `ON DELETE SET NULL` 置空（`ADR-026` §2.7）。
    #: 岗位**标题**不进这一列也不 join：它已经写进 `title`（快照语义，见 §2.1）。
    target_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


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
        version_no = self._allocate_version_no(student_profile_id)
        resume = Resume(
            id=str(uuid4()),
            student_profile_id=student_profile_id,
            title=title,
            content_html=content_html,
            version_no=version_no,
            current_version_id=None,
            origin=ORIGIN_GENERATED,
            target_job_id=None,
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

    # ------------------------------------------------------------------ B8 剩余
    # 版本对比与按岗位定制（`ADR-026`）。三件事都**只读**或**只插一行**，
    # 一个共同的硬约束：任何一条路径都不许改写 `current_version_id` 的指向，
    # 除了 `create_resume`（生成）——它是唯一被允许移动链头的动作（§2.2）。

    def list_versions(self, student_profile_id: str) -> list[Resume]:
        """该档案的全部版本，按 `version_no` **倒序**（最近一次产出在最前）。

        排序键只有 `version_no`：它是档案计数器的流水号，天然唯一（`create_resume`
        与 `create_customized_resume` 都从同一个计数器取号）。再叠 `created_at`
        只会让「同一秒落两行」时顺序随数据库心情变——那不是可复现的读路径。
        """
        statement = (
            select(Resume)
            .where(Resume.student_profile_id == student_profile_id)
            .order_by(Resume.version_no.desc())
        )
        return list(self._session.scalars(statement))

    def get_version(self, student_profile_id: str, resume_id: str) -> Resume | None:
        """按「档案 + 版本 id」取一行。

        `student_profile_id` 是**必须**的入参而不是可选过滤：只按 id 取一行，
        就等于把「这一版是不是我的」这件事留给调用方去判，而漏判一次就是
        读到别人的简历原文。两个条件写进同一条 SQL，越权在数据层就不可能发生。
        """
        statement = select(Resume).where(
            Resume.student_profile_id == student_profile_id,
            Resume.id == resume_id,
        )
        return self._session.scalar(statement)

    def get_version_for_user(self, resume_id: str, user_id: str) -> Resume | None:
        """按「版本 id + 登录用户」取一行（`ADR-026` §2.6 的差异端点用）。

        为什么不是「先按 id 取、再让服务层比对 `student_profile_id`」：
        那样一次漏判就是读到别人的简历原文，而漏判的代价与收益完全不对称。
        归属条件写进 SQL，越权在数据层就不可能发生——与 `get_version()` 同一理由，
        只是这里的归属键是 `student_profiles.user_id`（调用方手上只有用户）。
        """
        statement = (
            select(Resume)
            .join(StudentProfile, StudentProfile.id == Resume.student_profile_id)
            .where(Resume.id == resume_id, StudentProfile.user_id == user_id)
        )
        return self._session.scalar(statement)

    def create_customized_resume(
        self,
        student_profile_id: str,
        *,
        title: str,
        content_html: str,
        target_job_id: str,
        head_id: str,
    ) -> Resume:
        """插一行**定制版**：进入历史，但**不移动链头**（`ADR-026` §2.2）。

        与 `create_resume` 的差别只有一格：新行的 `current_version_id` 直接写成
        **当前链头的 id**，于是 `is_head = IF(id = current_version_id, 1, NULL)` 是 `NULL`，
        唯一键 `uq_resumes_profile_head` 不会撞，`GET /resumes/current` 仍然返回原链头。
        其余所有行一行都不动。

        `version_no` 仍从档案计数器取（`ADR-026` §2.3）：让两行不同内容共用一个号，
        「v3 vs v3」这种对比请求就失去意义；不占号则历史列表会出现无编号的行。
        代价是**链头号可能不连续**（生成 v1、定制 v2、再生成 v3）。

        `head_id` 由服务层从 `get_head()` 拿到后传下来，本方法**不再查一次**：
        查两次等于给「查完到插入之间链头被别人换掉」留一个窗口，而那个窗口里的
        插入会让新行的指针指向一条已经不再是链头的行。
        """
        now = utc_now()
        version_no = self._allocate_version_no(student_profile_id)
        resume = Resume(
            id=str(uuid4()),
            student_profile_id=student_profile_id,
            title=title,
            content_html=content_html,
            version_no=version_no,
            current_version_id=head_id,
            origin=ORIGIN_CUSTOMIZED,
            target_job_id=target_job_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(resume)
        self._session.commit()
        self._session.refresh(resume)
        return resume

    def _allocate_version_no(self, student_profile_id: str) -> int:
        """从档案计数器取下一个版本号（两处写入的唯一取号点）。

        放在文件末尾而不是 `create_resume` 上方，有两个原因：文档里有指向
        `create_resume` 的引用（`ADR-015` §2.3 / §6），插行会把指针推到别的代码上；
        而「取号必须 `FOR UPDATE`」这条纪律只能有一份实现——复制一份到定制路径，
        迟早在某一份里漏掉行锁，表现为并发下偶发 1062。
        """
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
        return version_no
