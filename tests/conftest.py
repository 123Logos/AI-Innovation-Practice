"""测试全局前置：数据库连接、建表与隔离清理的**唯一入口**。

## 这个文件负责什么

1. **在导入 `app.*` 之前**把 `DATABASE_URL` 指向测试库（`Settings` 会缓存配置，
   晚一步设置就会连到开发库）；
2. 会话级把测试库 `alembic upgrade head`（与生产同一条链路，不用 `create_all`）；
3. 每个用例前后把业务表清空，用例之间互相看不见对方的数据。

各测试文件不再自己建表、删表、`DELETE`，也不再重复那三行环境变量注入。

## 为什么不用「事务回滚隔离」

「整个用例包在一个外层事务里、结束 rollback」是最干净的隔离方式，但在这个项目上
不成立：

- 大量用例会**另开连接**直接查库（`SELECT password_hash …`、`SELECT layout_config …`），
  而外层事务里的写入对别的连接不可见，这些断言会全部失效——等于把「真的落库了」
  这条最重要的验证降级成「当前 session 里有」；
- `test_migrations.py` 的断言对象就是 DDL，而 MySQL 的 DDL 会隐式提交，
  包在事务里也回滚不掉；
- 应用侧用 session 工厂 + `TestClient` 的独立线程，多会话共享一条连接会互相阻塞。

所以这里走的是任务允许的第二条路：**统一由 fixture 建表 + 清理**。

## 为什么不再 `drop_all` / `downgrade base` 清理

`Base.metadata.drop_all()` 只认识 `Base.metadata` 里的表，DROP 顺序也只按它知道的
外键推导。测试库里一旦存在它不认识、却外键指向 `users` / `student_profiles` 的表
（历史脚本建的、`create_all` 时代留下的），MySQL 会直接给 **errno 3730**
（Cannot drop table referenced by a foreign key constraint），而且是「跑到一半炸」，
表现为「必须手动清库才能再跑」。

现在：建表只 `upgrade head`（幂等），清理只 DELETE **本项目的 13 张表**
（Phase 4 · B1 起含 `jobs` / `job_skills`，B1 别名层起含 `skill_aliases`，
B4 / ADR-015 起含 `profile_certificates` / `portfolio_links` / `profile_city_preferences`），
多余的表既不碰也不依赖，3730 从根上消失。真正的迁移可逆性由
`test_migrations.py` 自己验证（那是被测对象，不是清理手段）——它也只被允许
在**自己用例内部**降级，并在 `finally` 里把库交回 head。
"""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest

if TYPE_CHECKING:  # 只用于夹具的返回标注：真导入发生在 `task_runtime` 夹具内部
    from tests.task_support import FakeRuntimeProvider

# --------------------------------------------------------------------------- #
# 1. 环境准备：必须早于任何 `app.*` 导入
# --------------------------------------------------------------------------- #

#: 关闭登录/注册限流：T7.7 给 `/auth/register`、`/auth/login` 加了 5 次/分钟的
#: IP 限流，而认证用例会在同一分钟内反复注册+登录（TestClient 的 client.host 恒为
#: `testclient`，所有请求共用同一个限流键），跑不了几条就会自己撞上 429，
#: 与「验证鉴权逻辑」这件事无关。限流本身不是不测：`tests/test_rate_limit.py`
#: 会在用例内临时打开限流，断言第 6 次请求返回 429 且响应体符合 T6 统一错误模型。
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

#: 第 30 轮（`ADR-033`）：任务端点的默认 Redis 地址。
#:
#: **本机没有 Redis**（2026-11-16 实测 `127.0.0.1:6379` 与 `63790` 均拒绝连接），
#: 所以这条地址不会真的被连上——`task_runtime` 夹具会把「取运行时」整个换掉
#: （`tests/task_support.py` 的 fakeredis 实现）。
#: 之所以还是要给它一个值：`app.worker` 在 import 时就构造 `RedisSettings`
#: （ARQ 的 CLI 只读 `__dict__`，见 `app/worker.py` 的模块 docstring），
#: 空值会让「只是 import 一下」变成启动失败。真正的「没配 Redis」场景
#: 由 `tests/test_tasks.py::test_missing_redis_url_is_503` 显式构造。
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")
#: 任务产物与上传暂存放进临时目录，别在仓库里留下 `temp/` 与 `downloads/`。
#: 用例结束由 fixture 删掉整个目录（见 `test_task_files_are_removed`）。
os.environ.setdefault("WORKER_IMPORT_DIR", "temp/test-imports")
os.environ.setdefault("WORKER_DOWNLOAD_DIR", "downloads/test-outputs")


def _read_value(key: str) -> str | None:
    """取配置项：环境变量优先，其次读仓库根目录的 `.env`。

    回落 `.env` 是为了让本地 `uv run pytest` 不必先手动 `export`；
    CI 显式注入环境变量，走的是同一条分支。
    """
    value = os.environ.get(key)
    if value:
        return value

    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.is_file():
        return None

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw_value = line.partition("=")
        if name.strip() != key:
            continue
        return raw_value.split("#", 1)[0].strip().strip("'\"") or None
    return None


def _database_name(url: str) -> str:
    return urlsplit(url.split("?")[0]).path.lstrip("/")


TEST_DATABASE_URL = _read_value("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    # 护栏：用例会清空整库的业务表，指错库就是「跑个测试把开发数据删了」。
    # 只比较库名（不比较账号/端口），指向同一张库就直接拒绝启动。
    _development_url = _read_value("DATABASE_URL")
    if _development_url and _database_name(_development_url) == _database_name(TEST_DATABASE_URL):
        raise RuntimeError(
            "TEST_DATABASE_URL 与 DATABASE_URL 指向同一个库"
            f"（{_database_name(TEST_DATABASE_URL)}）：集成测试会清空该库的业务表，"
            "请在 .env 里为测试单独建一个库。"
        )

    # 必须是**覆盖**而不是 setdefault：开发者 shell 里若导出了指向开发库的
    # DATABASE_URL，setdefault 会让整套集成测试写进开发库。
    os.environ["TEST_DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# 以下导入必须发生在上面的环境变量就位之后：`Settings` 是缓存单例，
# 早导入一步就会把 `.env` 里的开发库连接串固化进应用。
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.main import app
from app.repositories.user_repository import dispose_engine, get_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 本项目迁移建出来的业务表，**按子表 → 父表排列**：外键是 RESTRICT，
#: 顺序错了会 1451（Cannot delete or update a parent row）。
#: 新增带外键的表时把它插到引用方前面；漏一张就会在下一个用例里撞上残留。
#: `skill_aliases`（B1 / `ADR-016`）没有外键、不属于任何用户，放在最前面只是排版；
#: 但它**必须**在这一栏里：别名行会改变判定结果，残留一行就等于「后面所有用例都跑在
#: 一张非空词表上」，而 §5.1 那条「空表 = 纯词面行为」的验收会静默失去意义。
APP_TABLES = (
    # B9（ADR-028）：测评会话与答案、画像标签。`assessment_answers` 外键指向会话与题库、
    # `profile_tags` 外键指向档案与会话，两者都必须排在父表之前清；
    # 顺序错了会 1451（Cannot delete or update a parent row）。
    #
    # **`assessment_questions` 故意不在这张清单里**：它是**参考数据**（题库），
    # 由迁移 `0026` 的种子建出来，不是任何用户写进去的。放进来就会被每用例的清理
    # 一并 DELETE 掉，于是第一个用例之后整套测评测试都跑在一张空题库上（表现为
    # 「题库为空」400，而红的地方与被测行为无关）。用例若要改题库（停用一道题之类），
    # 必须自己还原——它和 `skill_aliases` 不同：别名是空表才有意义，题库是**满表**才有意义。
    "assessment_answers",
    "profile_tags",
    "assessment_sessions",
    # FR-07 / B11a：面试两张表。`interview_turns` 外键指向 `interview_sessions`（子表在前），
    # `interview_sessions` 外键指向 `jobs` / `users`，因此两张都插在最前，保证 DELETE 顺序
    # 是子表 → 父表（见上方删除顺序的说明）。
    "interview_turns",
    "interview_sessions",
    # B6（ADR-023）：邀请表外键指向 organizations / users，必须排在两者之前清。
    "organization_invitations",
    "skill_aliases",
    "job_skills",
    "jobs",
    "refresh_tokens",
    "resumes",
    # B5（ADR-021）：审计表与成员表都外键指向 users / organizations，
    # 清理顺序必须排在它们前面；`organizations.code` 有唯一键，漏清会撞 1062。
    "audit_logs",
    "organization_members",
    "organizations",
    # B4（ADR-015）：三张多值子表，外键 RESTRICT 指向 student_profiles，
    # 必须排在它前面清；软删除的历史行也一并 DELETE（清理不区分 deleted_at）。
    "profile_certificates",
    "portfolio_links",
    "profile_city_preferences",
    "educations",
    "skills",
    "experiences",
    "student_profiles",
    "users",
)


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _clear_app_tables() -> None:
    """清空业务表；表不存在时（正在被迁移用例重建）安静跳过。

    为什么是 DELETE 而不是 TRUNCATE：InnoDB 的 TRUNCATE 要丢弃并重建表空间，
    实测这一套 7 张表跑一轮 **454ms**（DELETE 6ms），每用例前后各一次就是
    1.5s 纯开销；TRUNCATE 换来的「不用管删除顺序」并不划算，顺序写在
    `APP_TABLES` 里、错了会立刻抛 1451，不会静默。
    """
    with get_engine().begin() as connection:
        existing = set(
            connection.execute(
                text(
                    "SELECT TABLE_NAME FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE()"
                )
            ).scalars()
        )
        for table in APP_TABLES:
            if table in existing:
                connection.execute(text(f"DELETE FROM {table}"))


# --------------------------------------------------------------------------- #
# 2. 夹具
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """测试库连接串；缺失时给出可执行的修复提示，而不是悄悄连开发库。"""
    if not TEST_DATABASE_URL:
        raise RuntimeError(
            "TEST_DATABASE_URL 未配置：请在 .env 或环境变量里指向**独立的测试库**"
            "（集成测试会清空该库的业务表，绝不能指向开发库）。"
        )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """会话级建表：只 `upgrade head`，不 downgrade、不 drop。

    幂等：库已是最新版本时 Alembic 什么都不做，因此「连续跑两次」「库停在 base」
    都不需要人工干预。
    """
    if not TEST_DATABASE_URL:
        # 没有测试库时不在这里炸：错误模型、健康检查、配置校验这些纯逻辑用例
        # 本来就不需要数据库。需要数据库的用例由 `client` / `test_database_url`
        # 夹具给出明确报错。
        yield
        return

    command.upgrade(_alembic_config(), "head")
    try:
        yield
    finally:
        dispose_engine()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """每个用例前后清空业务表（autouse，先于 `client` 建好）。"""
    if not TEST_DATABASE_URL:
        yield
        return

    _clear_app_tables()
    try:
        yield
    finally:
        _clear_app_tables()


@pytest.fixture
def client() -> TestClient:
    """真实 HTTP 客户端；不进入上下文管理器（与原用例一致，不额外触发 lifespan）。"""
    if not TEST_DATABASE_URL:
        raise RuntimeError("集成测试需要 TEST_DATABASE_URL（指向独立测试库）。")
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 3. 异步任务（第 30 轮，`ADR-033`）
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def task_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator["FakeRuntimeProvider"]:
    """把「取任务运行时」换成 fakeredis 实现（autouse，理由见 `tests/task_support.py`）。

    autouse 而不是逐用例显式请求：**任何**一条用例只要碰到 `/resumes/import-pdf` 或
    `/teacher/resumes/batch-download`，就该拿到一套干净的队列与记录；
    漏一条就会让那条用例去连本机并不存在的 Redis，报成「后台任务服务不可用」——
    红的地方与它想测的东西毫无关系。
    """
    from tests.task_support import FakeRuntimeProvider, reset_server

    reset_server()
    provider = FakeRuntimeProvider()
    # 三个 import 点各有一份引用：只打一个补丁，「另一条路径」会绕过它去连真 Redis。
    monkeypatch.setattr("app.services.dependencies.get_task_runtime", provider)
    monkeypatch.setattr("app.worker.get_task_runtime", provider)
    # 第 31 轮（`ADR-034`）：内存降级的单例也是一份跨用例的全局状态
    # （记录、在途任务）。默认用例一条都不会进那个分支，但**有两条用例会**
    # （`test_missing_redis_url_in_development_returns_202` 与
    # `test_inline_runtime.py`），漏了复位就会串味到后面的用例。
    from app.services.inline_runtime import reset_inline_task_runtime

    asyncio.run(reset_inline_task_runtime())
    try:
        yield provider
    finally:
        asyncio.run(provider.close())
        asyncio.run(reset_inline_task_runtime())


@pytest.fixture(autouse=True)
def _task_dirs() -> Iterator[None]:
    """任务的两个目录在每个用例前后清空（`temp/test-imports`、`downloads/test-outputs`）。"""
    import shutil

    from app.core import task_files

    def clean() -> None:
        for directory in (task_files.import_dir(), task_files.download_dir()):
            shutil.rmtree(directory, ignore_errors=True)

    clean()
    try:
        yield
    finally:
        clean()
