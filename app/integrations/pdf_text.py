"""PDF 文字层抽取适配器（`ADR-031` 2.2 / 2.3）。

职责边界：本模块只做一件事——**把字节变成一段文本**，不读库、不写库、不调模型，
也不判断「这份档案缺哪一格」。分层理由与 `skill_extractor.py` 同一处：
`integrations/` 只带 AI/解析侧的契约，业务归一在服务层。

**为什么是 pypdf 而不是 pdfplumber**（2.2）：下游是一个「读文本、吐 JSON」的模型，
不需要版面与坐标；扫描版谁都抽不出字，换库不改变这件事，所以选纯 Python、
安装面小的那一个。

三条封顶里有两条住在本文件（2.3）：

| 常量 | 值 | 对应的故障 |
|---|---|---|
| `MAX_PAGES` | 3 页 | 论文式长 PDF 全文进 prompt 会挤掉档案本身该有的 token |
| `MAX_TEXT_CHARS` | 12000 字 | 超长输入下模型首尾注意力最差，且 30 秒预算兜不住 |

体积上限（5 MiB）不在这里：那是「读多少字节」的问题，属于上传侧（服务层流式累计）。

`pages_total` / `pages_used` / `text_truncated` 三个观测点必须带出来——`ADR-017`
立的规矩是「截断要看得见」，只回一段文本的话，用户永远不知道自己那份 12 页的
PDF 其实只读了前 3 页。
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

#: 只读前几页（2.3）。简历的正常长度是 1–2 页，3 页已经兜得住「一页纸 + 附录」。
MAX_PAGES = 3

#: 送进模型的字符上限（2.3）。中文约 1 字 ≈ 1 token 量级，12000 字 + 输出 2000 token
#: 仍在 `deepseek-flash` 的上下文内且 30 秒可回（`ADR-009` 的非流式超时口径）。
MAX_TEXT_CHARS = 12000

#: PDF 的文件头魔数：规范要求前四个字节就是 `%PDF`。
PDF_MAGIC = b"%PDF"

#: 文本是从哪一级来的（`ADR-031` v2 §2.9）。这两个值会原样进 `meta.source`：
#: 降级发生过就要能说出来（`ADR-017` 同一条纪律），也是前端两条路径
#: 「表现一致」那条验收判据唯一的观测点。常量住在本文件是因为
#: `pdf_ocr.py` import 它，反过来会成环。
#: 类型写成 `Literal` 而不是 `str`：响应契约（`PdfImportMeta.source`）就是那两个值，
#: 这里松一格，mypy 就得在服务层再判一次「这个 str 是不是那两个字之一」——
#: 而那个判断本来由类型系统做就够了。
SOURCE_TEXT_LAYER: Literal["text_layer"] = "text_layer"
SOURCE_OCR: Literal["ocr"] = "ocr"


class PdfTextError(Exception):
    """抽取失败基类。消息里**不允许**出现 PDF 正文内容。"""


class PdfUnreadableError(PdfTextError):
    """这份文件读不出文字：不是 PDF、结构坏了、加密打不开，或 OCR 也认不出。

    对外同一句（`ADR-031` 2.5 的 `PDF_INVALID`）：用户能做的动作是同一个——
    换一份能识别的 PDF，或者手动填。区分「扫描件」与「加密」只在让他猜更细的原因。
    """


class PdfNoTextError(PdfUnreadableError):
    """**有版面、没有文字层**——扫描件与图片版的专属形态，是三级链路的触发条件。

    单独一个类型不是为了细分文案（对外仍然并成 `PDF_INVALID` 那一句），而是为了
    让「该不该去认像素」有一个可判的依据：`PdfUnreadableError` 的其他三种原因
    （不是 PDF / 结构坏 / 加密）走 OCR 也一样读不出，白付一次推理。
    """


@dataclass(frozen=True)
class PdfText:
    """抽取结果。`text` 已经过 `MAX_TEXT_CHARS` 截断，其余几格是它的来历。"""

    pages_total: int
    pages_used: int
    text: str
    text_chars: int
    truncated: bool
    #: 默认是文本层：`PypdfTextExtractor` 只可能产出这一种，
    #: 走没走 OCR 由 `TextLayerThenOcrExtractor` 显式改写。
    source: Literal["text_layer", "ocr"] = SOURCE_TEXT_LAYER


class PdfTextExtractor(ABC):
    """字节 → 文本的抽象：测试用假实现替换，真实实现只在 `dependencies.py` 里装配。"""

    @abstractmethod
    def extract(self, data: bytes) -> PdfText:
        """读出前 `MAX_PAGES` 页的文字层；读不出来时抛 `PdfUnreadableError`。"""


class PypdfTextExtractor(PdfTextExtractor):
    """用 `pypdf` 在本地抽文字层，全程不出网（`ADR-031` 2.8：PDF 原文只在内存里）。"""

    def extract(self, data: bytes) -> PdfText:
        if not data[: len(PDF_MAGIC)] == PDF_MAGIC:
            # 不看魔数就交给 pypdf 也行，但那样一份 .docx 改名的文件会报成「PDF 坏了」，
            # 而真话是「这不是 PDF」——两个诊断对应的用户动作不同。
            raise PdfUnreadableError("文件不是 PDF。")

        try:
            reader = PdfReader(io.BytesIO(data))
            # 空密码解一次：未加密但被写成「所有者密码为空」的文件走这条路能过；
            # 真的加了密就抛，落到同一句「读不出文字」。
            if reader.is_encrypted and not reader.decrypt(""):
                raise PdfUnreadableError("PDF 已加密。")
            pages = reader.pages
            pages_total = len(pages)
            pages_used = min(pages_total, MAX_PAGES)
            chunks: list[str] = []
            for index in range(pages_used):
                try:
                    chunks.append(pages[index].extract_text() or "")
                except PdfReadError:
                    # 单页坏掉不该让整份文件白传：那一页当空白处理，剩下的页照常抽。
                    continue
        except PdfReadError as error:
            # 只记异常类型，不把 `str(error)` 往外抛：pypdf 的消息里可能带对象名与偏移量。
            raise PdfUnreadableError("PDF 结构无法解析。") from error

        text = "\n".join(chunks).strip()
        if not text:
            # 这是三级链路的触发信号（`ADR-031` v2 §2.9）：文件本身读得动，只是没有可复制的字。
            raise PdfNoTextError("PDF 没有文字层。")

        truncated = len(text) > MAX_TEXT_CHARS
        kept = text[:MAX_TEXT_CHARS]
        return PdfText(
            pages_total=pages_total,
            pages_used=pages_used,
            text=kept,
            text_chars=len(kept),
            truncated=truncated,
        )
