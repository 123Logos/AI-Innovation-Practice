"""外部 PDF 简历导入（第 28 轮，`ADR-031`）的端点级用例。

**这一族用例测的是整条链，不是三个部件各演一遍**：真 `PypdfTextExtractor`
（吃测试自己拼的最小 PDF 字节）、真 `DeepSeekResumeStructurer`（只把出网客户端换成
`httpx2.MockTransport`）、真归一化函数，端点用 `multipart/form-data` 收。
把适配器整体换成假对象会让「pypdf 抽出来的文本到底进了哪条消息」「模型给的
`2021年9月` 能不能变成库里那一列 `date`」这两件真正会坏的事测不到。

覆盖面按 `ADR-031` §5 的验收判据逐条对：

1. 2.1 零写入 —— 挂在 **Engine** 上收集语句（`test_jobs_extract_skills.py` 的同款取证，
   挂 Session 会抛 `InvalidRequestError`），外加「导入前后业务表行数不变」。
2. 2.5 一个 422 五个码 —— `PDF_TOO_LARGE` / `PDF_INVALID` / `AI_KEY_MISSING` /
   `AI_SERVICE_UNAVAILABLE` / `AI_OUTPUT_INVALID` 各至少一条，且都带统一错误模型的
   `code` 与 `request_id`。
3. 2.3 三封顶 —— 体积、页数（`pages_total` 与 `pages_used` 分开报）、截断标记。
4. 2.4 归一与 `dropped` —— 技能超 8 条、`projects` 并到 `experiences` 之后、
   六种日期形状、学历同义与不可折、手机号 `+86` 与非法值两种走向。
5. 回执即可保存 —— 把响应里的 `profile` 原样 `PUT /profiles/me` 必须 200：
   这是「AI 产物不该让用户点保存时才吃 422」这条口径唯一的证明方式。

Key 相关：`API_KEY` 只是本地假串，本机没有真实凭据；它必须出现在**外呼请求**里
（BYOK 的定义），且绝不出现在响应体里。

拦截点仍是自己构造的 `httpx2.MockTransport`：`openai` 3.x 走 `httpx2`，
而 pytest-httpx / respx 只给 `httpx` 打补丁（实测那种「Mock」会把请求真的发出去）。
两个夹具都是 **autouse** 的，所以本模块没有任何一条用例可能真出网（AGENTS.md §4.4）。

**v2（`ADR-031` §2.9）加的是三级降级链路**，所以再加一个 autouse 的假 OCR 引擎：

- 真 `RapidOCR` 一次推理约 1.8 秒/页，全套跑几十条用例不该都付这笔时间；
- 但「假件说它认出了字」不等于「真引擎认得出字」，所以
  `test_real_rapidocr_reads_text_from_image_only_pdf` 用**真引擎**跑一张真嵌图的 PDF，
  并把实测到的识别噪声（`SCANME 2024` → `SCAME024`）写进它的 docstring；
- 假引擎默认**什么都认不出**：这样 v1 那几条「读不出文字 → 422」不会因为多了
  第二级而悄悄变成 200——那种「旧用例集体翻绿」正是加兜底层时最容易漏的账。
"""

from __future__ import annotations

import io
import json
import re
import threading
import uuid
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.core.rate_limit import IMPORT_RATE_LIMIT
from app.repositories.user_repository import get_engine
from app.services.resume_import import MAX_PDF_BYTES

IMPORT_URL = "/api/v1/resumes/import-pdf"
PROFILE_ME_URL = "/api/v1/profiles/me"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
API_KEY = "sk-deepseek-import-test-0123456789"

#: DML 的判据只认这三个动词开头（与提取端点同一把尺）。用词边界而不是 `in`，
#: 免得一条 `SELECT ... updated_at` 因为子串里含 "update" 被误判成写。
WRITE_PATTERN = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

#: 一份「全部合格」的模型输出：字段齐、日期都能解析、没有一条会被归一化丢掉。
#: 用它当基线，其他用例只改**一个**变量，断言才不会互相牵连。
CLEAN_OUTPUT: dict[str, Any] = {
    "full_name": "张三",
    "contact": "13800138000",
    "target_role": "算法工程师",
    "skills": [{"name": "Python"}, {"name": "PyTorch"}, {"name": "SQL"}],
    "experiences": [
        {
            "title": "某公司 算法实习",
            "start_date": "2024-06",
            "end_date": "2024-09",
            "description": "参与推荐模型离线评估。",
            "responsibility": "负责特征工程与指标复盘。",
        }
    ],
    "projects": [
        {
            "title": "校园问答系统",
            "start_date": "2023-09",
            "end_date": None,
            "description": "基于 RAG 的校园问答。",
            "responsibility": "召回与重排。",
        }
    ],
    "educations": [
        {
            "school": "中国计量大学",
            "major": "人工智能",
            "degree": "本科",
            "edu_start": "2021-09",
            "edu_end": "2025-06",
        }
    ],
}


class FakeDeepSeek:
    """可编程的 DeepSeek 上游：吐结构化 JSON / 吐状态码 / 抛超时与断连，并记录真实请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.mode = "json"
        self.status = 500
        self.finish_reason: str | None = "stop"
        self.payload: dict[str, Any] = dict(CLEAN_OUTPUT)
        #: 直接控制发回来的原始字符串：测「模型不守格式」时走这条，绕开 payload。
        self.raw_content: str | None = None

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content.decode("utf-8")) for request in self.requests]

    @property
    def system_prompt(self) -> str:
        """最近一次外呼里的 System Prompt 全文——三个上限数字有没有发出去就看它。"""
        for message in self.bodies[-1]["messages"]:
            if message.get("role") == "system":
                return str(message["content"])
        raise AssertionError("请求体里没有 system 消息")

    @property
    def user_message(self) -> str:
        return str(self.bodies[-1]["messages"][-1]["content"])

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx2.ReadTimeout("simulated read timeout")
        if self.mode == "network":
            raise httpx2.ConnectError("simulated connection reset")
        if self.mode == "status":
            return httpx2.Response(self.status, json={"error": {"message": "upstream refused"}})
        content: Any = (
            self.raw_content
            if self.raw_content is not None
            else json.dumps(self.payload, ensure_ascii=False)
        )
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": self.finish_reason}]
            },
        )

    @property
    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def fake_deepseek(monkeypatch: pytest.MonkeyPatch) -> FakeDeepSeek:
    """把结构化适配器的默认出网客户端换成 Mock transport（autouse，理由见模块 docstring）。"""
    fake = FakeDeepSeek()
    monkeypatch.setattr(
        "app.integrations.resume_structurer._build_http_client", lambda: fake.client
    )
    return fake


class StubOcr:
    """假 OCR 引擎：按「第几张图」返回预置文本，并记录被调用了几次。

    记调用次数是 §2.9 第 1 条判据（有文字层时**绝不**去认像素）唯一能测的方式——
    真实引擎不会告诉你它本不该被叫起来。
    """

    def __init__(self, texts: list[str] | None = None) -> None:
        self.calls: list[object] = []
        # 默认「什么都认不出」：没被指定看见过什么的假引擎，就该 behave 得像一张白纸。
        # 这样 v1 那几条「读不出文字 → 422」的用例不会因为多了第二级而悄悄变成 200。
        self._texts = texts if texts is not None else [""]
        self.name = "stub"

    def recognize(self, image: object) -> str:
        self.calls.append(image)
        index = len(self.calls) - 1
        return self._texts[index] if index < len(self._texts) else ""

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture(autouse=True)
def stub_ocr(monkeypatch: pytest.MonkeyPatch) -> StubOcr:
    """把 OCR 引擎换成假件，并清掉全局单例。

    两件都是必须的：
    1. 真引擎一次推理约 1.8 秒/页，全套跑下来只是把 CI 拖慢，而**判据并不更需要它**
       （端到端能认字那一件事由 `test_real_rapidocr_reads_text_from_image_only_pdf` 单独钉）；
    2. `_ENGINE` 是模块级单例，不清的话上一个测试建好的实例会漏给下一个测试，
       「引擎只建一次」这类断言就变成测缓存污染而不是测单例。
    """
    from app.integrations import pdf_ocr

    engine = StubOcr()
    pdf_ocr._ENGINE = None
    monkeypatch.setattr(pdf_ocr, "_build_engine", lambda: engine)
    yield engine
    pdf_ocr._ENGINE = None


# --------------------------------------------------------------------------- #
# 夹具式辅助
# --------------------------------------------------------------------------- #


def _register_and_login(client: TestClient) -> str:
    username = f"import_{uuid.uuid4().hex[:10]}"
    registered = client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    token: str = logged_in.json()["data"]["access_token"]
    return token


def _byok(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-DeepSeek-Key": API_KEY}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def build_pdf(pages: list[list[str]]) -> bytes:
    """拼一份**真的能被 pypdf 解析**的文字型 PDF。

    为什么不放一个样例二进制进仓库：这份 PDF 的全部内容就是用例的输入，写在代码里
    才能让「抽出来的文本 == 模型收到的那段文本」这条断言自解释；进了二进制资产，
    下一个改用例的人不知道该改哪一行。

    xref 偏移量按对象逐个算：pypdf 靠扫描确实能重建坏掉的表，但那种「读得出」
    是运气而不是契约，用例不该建在运气上。
    """
    page_count = len(pages)
    # 对象编号：1 Catalog、2 Pages、每页两个（Page + Contents），最后一个是字体。
    font_number = 3 + page_count * 2
    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (
            b" ".join(b"%d 0 R" % (3 + index * 2) for index in range(page_count)),
            page_count,
        ),
    ]
    for index in range(page_count):
        page_number = 3 + index * 2
        content_number = page_number + 1
        bodies.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (font_number, content_number)
        )
        stream = "".join(
            f"BT /F1 12 Tf 50 {760 - position * 16:.0f} Td ({line}) Tj ET\n"
            for position, line in enumerate(pages[index])
        ).encode("latin-1", "replace")
        bodies.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    bodies.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(bodies) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(bodies) + 1,
        xref_at,
    )
    return bytes(out)


def blank_page_pdf() -> bytes:
    """一份**只有版面、没有文字层**的 PDF（扫描件的等价形态）。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def image_pdf(text: str, *, pages: int = 1) -> bytes:
    """把一段文字**画成图**再嵌进 PDF：得到的文件没有文字层，只有像素。

    这就是扫描件在测试里的等价形态，而不是往仓库里塞一个二进制样例——
    「pypdf 抽出空串、pypdfium2 能渲染出字」这两件事都由代码自己说明，
    下一个改用例的人看得见该改哪一行。

    画布取 1800×440：嵌进 A4 页面按 2.0× 渲染后约 1190 宽，
    字高还剩够认的像素（本机实测更小的画布会被降采样到认不出）。
    `pages` 造多页时共用同一张图（对象编号：1 Catalog、2 Pages、每页两个、最后一个是图像）。
    """
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.new("RGB", (1800, 440), "white")
    ImageDraw.Draw(canvas).text((60, 160), text, fill="black", font=ImageFont.load_default(size=72))
    jpeg = io.BytesIO()
    canvas.save(jpeg, format="JPEG", quality=92)
    blob = jpeg.getvalue()

    image_number = 3 + pages * 2
    content = b"q 595 0 0 700 0 60 cm /Im0 Do Q"
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % (3 + index * 2) for index in range(pages)), pages),
    ]
    for index in range(pages):
        page_number = 3 + index * 2
        bodies.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
            % (image_number, page_number + 1)
        )
        bodies.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    bodies.append(
        b"<< /Type /XObject /Subtype /Image /Width 1800 /Height 440 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n%s\nendstream"
        % (len(blob), blob)
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(bodies) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(bodies) + 1,
        xref_at,
    )
    return bytes(out)


def _import(
    client: TestClient,
    token: str,
    data: bytes,
    *,
    filename: str = "resume.pdf",
    with_key: bool = True,
) -> Any:
    return client.post(
        IMPORT_URL,
        files={"file": (filename, data, "application/pdf")},
        headers=_byok(token) if with_key else _auth(token),
    )


def _profile(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]["profile"]
    return data


def _meta(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    meta: dict[str, Any] = response.json()["data"]["meta"]
    return meta


def _error(response: Any) -> dict[str, Any]:
    body = response.json()
    assert body["error"]["code"], response.text
    assert body["meta"]["request_id"], response.text
    error: dict[str, Any] = body["error"]
    return error


def _rows(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _with_statement_log(run: Callable[[], Any]) -> tuple[Any, list[str]]:
    """跑一次请求，同时把这次在 **Engine** 上执行过的语句全部收下来。

    挂在 Engine 而不是 Session（B1 的实测结论）：`before_cursor_execute` 是
    Connection/Engine 层事件，`event.listen(session, ...)` 会直接抛
    `InvalidRequestError: No such event`——那种写法要么在收集阶段就报错，
    要么被 except 掉，等于没有断言。
    """
    captured: list[str] = []

    def record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        captured.append(statement)

    engine: Engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = run()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, captured


# --------------------------------------------------------------------------- #
# 一、成功路径与零写入（2.1）
# --------------------------------------------------------------------------- #


def test_import_returns_normalized_profile(client: TestClient) -> None:
    """文字型 PDF → 200，且各格与模型给的内容一一对应。"""
    token = _register_and_login(client)

    profile = _profile(_import(client, token, build_pdf([["Zhang San", "Python", "2021-09"]])))

    assert profile["full_name"] == "张三"
    assert profile["contact"] == "13800138000"
    assert profile["target_role"] == "算法工程师"
    assert [skill["name"] for skill in profile["skills"]] == ["Python", "PyTorch", "SQL"]
    # `projects` 并进 `experiences`、经历在前项目在后（2.4）：前端没有独立的「项目」区块。
    titles = [experience["title"] for experience in profile["experiences"]]
    assert titles == ["某公司 算法实习", "校园问答系统"]
    assert profile["educations"][0]["school"] == "中国计量大学"
    assert profile["educations"][0]["degree"] == "本科"


def test_clean_import_reports_no_drops(client: TestClient) -> None:
    """全合格的一份简历：`dropped` 必须全是 0 / false，否则「丢了几条」就成了假话。"""
    token = _register_and_login(client)

    dropped = _meta(_import(client, token, build_pdf([["Python"]])))["dropped"]

    assert dropped == {
        "skills": 0,
        "experiences": 0,
        "educations": 0,
        "contact_discarded": False,
        "text_clamped": False,
    }


def test_extracted_text_is_what_reaches_the_model(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """抽出来的文字层**原样**是发给模型的那条消息（中间没有第二份文本）。"""
    token = _register_and_login(client)

    _import(client, token, build_pdf([["Alpha line", "Beta line"]]))

    assert "Alpha line" in fake_deepseek.user_message
    assert "Beta line" in fake_deepseek.user_message
    # 模型收到的必须是 PDF 里的字，而不是文件名或一句占位说明。
    assert "resume.pdf" not in fake_deepseek.user_message


def test_byok_key_is_forwarded_and_never_echoed(client: TestClient) -> None:
    """Key 从请求头进、往外呼请求出，但一个字节都不回显在响应里（`ADR-009`）。"""
    token = _register_and_login(client)

    response = _import(client, token, build_pdf([["Python"]]))

    assert response.status_code == 200, response.text
    assert API_KEY in response.request.headers.get("X-DeepSeek-Key", "")
    assert API_KEY not in response.text


def test_endpoint_writes_no_business_rows(client: TestClient) -> None:
    """零写入证明：Engine 上收集到的语句里不许有任何 DML，业务表行数一格不动。

    为什么不用「回滚后行数不变」：代码压根不 flush 时那种断言会空过，是典型的
    「绿色但无意义」。这里用**语句清单**做证据，并把清单原样打出来
    （`uv run pytest -s -k no_business_rows` 可见），交付贴的是实际取证而不是转述。
    """
    token = _register_and_login(client)
    before = {
        table: _rows(table) for table in ("student_profiles", "skills", "experiences", "educations")
    }

    response, statements = _with_statement_log(
        lambda: _import(client, token, build_pdf([["Python"]]))
    )
    writes = [statement for statement in statements if WRITE_PATTERN.match(statement)]
    print(f"[zero-write] 本次请求执行过的语句共 {len(statements)} 条")
    for index, statement in enumerate(statements):
        print(f"[zero-write] {index}: {statement!r}")
    print(f"[zero-write] 其中命中 INSERT/UPDATE/DELETE 的：{writes!r}")

    assert response.status_code == 200, response.text
    assert writes == [], f"导入端点写出了 DML：{writes}"
    # 采集器本身要证明「不是空过」：鉴权必然读一次 users。
    assert statements, "语句采集器没捕到任何东西，这条断言就是假的"
    assert any(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert {table: _rows(table) for table in before} == before


@pytest.mark.parametrize(
    "payload",
    [b"not a pdf at all", lambda: blank_page_pdf()],
    ids=["broken-file", "no-text-layer"],
)
def test_pdf_failure_writes_nothing(client: TestClient, payload: Any) -> None:
    """读取侧的两种失败同样不碰库：有人在服务里顺手写一行「导入失败记录」时这条会红。"""
    token = _register_and_login(client)
    data = payload() if callable(payload) else payload
    before = _rows("student_profiles")

    response, statements = _with_statement_log(lambda: _import(client, token, data))

    assert response.status_code == 422, response.text
    assert [statement for statement in statements if WRITE_PATTERN.match(statement)] == []
    assert _rows("student_profiles") == before


def test_ai_failure_writes_nothing(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """AI 侧失败也不留痕（与上一条合起来才覆盖「五种失败都在写之前」）。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 500
    before = _rows("student_profiles")

    response, statements = _with_statement_log(
        lambda: _import(client, token, build_pdf([["Python"]]))
    )

    assert response.status_code == 422, response.text
    assert [statement for statement in statements if WRITE_PATTERN.match(statement)] == []
    assert _rows("student_profiles") == before


# --------------------------------------------------------------------------- #
# 二、一个 422、五个码（2.5）
# --------------------------------------------------------------------------- #


def test_import_requires_login(client: TestClient) -> None:
    response = client.post(
        IMPORT_URL, files={"file": ("resume.pdf", build_pdf([["Python"]]), "application/pdf")}
    )

    assert response.status_code == 401, response.text


@pytest.mark.parametrize("mode", ["timeout", "network", "status"])
def test_ai_failures_are_422_service_unavailable(
    client: TestClient, fake_deepseek: FakeDeepSeek, mode: str
) -> None:
    """超时 / 断连 / 上游非 200 三种都归 `AI_SERVICE_UNAVAILABLE`，且都 `retryable`。"""
    token = _register_and_login(client)
    fake_deepseek.mode = mode
    fake_deepseek.status = 500

    response = _import(client, token, build_pdf([["Python"]]))

    error = _error(response)
    assert response.status_code == 422, response.text
    assert error["code"] == "AI_SERVICE_UNAVAILABLE"
    assert error["retryable"] is True


def test_missing_key_is_422_ai_key_missing(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """没带 `X-DeepSeek-Key`：不读文件、不出网，直接给「去设置」那一句。"""
    token = _register_and_login(client)

    response = _import(client, token, build_pdf([["Python"]]), with_key=False)

    error = _error(response)
    assert response.status_code == 422, response.text
    assert error["code"] == "AI_KEY_MISSING"
    assert "Key" in error["message"]
    # 「先判 Key 再抽文本」这条顺序的可观测证据：一个字节都没往外发。
    assert fake_deepseek.requests == []


def test_rejected_key_is_422_ai_key_missing(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """上游回 401（Key 无效/欠费）也归这个码（2.5 第 2 条）：用户能修的只有那一格。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 401

    response = _import(client, token, build_pdf([["Python"]]))

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "AI_KEY_MISSING"


def test_empty_content_is_422_service_unavailable(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """模型回空内容算「没答」，不能报成「答得不对」（与提取端点同一条诊断纪律）。"""
    token = _register_and_login(client)
    fake_deepseek.raw_content = "   "

    response = _import(client, token, build_pdf([["Python"]]))

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "AI_SERVICE_UNAVAILABLE"


@pytest.mark.parametrize(
    "content",
    [
        "抱歉，我无法解析这份简历。",
        '{"skills": "Python"}',
        '{"educations": [{"school": "A", "edu_start": "2021-09", "degree": 123}]}',
    ],
)
def test_unparseable_output_is_422_output_invalid(
    client: TestClient, fake_deepseek: FakeDeepSeek, content: str
) -> None:
    """散文、类型错的 JSON、结构不合契约的 JSON —— 三种都算模型没守格式。"""
    token = _register_and_login(client)
    fake_deepseek.raw_content = content

    response = _import(client, token, build_pdf([["Python"]]))

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "AI_OUTPUT_INVALID"


def test_truncated_output_is_422_output_invalid(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """被我们自己的 `max_tokens` 切断：那是「输出不完整」，不是「上游不可用」。"""
    token = _register_and_login(client)
    fake_deepseek.finish_reason = "length"

    response = _import(client, token, build_pdf([["Python"]]))

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "AI_OUTPUT_INVALID"


def test_oversized_upload_is_422_pdf_too_large(client: TestClient) -> None:
    """超过 5 MiB：先中止读取再报，且此时根本不该出网（带着 Key 也一样）。"""
    token = _register_and_login(client)
    payload = b"%PDF-1.4\n" + b"x" * (MAX_PDF_BYTES + 1024)

    response = _import(client, token, payload)

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "PDF_TOO_LARGE"


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        # 汉字必须显式 encode：`b"中文"` 在 Python 里直接是语法错误（bytes 只收 ASCII）。
        ("这不是 PDF，只是一段纯文本。".encode(), "resume.txt"),
        (b"", "empty.pdf"),
        (b"%PDF-1.4\n" + "乱写的对象流".encode(), "broken.pdf"),
    ],
)
def test_unreadable_pdf_is_422_pdf_invalid(client: TestClient, data: bytes, filename: str) -> None:
    """非 PDF、空文件、结构坏了：三种都是「这份文件我们读不动」。"""
    token = _register_and_login(client)

    response = _import(client, token, data, filename=filename)

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "PDF_INVALID"


def test_pdf_without_text_layer_is_422_pdf_invalid(client: TestClient, stub_ocr: StubOcr) -> None:
    """扫描件（有版面没文字层）是最常见的一种失败，必须单独钉住。

    v2 之后它不再直接失败，而是**两级都失败**才失败：所以这条要把 OCR 也设成
    「什么都认不出」，否则它会在第二级被救回来，测的就不是这一句了。
    """
    token = _register_and_login(client)
    stub_ocr._texts = [""]

    response = _import(client, token, blank_page_pdf())

    error = _error(response)
    assert response.status_code == 422, response.text
    assert error["code"] == "PDF_INVALID"
    # 文案要给一条真能走的路，不能只说「失败」。
    assert "手动" in error["message"] or "文字" in error["message"]


# --------------------------------------------------------------------------- #
# 三、三封顶与可见性（2.3）
# --------------------------------------------------------------------------- #


def test_only_first_three_pages_are_read_and_said_so(client: TestClient) -> None:
    """4 页的 PDF：只读前 3 页，且 `pages_total` 与 `pages_used` 分开报（不许静默截页）。"""
    token = _register_and_login(client)

    meta = _meta(_import(client, token, build_pdf([[f"page {n} text"] for n in range(4)])))

    assert meta["pages_total"] == 4
    assert meta["pages_used"] == 3
    assert meta["prompt_version"] == "v1"


def test_one_page_pdf_says_no_truncation(client: TestClient) -> None:
    """正常长度的一份简历：两个截断标记都必须是假的，否则用户以为被切过。"""
    token = _register_and_login(client)

    meta = _meta(_import(client, token, build_pdf([["Only page"]])))

    assert meta["pages_total"] == 1 and meta["pages_used"] == 1
    assert meta["text_truncated"] is False


def test_prompt_carries_the_write_side_caps(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """发出去的 Prompt 里带着 8 / 3 / 5 这三个数：模型少产几条，用户就少被裁几条。"""
    token = _register_and_login(client)

    _import(client, token, build_pdf([["Python"]]))

    prompt = fake_deepseek.system_prompt
    assert "skills 最多 8 条" in prompt
    assert "各最多 3 条" in prompt
    assert "educations 最多 5 条" in prompt


# --------------------------------------------------------------------------- #
# 三点五、v2 的三级降级链路（`ADR-031` §2.9）
# --------------------------------------------------------------------------- #


def test_text_layer_hit_never_calls_ocr(client: TestClient, stub_ocr: StubOcr) -> None:
    """判据 §5 第 9 条：文字层能读到东西时**一个像素都不该去认**。

    顺序反过来不是「多花 1.8 秒」这么简单：同一页 OCR 的结果比文本层差
    （实测 `SCANME 2024` → `SCAME024`），把质量更低的那一级放在前面等于
    系统性地给用户提供脏数据。
    """
    token = _register_and_login(client)

    meta = _meta(_import(client, token, build_pdf([["Zhang San  Python"]])))

    assert meta["source"] == "text_layer"
    assert stub_ocr.call_count == 0


def test_scanned_pdf_falls_back_to_ocr_and_says_so(
    client: TestClient, stub_ocr: StubOcr, fake_deepseek: FakeDeepSeek
) -> None:
    """判据 §5 第 8 条前半：无文字层 → 走 OCR、`meta.source == "ocr"`、
    且**送进模型的就是 OCR 认回来的那段文本**。"""
    token = _register_and_login(client)
    stub_ocr._texts = ["张三  中国计量大学  13800138000"]

    response = _import(client, token, image_pdf("SCANME 2024"))

    meta = _meta(response)
    assert meta["source"] == "ocr"
    assert meta["pages_total"] == 1 and meta["pages_used"] == 1
    assert stub_ocr.call_count == 1
    # 假引擎返回的两行都该出现在发给模型的那条消息里（顺序按页序，用换行拼接）。
    assert "中国计量大学" in fake_deepseek.user_message
    assert "13800138000" in fake_deepseek.user_message


def test_blank_page_pdf_goes_through_ocr_then_422(client: TestClient, stub_ocr: StubOcr) -> None:
    """判据 §5 第 8 条后半：OCR 也认不出 → 422 `PDF_INVALID`，文案给的是能动的下一步。

    这一条同时证明「第二级真的被叫起来了」（`call_count`），
    而不是文本层失败后直接跳到失败——那样「兜底」就只是一句注释。
    """
    token = _register_and_login(client)
    stub_ocr._texts = [""]

    response = _import(client, token, blank_page_pdf())

    error = _error(response)
    assert response.status_code == 422, response.text
    assert error["code"] == "PDF_INVALID"
    assert stub_ocr.call_count == 1
    assert "没识别到文字" in error["message"] and "手动" in error["message"]


def test_ocr_only_reads_first_three_pages(
    client: TestClient, stub_ocr: StubOcr, fake_deepseek: FakeDeepSeek
) -> None:
    """v2 仍只处理前 3 页（老板原话「防止超时」）：4 页扫描件只渲染 3 张。"""
    token = _register_and_login(client)
    stub_ocr._texts = ["第一页", "第二页", "第三页", "第四页不该被读到"]

    meta = _meta(_import(client, token, image_pdf("SCANME 2024", pages=4)))

    assert stub_ocr.call_count == 3
    assert meta["pages_used"] == 3
    assert meta["pages_total"] == 4, "只读前 3 页这件事要说得出总共几页"
    assert "第四页不该被读到" not in fake_deepseek.user_message


def test_ocr_engine_is_built_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """判据 §5 第 10 条前半：引擎是全局单例——两次导入只建一次模型。

    这里刻意不用 `stub_ocr` 夹具：要数的正是「建了几次」，
    所以自己换掉 `_build_engine` 并自己清单例（夹具的清理与本条互斥）。
    """
    from app.integrations import pdf_ocr

    built: list[StubOcr] = []

    def build() -> StubOcr:
        engine = StubOcr(["认出来的字"])
        built.append(engine)
        return engine

    pdf_ocr._ENGINE = None
    monkeypatch.setattr(pdf_ocr, "_build_engine", build)
    try:
        token = _register_and_login(client)
        _import(client, token, image_pdf("A"))
        _import(client, token, image_pdf("B"))
    finally:
        pdf_ocr._ENGINE = None

    assert len(built) == 1, f"模型被建了 {len(built)} 次，单例没生效"


def test_ocr_recognition_is_serialized_by_one_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """判据 §5 第 10 条后半：每次识别都过同一把 `threading.Lock`（老板指定的串行化）。

    用一把会记数的锁换掉模块级那把，再直接开引擎认两张图：
    锁被 acquire 的次数必须是「调用次数」，少一次就是漏了串行化，
    多一次就是锁在了错误的位置（比如整页渲染也被它挡住）。
    """
    from app.integrations import pdf_ocr

    class CountingLock:
        def __init__(self) -> None:
            self.acquired = 0
            self._inner = threading.Lock()

        def __enter__(self) -> object:
            self.acquired += 1
            return self._inner.__enter__()

        def __exit__(self, *exc: object) -> None:
            self._inner.__exit__(*exc)

    counter = CountingLock()
    monkeypatch.setattr(pdf_ocr, "_CALL_LOCK", counter)

    seen: list[object] = []

    def instance(image: object) -> object:
        seen.append(image)
        return type("Res", (), {"txts": ("HELLO",)})()

    engine = pdf_ocr.RapidOcrEngine(instance)
    assert engine.recognize("图一") == "HELLO"
    assert engine.recognize("图二") == "HELLO"
    assert counter.acquired == 2
    assert len(seen) == 2


def test_ocr_empty_txts_is_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 `RapidOCR` 什么都没认出来时 `txts` 是 `None`（本机实测），不是空元组。

    不判空就是 `TypeError: 'NoneType' is not iterable` → 500 + 问题编号，
    而正确答案是一句 422。这条测的是那一格判空本身，所以直接开引擎、不经过端点。
    """
    from app.integrations import pdf_ocr

    engine = pdf_ocr.RapidOcrEngine(lambda image: type("Res", (), {"txts": None})())
    assert engine.recognize("一张空白图") == ""


def test_real_rapidocr_reads_text_from_image_only_pdf(
    client: TestClient, fake_deepseek: FakeDeepSeek, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整条第二级用**真引擎**跑一遍：证明 OCR 不是只在假件里成立。

    本机实测（2026-11-16，CPU）：一张写着 `SCANME 2024` 的图嵌进 PDF、按 2.0× 渲染后，
    真引擎认回来的是 `SCAME024`——**丢了一个 `N` 与一个空格**。所以这条断言刻意只断
    「非空 + 是 OCR 来的 + 那段文本进了模型」，不去断字符逐字正确：
    逐字正确是 §6 遗留第 5 条记下的已知不足，不是本批的承诺。
    """
    from rapidocr import RapidOCR

    from app.integrations import pdf_ocr

    pdf_ocr._ENGINE = None
    monkeypatch.setattr(pdf_ocr, "_build_engine", lambda: pdf_ocr.RapidOcrEngine(RapidOCR()))
    token = _register_and_login(client)
    try:
        response = _import(client, token, image_pdf("SCANME 2024"))
    finally:
        pdf_ocr._ENGINE = None

    assert response.status_code == 200, response.text
    assert _meta(response)["source"] == "ocr"
    assert fake_deepseek.user_message.strip() != ""


# --------------------------------------------------------------------------- #
# 四、归一化与 dropped 的账（2.4）
# --------------------------------------------------------------------------- #


def test_skills_over_cap_are_counted_not_silently_dropped(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """12 条技能 → 只留 8 条，且 `dropped.skills == 4`（判据 3）。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {**CLEAN_OUTPUT, "skills": [{"name": f"技能{i}"} for i in range(12)]}

    response = _import(client, token, build_pdf([["Python"]]))

    assert len(_profile(response)["skills"]) == 8
    assert _meta(response)["dropped"]["skills"] == 4


def test_duplicate_skill_names_are_merged_once(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """`Python` / `python ` 是同一条：库里的大小写不敏感唯一键会撞 1062（`ADR-011` §2.D.2）。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {**CLEAN_OUTPUT, "skills": [{"name": "Python"}, {"name": "python "}]}

    response = _import(client, token, build_pdf([["Python"]]))

    assert [skill["name"] for skill in _profile(response)["skills"]] == ["Python"]
    assert _meta(response)["dropped"]["skills"] == 1


def test_experiences_and_projects_share_three_slots(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """经历在前、项目在后，合起来只留 3 段，第 4 段计入 `dropped.experiences`。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "experiences": [
            {"title": "经历一", "start_date": "2023-01", "end_date": "2023-06"},
            {"title": "经历二", "start_date": "2023-07", "end_date": "2023-12"},
        ],
        "projects": [
            {"title": "项目一", "start_date": "2024-01"},
            {"title": "项目二", "start_date": "2024-03"},
        ],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    titles = [item["title"] for item in _profile(response)["experiences"]]
    assert titles == ["经历一", "经历二", "项目一"]
    assert _meta(response)["dropped"]["experiences"] == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2021-09", "2021-09-01"),
        ("2021年9月", "2021-09-01"),
        ("2021.09", "2021-09-01"),
        ("202109", "2021-09-01"),
        ("2021-09-15", "2021-09-15"),
        ("2021", "2021-01-01"),
    ],
)
def test_date_shapes_all_reach_a_real_date(
    client: TestClient, fake_deepseek: FakeDeepSeek, raw: str, expected: str
) -> None:
    """模型可能给的六种日期形状都要能落成库里那一列 `date`（解析不出就整条丢，代价太大）。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "experiences": [{"title": "实习", "start_date": raw, "end_date": None}],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    assert _profile(response)["experiences"][0]["start_date"] == expected


def test_impossible_dates_do_not_500(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """`2024-02-31` / `2024-13` 这种「看着像日期但成不了」的值：丢这一条并计数，不是 500。

    这两个形状都是模型会原样抄回来的（PDF 里印错日期、或把不相干的数字抄进日期格），
    而服务层一旦漏接住 `ValueError`，用户拿到的是一句「服务暂时不可用 + 问题编号」。
    """
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "experiences": [
            {"title": "闰年之外", "start_date": "2024-02-31"},
            {"title": "没有第十三个月", "start_date": "2024-13"},
        ],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    titles = [item["title"] for item in _profile(response)["experiences"]]
    assert titles == ["校园问答系统"]
    assert _meta(response)["dropped"]["experiences"] == 2


def test_year_only_dates_use_the_whole_year(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """只有年份时开始补年初、结束补年末：「2020 至今」这类写法不该被判成 `end < start`。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "educations": [
            {
                "school": "某大学",
                "major": "",
                "degree": "本科",
                "edu_start": "2020",
                "edu_end": "2020",
            }
        ],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    education = _profile(response)["educations"][0]
    assert (education["edu_start"], education["edu_end"]) == ("2020-01-01", "2020-12-31")


def test_experience_with_future_start_is_dropped(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """开始日期在未来是后端 `ExperienceInput` 的硬 422，不该由 AI 造出来。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "experiences": [{"title": "还没开始", "start_date": "2099-01"}],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    # `CLEAN_OUTPUT.projects` 那一段仍然留下：丢掉的是「未来开始」那条，不是整组。
    titles = [item["title"] for item in _profile(response)["experiences"]]
    assert titles == ["校园问答系统"]
    assert _meta(response)["dropped"]["experiences"] == 1


@pytest.mark.parametrize(
    ("degree", "kept"),
    [("学士", "本科"), ("大专", "专科"), ("博士研究生", "博士"), ("bachelor", "本科")],
)
def test_known_degree_synonyms_are_collapsed(
    client: TestClient, fake_deepseek: FakeDeepSeek, degree: str, kept: str
) -> None:
    """确有把握的同义写法折成库里那四个值（`ck_educations_degree`）。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "educations": [{"school": "某大学", "major": "", "degree": degree, "edu_start": "2021-09"}],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    assert _profile(response)["educations"][0]["degree"] == kept


def test_unknown_degree_drops_the_education_row(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """「高中」库里装不下：丢这一条并计数，而不是把它折成「专科」伪造事实。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "educations": [{"school": "某高中", "major": "", "degree": "高中", "edu_start": "2018-09"}],
    }

    response = _import(client, token, build_pdf([["Python"]]))

    assert _profile(response)["educations"] == []
    assert _meta(response)["dropped"]["educations"] == 1


@pytest.mark.parametrize(
    ("raw", "expected", "discarded"),
    [
        ("138-0013-8000", "13800138000", False),
        ("+86 138 0013 8000", "13800138000", False),
        ("0571-88888888", "", True),
        ("", "", False),
    ],
)
def test_contact_digits_kept_or_discarded(
    client: TestClient, fake_deepseek: FakeDeepSeek, raw: str, expected: str, discarded: bool
) -> None:
    """手机号只留数字；带 `+86` 的同一个号码不许被丢掉，而非法号码一律留空并报告。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {**CLEAN_OUTPUT, "contact": raw}

    response = _import(client, token, build_pdf([["Python"]]))

    assert _profile(response)["contact"] == expected
    assert _meta(response)["dropped"]["contact_discarded"] is discarded


# --------------------------------------------------------------------------- #
# 五、回执即可保存（`profile` 就是 `ProfileUpsertRequest`）
# --------------------------------------------------------------------------- #


def test_imported_profile_can_be_saved_directly(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """把导入响应原样 `PUT /profiles/me` 必须 200：用户不该在点保存时才吃 422。"""
    token = _register_and_login(client)
    fake_deepseek.payload = {
        **CLEAN_OUTPUT,
        "skills": [{"name": f"技能{i}"} for i in range(12)],
        "experiences": [{"title": f"经历{i}", "start_date": "2023-01"} for i in range(5)],
        "educations": [
            {"school": f"学校{i}", "major": "", "degree": "本科", "edu_start": "2021-09"}
            for i in range(7)
        ],
    }

    profile = _profile(_import(client, token, build_pdf([["Python"]])))
    saved = client.put(PROFILE_ME_URL, json=profile, headers=_auth(token))

    assert saved.status_code == 200, saved.text
    # 保存之后库里确实是导入的那几条：上限数与写侧同源（8 / 3 / 5）。
    # `PUT /profiles/me` 的响应把档案包在 `data.profile` 里（`app/api/v1/profiles.py:134`）。
    data = saved.json()["data"]["profile"]
    assert len(data["skills"]) == 8
    assert len(data["experiences"]) == 3
    assert len(data["educations"]) == 5


def test_import_does_not_need_an_existing_profile(client: TestClient) -> None:
    """刚注册、一份档案都没有：导入仍然 200（它不读库，也不要求库里已经有行）。"""
    token = _register_and_login(client)

    assert _rows("student_profiles") == 0
    assert _import(client, token, build_pdf([["Python"]])).status_code == 200


def test_import_rate_limit_rule_matches_the_polish_tier() -> None:
    """`ADR-031` 3 代价第 4 条：限流沿用润色那一档，不单独放宽。"""
    assert IMPORT_RATE_LIMIT == "10/minute"
