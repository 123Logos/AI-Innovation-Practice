"""PDF 导入的响应契约（`ADR-031` 2.4 / 2.5）。

单独一个模块而不是塞进 `schemas/resume.py`：`schemas/profile.py` 已经 import
`schemas/resume.py`，这里要同时引用两边的契约（`ProfileUpsertRequest` 与
`SkillInput`），反向 import 会成环。

**`profile` 一格直接复用 `ProfileUpsertRequest`**，这是刻意的：AI 归一化的产物
必须**当场通过**保存用的校验，而不是等用户点「保存修改」才发现 422。
前端拿到的 `profile` 因此可以原样 PUT——回执即契约。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.profile import ProfileUpsertRequest


class PdfImportDropped(BaseModel):
    """被裁掉的条数（`ADR-017`：截断必须看得见，一句都不许静默）。

    - 三个计数只统计**真的有内容**却被丢掉的条目：空白行、与前面重名的技能不计入
      `skills`（那是模型给的噪音，不是用户的数据）；
    - `contact_discarded` = PDF 里有手机号但不是大陆 11 位（后端 PUT 会 422，
      不该由 AI 造一个非法值进表单）；
    - `text_clamped` = 有描述或职责被截到文案契约上限（与润色同一对数字）。
    """

    skills: int = Field(default=0, ge=0)
    experiences: int = Field(default=0, ge=0)
    educations: int = Field(default=0, ge=0)
    contact_discarded: bool = False
    text_clamped: bool = False


class PdfImportMeta(BaseModel):
    """这次导入读了多少、发了哪版指令、裁掉了什么。

    `pages_total` / `pages_used` 分开给：一份 12 页的 PDF 只读了前 3 页
    （`MAX_PAGES`），只看 `pages_used` 就说不出这件事。
    `source` 是 v2 加的第三格（`ADR-031` §2.9）：这段文本是从**文字层**读的还是从
    **像素**认回来的。它必须回传，因为两级的质量不一样（实测同一页 OCR 会把
    `SCANME 2024` 认成 `SCAME024`），而「为什么这一份的字段更脏」总得有人回答。
    """

    pages_total: int
    pages_used: int
    text_chars: int
    text_truncated: bool
    prompt_version: str
    source: Literal["text_layer", "ocr"]
    dropped: PdfImportDropped


class PdfImportResult(BaseModel):
    """`POST /resumes/import-pdf` 的响应体：草稿 + 来历，没有一行进过数据库。"""

    profile: ProfileUpsertRequest
    meta: PdfImportMeta
