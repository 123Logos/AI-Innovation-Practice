"""PDF 导出的失败语义（批次 B10b，`ADR-029` §2.5）。

住在 `app/core` 而不是 `app/integrations/pdf_renderer.py`：import-linter 的
「API cannot depend on integrations」契约禁止路由层直接 import 适配器，但路由必须能把
「浏览器不可用」翻成 503。把异常放在两个层都能引用的 core 里，路由就只需要认识
这个异常，不需要认识产生它的适配器（与 `EvidenceScore*` 异常由服务层翻译同构）。

**为什么不静默降级**：找不到浏览器 / 渲染失败时，返回 HTML 或一个空 PDF
都会让用户以为导出成功。宁可 503 + 一句能走的路（浏览器打印）。
"""

from __future__ import annotations


class PdfRendererUnavailableError(Exception):
    """无头浏览器不可用或渲染产物不是 PDF。"""
