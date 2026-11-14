"""教师端学情看板与批量导出（FR-08 / B10a、B10b，`ADR-029` §4）。

守的是三条最容易被「看起来对」蒙过去的规则：

1. **动作闸的位置**：散人必须在读任何业务数据之前被拦下并拿到「请先绑定组织」，
   而不是先被权限闸挡成 403，也不是先查完库再判。既有一条源码级用例盯住语句顺序，
   也有 HTTP 用例盯住响应与 `denied` 审计；
2. **口径是聚合而不是名单**：标签分布按学生去重、匹配度概况是技能覆盖率（不是
   `MatchService` 的 AI 分），响应里不许出现姓名 / 邮箱 / 手机号；
3. **导出真的用同一份纸面**：PDF 的 HTML 里带的是从 `ResumePreview.vue` 原样切出的
   **非 scoped** `@media print` 规则，不是后端另抄一份。渲染器用假实现（CI 不需要
   真浏览器），但断言落在「交给渲染器的 HTML」上——假实现也要能证明样式对。

测试库用 `resume_mvp_b10`，与并行的其它批次不共用（AGENTS.md §5.4.1）。
"""

from __future__ import annotations

import ast
import io
import json
import zipfile
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.pdf_errors import PdfRendererUnavailableError
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import get_engine, session_scope
from app.services import dependencies

API = "/api/v1"
DASHBOARD_URL = f"{API}/teacher/dashboard"
RESUMES_URL = f"{API}/teacher/resumes"
BATCH_URL = f"{API}/teacher/resumes/batch-download"
EXPORT_URL = f"{API}/resumes/{{id}}/export"
JOBS_URL = f"{API}/jobs"
PROFILES_URL = f"{API}/profiles/me"
GENERATE_URL = f"{API}/resumes/generate"
PASSWORD = "Strong-password-1"

SERVICE_SOURCE = Path(__file__).resolve().parents[1] / "app/services/teacher_tools_service.py"
ROUTES_SOURCE = Path(__file__).resolve().parents[1] / "app/api/v1/teacher_tools.py"
DEPENDENCIES_SOURCE = Path(__file__).resolve().parents[1] / "app/services/dependencies.py"
PREVIEW_SOURCE = Path(__file__).resolve().parents[1] / "frontend/src/components/ResumePreview.vue"


# --------------------------------------------------------------------------- #
# 夹具与帮手
# --------------------------------------------------------------------------- #


class _FakeRenderer:
    """假 PDF 渲染器：记录交进来的 HTML，回一段稳定的 PDF 字节。"""

    def __init__(self) -> None:
        self.htmls: list[str] = []
        self.raise_error: Exception | None = None

    def render(self, html: str) -> bytes:
        self.htmls.append(html)
        if self.raise_error is not None:
            raise self.raise_error
        return b"%PDF-1.4\n" + str(len(self.htmls)).encode("ascii")


@pytest.fixture
def renderer(monkeypatch: pytest.MonkeyPatch) -> _FakeRenderer:
    fake = _FakeRenderer()
    monkeypatch.setattr(dependencies, "HeadlessBrowserPdfRenderer", lambda: fake)
    return fake


def _register(client: TestClient, username: str) -> dict[str, str]:
    created = client.post(
        f"{API}/auth/register",
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert created.status_code == 201, created.text
    logged_in = client.post(
        f"{API}/auth/login", json={"identifier": username, "password": PASSWORD}
    )
    assert logged_in.status_code == 200, logged_in.text
    return {"Authorization": f"Bearer {logged_in.json()['data']['access_token']}"}


def _user_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get(f"{API}/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    user_id: str = response.json()["data"]["user"]["id"]
    return user_id


def _organization(code: str) -> str:
    with session_scope() as session:
        repository = OrganizationRepository(session)
        organization = repository.create(code=code, name=f"{code}大学", kind="school")
        repository.commit()
        return organization.id


def _user(
    client: TestClient,
    username: str,
    *,
    organization_id: str | None = None,
    role: str = "student",
) -> tuple[dict[str, str], str]:
    """注册 → 入组织 → 重新登录（角色是登录那一刻从成员表读的，顺序不能倒）。"""
    headers = _register(client, username)
    user_id = _user_id(client, headers)
    if organization_id is not None:
        with session_scope() as session:
            OrganizationRepository(session).add_member(
                organization_id=organization_id, user_id=user_id, role=role
            )
            session.commit()
        headers = _register_login(client, username)
    return headers, user_id


def _register_login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(f"{API}/auth/login", json={"identifier": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _profile(client: TestClient, headers: dict[str, str], *, name: str, skills: list[str]) -> str:
    saved = client.put(
        PROFILES_URL,
        json={
            "full_name": name,
            "contact": "13800000000",
            "skills": [{"name": skill} for skill in skills],
            "experiences": [
                {
                    "title": "校园二手交易平台",
                    "start_date": "2024-03-01",
                    "end_date": "2024-06-30",
                    "description": "面向校内学生的二手交易 Web 应用",
                    "responsibility": "负责接口开发",
                }
            ],
            "educations": [
                {
                    "school": "示例大学",
                    "major": "计算机",
                    "degree": "本科",
                    "edu_start": "2021-09-01",
                }
            ],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    profile_id: str = saved.json()["data"]["profile"]["id"]
    return profile_id


def _resume(client: TestClient, headers: dict[str, str]) -> str:
    generated = client.post(GENERATE_URL, json={"title": "投后端"}, headers=headers)
    assert generated.status_code == 200, generated.text
    resume_id: str = generated.json()["data"]["resume_id"]
    return resume_id


def _add_tags(profile_ids: list[str], rows: list[tuple[str, str, int]]) -> None:
    """`(tag, status, 第几个档案)` → 直接写 `profile_tags`（B9 的表，本批只读）。"""
    from datetime import datetime

    now = datetime(2026, 11, 14, 8, 0, 0)
    with get_engine().begin() as connection:
        for tag, status, index in rows:
            connection.execute(
                text(
                    "INSERT INTO profile_tags "
                    "(id, student_profile_id, tag, status, score, source, evidence_count, "
                    " session_id, created_at, updated_at) VALUES "
                    "(:id, :pid, :tag, :status, :score, 'assessment', :evidence, NULL, :now, :now)"
                ),
                {
                    "id": str(uuid4()),
                    "pid": profile_ids[index],
                    "tag": tag,
                    "status": status,
                    "score": 100 if status == "verified" else 50,
                    "evidence": 3,
                    "now": now,
                },
            )


def _published_job(
    client: TestClient, headers: dict[str, str], organization_id: str, skills: list[str]
) -> str:
    created = client.post(
        JOBS_URL,
        json={
            "company_name": "示例科技",
            "title": "后端实习",
            "description": "服务端接口开发",
            "skills": [{"name": skill} for skill in skills],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    job_id = str(created.json()["data"]["job"]["id"])
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE jobs SET status = 'published', organization_id = :org WHERE id = :id"),
            {"org": organization_id, "id": job_id},
        )
    return job_id


def _audit_rows() -> list[dict[str, object]]:
    with get_engine().connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT action, actor_user_id, organization_id, target_type, target_id, "
                    "result, detail, ip FROM audit_logs ORDER BY id"
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _teacher_rows() -> list[dict[str, object]]:
    """只取 B10 的三个动作：注册与登录也会写 `audit_logs`（B5 既有行为）。"""
    actions = {"dashboard.view", "resume.export_pdf", "resume.batch_download"}
    return [row for row in _audit_rows() if row["action"] in actions]


def _reason(response: object) -> str:
    body = response.json()  # type: ignore[attr-defined]
    return str(body["error"]["details"].get("reason", ""))


# --------------------------------------------------------------------------- #
# 1. 动作层组织闸
# --------------------------------------------------------------------------- #


def test_unbound_user_is_blocked_on_every_entry(client: TestClient) -> None:
    """散人：看板 / 批量下载 / 单份导出三个入口都是 400 + 「请先绑定组织」。"""
    headers, _ = _user(client, f"b10_loose_{uuid4().hex[:8]}")
    resume_id = str(uuid4())

    attempts = (
        client.get(DASHBOARD_URL, headers=headers),
        client.get(RESUMES_URL, headers=headers),
        client.post(BATCH_URL, json={"resume_ids": [resume_id]}, headers=headers),
        client.post(EXPORT_URL.format(id=resume_id), headers=headers),
    )
    for response in attempts:
        assert response.status_code == 400, response.text
        assert "绑定组织" in response.json()["error"]["message"], response.text
        assert _reason(response) == "ORGANIZATION_ACTION_REQUIRED"


def test_denied_attempt_writes_audit_and_reads_no_business_data(client: TestClient) -> None:
    """拒绝发生在读业务表之前：响应没有聚合数字，库里只有一条 denied 审计。"""
    headers, user_id = _user(client, f"b10_deny_{uuid4().hex[:8]}")

    response = client.get(DASHBOARD_URL, headers=headers)

    assert response.status_code == 400, response.text
    assert "student_total" not in response.text
    rows = _teacher_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "dashboard.view"
    assert row["result"] == "denied"
    assert row["organization_id"] is None
    assert row["actor_user_id"] == user_id
    detail = json.loads(str(row["detail"]))
    assert detail["reason"] == "no_organization"
    assert detail["resource_ids"] == []


def test_every_entry_starts_with_the_action_gate() -> None:
    """源码级：service 每个公开方法第一句 = 闸；闸的第一句 = `require_organization`。

    只测 HTTP 行为是不够的：把闸挪到「先查完学生再判」一样能返回 400，
    但那时聚合数据已经被读进内存了。位置本身是契约，所以用 AST 钉住。
    """
    tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"))
    service_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TeacherToolsService"
    )
    methods = {
        node.name: node
        for node in service_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("dashboard", "list_downloadable", "export_pdf", "batch_download"):
        statements = [
            node
            for node in methods[name].body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        first = statements[0]
        assert isinstance(first, ast.Assign), name
        assert isinstance(first.value, ast.Call), name
        assert isinstance(first.value.func, ast.Attribute), name
        assert first.value.func.attr == "_require_organization", name

    # 第一条语句是 docstring（AST 里的 Expr），第二条才是 `try`。
    statements = [
        node
        for node in methods["_require_organization"].body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    guard = statements[0]
    assert isinstance(guard, ast.Try)
    first = guard.body[0]
    assert isinstance(first, ast.Return) and isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "require_organization"

    routes = ast.parse(ROUTES_SOURCE.read_text(encoding="utf-8"))
    handlers = {
        node.name: node
        for node in routes.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in (
        "read_dashboard",
        "list_downloadable_resumes",
        "batch_download",
        "export_resume",
    ):
        statements = [
            n
            for n in handlers[name].body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        first = statements[0]
        assert isinstance(first, ast.Try), name
        call = first.body[0]
        assert isinstance(call, ast.Assign) and isinstance(call.value, ast.Call), name
        assert isinstance(call.value.func, ast.Attribute), name
        assert (
            call.value.func.attr
            == {
                "read_dashboard": "dashboard",
                "list_downloadable_resumes": "list_downloadable",
                "batch_download": "batch_download",
                "export_resume": "export_pdf",
            }[name]
        )

    # 权限闸不能跑在动作闸前面：否则散人拿到的是 403 而不是绑定提示。
    dep = ast.parse(DEPENDENCIES_SOURCE.read_text(encoding="utf-8"))
    factory = next(
        node
        for node in dep.body
        if isinstance(node, ast.FunctionDef) and node.name == "teacher_permission"
    )
    inner = next(
        node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == "guard"
    )
    guard_source = (
        ast.get_source_segment(DEPENDENCIES_SOURCE.read_text(encoding="utf-8"), inner) or ""
    )
    assert guard_source.index("require_organization(") < guard_source.index(
        "permission not in context.permissions"
    )


def test_student_with_organization_is_forbidden_by_permission(client: TestClient) -> None:
    """组织里的学生过了动作闸，但过不了权限闸：403，且没有下载审计。"""
    org_id = _organization(f"B10S{uuid4().hex[:6]}")
    headers, _ = _user(client, f"b10_stu_{uuid4().hex[:8]}", organization_id=org_id, role="student")

    response = client.get(DASHBOARD_URL, headers=headers)

    assert response.status_code == 403, response.text
    assert _teacher_rows() == []


# --------------------------------------------------------------------------- #
# 2. B10a 看板聚合
# --------------------------------------------------------------------------- #


def _school(client: TestClient, prefix: str = "b10") -> tuple[dict[str, str], str]:
    org_id = _organization(f"B10{uuid4().hex[:6]}")
    teacher, _ = _user(
        client, f"{prefix}_teacher_{uuid4().hex[:6]}", organization_id=org_id, role="teacher"
    )
    return teacher, org_id


def test_empty_organization_returns_zero_structure(client: TestClient) -> None:
    """没有学生时给结构完整的零值，不返回 404 / null / 空对象。"""
    headers, _ = _school(client)

    response = client.get(DASHBOARD_URL, headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["student_total"] == 0
    assert data["tagged_student_total"] == 0
    assert data["tag_distribution"] == []
    assert data["match_overview"] == []


def test_tag_distribution_counts_students_not_rows(client: TestClient) -> None:
    """两条 verified 标签 = 2 名学生；响应里不能出现任何个人信息。"""
    headers, org_id = _school(client)
    ids: list[str] = []
    for index in range(2):
        student, _ = _user(
            client, f"b10_tag_s{index}_{uuid4().hex[:6]}", organization_id=org_id, role="student"
        )
        ids.append(_profile(client, student, name=f"学生{index}", skills=["Python"]))
    _add_tags(ids, [("Python", "verified", 0), ("Python", "verified", 1), ("SQL", "pending", 0)])

    response = client.get(DASHBOARD_URL, headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["student_total"] == 2
    assert data["tagged_student_total"] == 2
    distribution = {
        (item["tag"], item["status"]): item["student_count"] for item in data["tag_distribution"]
    }
    assert distribution == {("Python", "verified"): 2, ("SQL", "pending"): 1}
    for forbidden in ("学生0", "13800000000", "@example.com", "content_html"):
        assert forbidden not in response.text


def test_match_overview_is_skill_coverage_not_ai_score(client: TestClient) -> None:
    """岗位要求 Python+SQL，学生只有 Python → 覆盖率 50，落到 50–80 那一档。"""
    headers, org_id = _school(client)
    student, _ = _user(client, f"b10_cov_{uuid4().hex[:8]}", organization_id=org_id, role="student")
    _profile(client, student, name="覆盖率学生", skills=["python"])
    _published_job(client, headers, org_id, ["Python", "SQL"])

    response = client.get(DASHBOARD_URL, headers=headers)

    assert response.status_code == 200, response.text
    overview = response.json()["data"]["match_overview"]
    assert len(overview) == 1
    item = overview[0]
    assert item["required_skill_count"] == 2
    assert item["student_count"] == 1
    assert item["avg_coverage"] == 50
    assert item["no_requirements"] is False
    assert item["buckets"] == {"below_half": 0, "half_to_eighty": 1, "eighty_plus": 0}
    # 没有 AI 分数字段：这一格是纸面覆盖率，不能和 ADR-013 的分数互相解释。
    assert "score" not in item and "match_score" not in item


# --------------------------------------------------------------------------- #
# 3. B10b 批量下载 / 导出 / 审计
# --------------------------------------------------------------------------- #


def test_other_organization_resumes_are_skipped(
    client: TestClient, renderer: _FakeRenderer
) -> None:
    """别的组织的简历不进 ZIP；全都不属于本组织时 400，而不是返回空包。"""
    headers, org_id = _school(client, "b10own")
    other_org = _organization(f"B10O{uuid4().hex[:6]}")
    other_student, _ = _user(
        client, f"b10_other_{uuid4().hex[:8]}", organization_id=other_org, role="student"
    )
    _profile(client, other_student, name="别校学生", skills=["Python"])
    foreign_id = _resume(client, other_student)
    own_student, _ = _user(
        client, f"b10_own_{uuid4().hex[:8]}", organization_id=org_id, role="student"
    )
    _profile(client, own_student, name="本校学生", skills=["Python"])
    own_id = _resume(client, own_student)

    only_foreign = client.post(BATCH_URL, json={"resume_ids": [foreign_id]}, headers=headers)
    assert only_foreign.status_code == 400, only_foreign.text
    assert _reason(only_foreign) == "NO_DOWNLOADABLE_RESUME"

    mixed = client.post(BATCH_URL, json={"resume_ids": [own_id, foreign_id]}, headers=headers)
    assert mixed.status_code == 200, mixed.text
    with zipfile.ZipFile(io.BytesIO(mixed.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["item_count"] == 1
    assert [item["resume_id"] for item in manifest["items"]] == [own_id]
    assert [item["resume_id"] for item in manifest["skipped"]] == [foreign_id]


def test_downloadable_list_only_contains_own_organization(
    client: TestClient, renderer: _FakeRenderer
) -> None:
    """下载清单也是「查看数据」入口：散人 400、教师只看到本组织学生的简历。"""
    loose, _ = _user(client, f"b10_list_loose_{uuid4().hex[:6]}")
    assert client.get(RESUMES_URL, headers=loose).status_code == 400

    headers, org_id = _school(client, "b10list")
    student, _ = _user(
        client, f"b10_list_s_{uuid4().hex[:6]}", organization_id=org_id, role="student"
    )
    _profile(client, student, name="清单学生", skills=["Python"])
    own_id = _resume(client, student)
    other_org = _organization(f"B10L{uuid4().hex[:6]}")
    other, _ = _user(
        client, f"b10_list_o_{uuid4().hex[:6]}", organization_id=other_org, role="student"
    )
    _profile(client, other, name="别校清单", skills=["Python"])
    _resume(client, other)

    response = client.get(RESUMES_URL, headers=headers)

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert [item["resume_id"] for item in items] == [own_id]
    assert items[0]["full_name"] == "清单学生"
    assert "content_html" not in response.text


def test_zip_layout_and_manifest(client: TestClient, renderer: _FakeRenderer) -> None:
    """ZIP 三层结构、sha256 对得上字节、危险字符被从文件名里清掉。"""
    headers, org_id = _school(client, "b10zip")
    ids: list[str] = []
    for index in range(2):
        student, _ = _user(
            client, f"b10_z_s{index}_{uuid4().hex[:6]}", organization_id=org_id, role="student"
        )
        _profile(client, student, name="../张三" if index == 0 else "李四", skills=["Python"])
        ids.append(_resume(client, student))

    response = client.post(BATCH_URL, json={"resume_ids": ids + [ids[0]]}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "README.txt" in names and "manifest.json" in names
        pdfs = [name for name in names if name.startswith("resumes/") and name.endswith(".pdf")]
        assert len(pdfs) == 2  # 重复 id 只出一份
        assert all(".." not in name and "/" not in name[len("resumes/") :] for name in pdfs)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["item_count"] == 2
        for item in manifest["items"]:
            pdf = archive.read(item["file"])
            assert item["sha256"] == sha256(pdf).hexdigest()
            assert item["bytes"] == len(pdf)
        assert "导出时间" in archive.read("README.txt").decode("utf-8")


def test_download_audit_rows(client: TestClient, renderer: _FakeRenderer) -> None:
    """看板 / 单份导出 / 批量下载各一条 `ok`，批量不按条目拆成多条。"""
    headers, org_id = _school(client, "b10audit")
    student, _ = _user(client, f"b10_a_s_{uuid4().hex[:8]}", organization_id=org_id, role="student")
    _profile(client, student, name="审计学生", skills=["Python"])
    resume_id = _resume(client, student)

    assert client.get(DASHBOARD_URL, headers=headers).status_code == 200
    assert client.post(EXPORT_URL.format(id=resume_id), headers=headers).status_code == 200
    assert (
        client.post(BATCH_URL, json={"resume_ids": [resume_id]}, headers=headers).status_code == 200
    )

    rows = _teacher_rows()
    assert [row["action"] for row in rows] == [
        "dashboard.view",
        "resume.export_pdf",
        "resume.batch_download",
    ]
    assert all(row["result"] == "ok" for row in rows)
    assert rows[1]["target_type"] == "resume" and rows[1]["target_id"] == resume_id
    batch_detail = json.loads(str(rows[2]["detail"]))
    assert batch_detail["item_count"] == 1
    assert batch_detail["resource_ids"] == [resume_id]
    assert batch_detail["bytes"] > 0


def test_export_html_uses_global_print_css(client: TestClient, renderer: _FakeRenderer) -> None:
    """交给渲染器的 HTML 带的是 `ResumePreview.vue` 原样切出的非 scoped 打印规则。"""
    headers, org_id = _school(client, "b10css")
    student, _ = _user(
        client, f"b10_css_s_{uuid4().hex[:8]}", organization_id=org_id, role="student"
    )
    _profile(client, student, name="打印学生", skills=["Python"])
    resume_id = _resume(client, student)

    response = client.post(EXPORT_URL.format(id=resume_id), headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert renderer.htmls, "渲染器必须真的被调用过"
    html = renderer.htmls[0]
    assert "@media print" in html
    assert "page-break-inside: avoid" in html
    assert "break-inside: avoid" in html
    assert ".paper .paper-experience" in html
    assert "break-after: avoid" in html
    assert "@page { size: A4; margin: 0; }" in html
    assert '<div class="paper-stage"><div class="paper">' in html
    # 自然分页：不截断、不缩字号、不把纸面钉死成一页。
    assert "overflow: hidden" not in html
    assert "font-size: 8pt" not in html
    # 纸面高度只作为变量 / min-height 出现，不给 `.paper` 钉死固定 height。
    assert ".paper { height:" not in html
    assert "min-height: 0" in html
    # 单一真相：打印块是源文件里那一段，不是后端另抄的一份。
    # 两边都剔注释：`load_paper_css()` 按闸的判据剔掉注释后拼进 HTML，
    # 拿带注释的原文去比只会因为注释而不是规则失败。
    import re

    source = PREVIEW_SOURCE.read_text(encoding="utf-8")
    non_scoped = source.split("<style>\n", 1)[1].split("</style>", 1)[0]
    print_block = non_scoped[non_scoped.index("@media print") :]
    print_block = re.sub(r"/\*[\s\S]*?\*/", "", print_block).strip()
    assert print_block in html


def test_export_hides_overflow_guard(client: TestClient, renderer: _FakeRenderer) -> None:
    """预警线打印时隐藏；页码不写进正文 HTML（那一格归导出侧）。"""
    headers, org_id = _school(client, "b10guard")
    student, _ = _user(client, f"b10_g_s_{uuid4().hex[:8]}", organization_id=org_id, role="student")
    _profile(client, student, name="预警线学生", skills=["Python"])
    resume_id = _resume(client, student)

    response = client.post(EXPORT_URL.format(id=resume_id), headers=headers)

    assert response.status_code == 200, response.text
    html = renderer.htmls[0]
    assert ".paper-page-guard { display: none; }" in html
    assert "min-height: 0" in html
    assert "第 1 页" not in html
    assert "建议单页" not in html


def test_renderer_unavailable_is_not_a_fake_pdf(
    client: TestClient, renderer: _FakeRenderer
) -> None:
    """浏览器挂了 → 503 + 一句能走的路；绝不返回 HTML 或空 PDF，且留 denied 审计。"""
    headers, org_id = _school(client, "b10down")
    student, _ = _user(client, f"b10_d_s_{uuid4().hex[:8]}", organization_id=org_id, role="student")
    _profile(client, student, name="降级学生", skills=["Python"])
    resume_id = _resume(client, student)
    renderer.raise_error = PdfRendererUnavailableError("no browser")

    single = client.post(EXPORT_URL.format(id=resume_id), headers=headers)
    assert single.status_code == 503, single.text
    assert _reason(single) == "PDF_RENDERER_UNAVAILABLE"

    batch = client.post(BATCH_URL, json={"resume_ids": [resume_id]}, headers=headers)
    assert batch.status_code == 503, batch.text
    denied = [row for row in _teacher_rows() if row["result"] == "denied"]
    assert len(denied) == 2
    assert all(json.loads(str(row["detail"]))["reason"] == "renderer_unavailable" for row in denied)
