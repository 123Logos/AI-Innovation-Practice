"""PDF 渲染适配器（批次 B10b，`ADR-029` §2.5）。

职责边界：只把**服务端已经拼好的打印 HTML**交给无头浏览器、把 PDF 字节带回来。
不读库、不判权限、不决定导出哪些简历（那是 `app/services/teacher_tools_service.py`）。

为什么不引 reportlab / weasyprint：本批的硬约束是「自然分页 + 非 scoped 的
`page-break-inside: avoid`」——那是**浏览器排版**的语义，用 Python 重画一份 PDF
等于另写一套分页算法，写出来的一定不是预览里那一版。所以走系统级无头
Chromium / Edge 的 `--print-to-pdf`，**不新增 Python 依赖**（`AGENTS.md` §4.6 的
「新依赖」事件因此不触发，但系统工具的配置口径写进 `Settings.pdf_browser_path`）。

失败语义：找不到浏览器 / 超时 / 非 0 退出 / 产物空 → 一律
`PdfRendererUnavailableError`，由服务层翻成 503。**不返回假的 PDF，也不静默
降级成 HTML**——这两种都让用户以为导出成功了。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings
from app.core.pdf_errors import PdfRendererUnavailableError

logger = logging.getLogger(__name__)

#: Linux CI / 常见容器与本机 Windows EdgeCore 的候选路径。显式配置优先。
_BROWSER_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "msedge",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

WINDOWS_EDGE_CORE_ROOT = r"C:\Program Files (x86)\Microsoft\EdgeCore"


class PdfRenderer(ABC):
    @abstractmethod
    def render(self, html: str) -> bytes:
        """把完整打印 HTML 渲染成 PDF 字节。"""


def _resolve_browser(configured: str | None) -> str | None:
    if configured:
        return configured if Path(configured).exists() else None
    for candidate in _BROWSER_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).is_file():
            return candidate
    # 本机验收用的 EdgeCore 是带版本号的子目录（`AGENTS.md` §4.5），不在 PATH 上。
    root = Path(WINDOWS_EDGE_CORE_ROOT)
    if root.is_dir():
        executables = sorted(root.glob("*/msedge.exe"), reverse=True)
        if executables:
            return str(executables[0])
    return None


class HeadlessBrowserPdfRenderer(PdfRenderer):
    """无头 Chromium / Edge `--print-to-pdf`。

    HTML 与产物都写在一次性的临时目录里，**渲染完立刻删**：导出的中间文件没有理由
    留在磁盘上（与 B11b 那条「音频转写完立即删除」同一条隐私纪律）。
    """

    TIMEOUT_SECONDS = 90

    def __init__(self, browser_path: str | None = None) -> None:
        configured = browser_path if browser_path is not None else get_settings().pdf_browser_path
        self._browser = _resolve_browser(configured)

    @property
    def available(self) -> bool:
        return self._browser is not None

    def render(self, html: str) -> bytes:
        if self._browser is None:
            raise PdfRendererUnavailableError("没有可用的无头浏览器")

        with tempfile.TemporaryDirectory(prefix="resume-export-") as workdir:
            source = Path(workdir) / "resume.html"
            target = Path(workdir) / "resume.pdf"
            source.write_text(html, encoding="utf-8")
            command = [
                self._browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # 不传 `--no-pdf-header-footer`：页码「当前页 / 总页数」由浏览器的
                # 打印页脚给，正文 HTML 里一个页码都不写（`ADR-029` §2.5）。
                f"--print-to-pdf={target}",
                source.as_uri(),
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=self.TIMEOUT_SECONDS,
                    check=False,
                    env={**os.environ, "HOME": workdir},
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                logger.warning("PDF 渲染调用失败（type=%s）", type(error).__name__)
                raise PdfRendererUnavailableError("PDF 渲染服务暂时不可用。") from error
            if completed.returncode != 0 or not target.is_file():
                logger.warning("PDF 渲染失败（status=%s）", completed.returncode)
                raise PdfRendererUnavailableError("PDF 渲染服务暂时不可用。")
            content = target.read_bytes()
        if not content.startswith(b"%PDF-"):
            raise PdfRendererUnavailableError("渲染产物不是 PDF")
        return content
