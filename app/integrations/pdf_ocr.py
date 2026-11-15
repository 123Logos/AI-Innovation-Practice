"""扫描版 PDF 的 OCR 兜底（`ADR-031` v2 §2.9，第 29 轮）。

**这是三级链路里的第二级**：

```
① pypdf 读文字层        → 非空就用它（无损、近乎零耗时）
② pypdfium2 渲染 + RapidOCR → 文字层为空才走这一步（本地推理，不出网）
③ 两级都空              → 抛 `PdfUnreadableError`，服务层翻译成 422 `PDF_INVALID`
```

顺序不能反过来，这条不是偏好而是实测：同一张写着 `SCANME 2024` 的图，直接喂 OCR 认回
`SCANME 2024`，嵌进 PDF 再按 2.0× 渲染出来喂进去就变成 `SCAME024`（丢一个 `N` 和一个空格）。
**文字型 PDF 用文本层是无损的**，所以只要文本层能读到东西，就不该去认像素。

三条硬约束与它们的理由：

1. **全局单例 + 一把 `threading.Lock`**（老板第 29 轮原话）。三个 `.onnx` 权重常驻内存，
   每个请求各建一份就是成倍占用；串行化的代价是「两个人同时导扫描件」时第二个排队，
   在不引入任务队列（那是另一篇 ADR）的前提下，排队比 OOM 好。
2. **仍只处理前 3 页**：OCR 比文本层贵两个数量级（本机实测约 1.8 秒/页），
   页数封顶是这条路径唯一的时间兜底。
3. **模型在 wheel 里，运行时不下载**（实测日志 `File exists and is valid: …rapidocr\\models\\…onnx`）：
   所以离线部署可用，也**不出网**——这与 `ADR-031` 2.8 的隐私边界一致：
   学生的姓名、电话、学校只在本机内存里过一遍，第二个接收方只有用户自己那把 Key 对应的 DeepSeek。

分层与 `pdf_text.py` 同口径：本模块不知道前端上限、不知道哪格必填，
只把「字节 → 文本」这件事做完，并如实说出台阶是第几级（`PdfText.source`）。
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.integrations.pdf_text import (
    MAX_PAGES,
    MAX_TEXT_CHARS,
    SOURCE_OCR,
    PdfNoTextError,
    PdfText,
    PdfTextExtractor,
    PdfUnreadableError,
    PypdfTextExtractor,
)

logger = logging.getLogger(__name__)

#: 渲染缩放（`ADR-031` 2.3 v2 那一行，老板指定 2 倍）。
#: 本机实测 A4 页面在 2.0× 下是 1190×1684 像素——再大只是把推理时间拉长，认字率不再明显上升。
OCR_RENDER_SCALE = 2.0


class OcrEngineError(Exception):
    """OCR 引擎自身的失败。消息里**不允许**出现识别出的文本内容。"""


#: 建实例的锁（双检锁：两个请求同时首次触发时只建一份模型）。
_ENGINE_LOCK = threading.Lock()
#: 识别调用的锁。`onnxruntime` 会话本身可以并发推理，但并发会把内存顶上去，
#: 而这条链路的正确性不依赖并发——排队比 OOM 好（`ADR-031` v2 §2.9 第 1 条）。
_CALL_LOCK = threading.Lock()
#: 引擎实例。懒建：没导过扫描件的用户不为模型初始化付一分钱成本。
_ENGINE: RapidOcrEngine | None = None


class OcrEngine(ABC):
    """位图 → 文本的抽象：测试注入假引擎，真实实现只在装配点里懒建。"""

    #: 给日志与响应用的名字（`"rapidocr"` 之类），不说出模型文件名与本地路径。
    name: str = "unknown"

    @abstractmethod
    def recognize(self, image: Any) -> str:
        """认一张位图里的字，返回按行拼接的文本；认不出返回空串。"""


class RapidOcrEngine(OcrEngine):
    """`rapidocr` + `onnxruntime` 的 CPU 推理封装（全局单例 + 调用串行化）。

    单例与锁都是**模块级**的：`dependencies.py` 每次请求都会 new 一个服务，
    如果实例挂在服务上，锁就只锁得住自己那一把，等于没有锁。
    """

    name = "rapidocr"

    def __init__(self, instance: Any) -> None:
        self._instance = instance

    def recognize(self, image: Any) -> str:
        with _CALL_LOCK:
            result = self._instance(image)
        # 什么都没认出来时 `txts` 是 `None` 而不是空元组（本机实测），
        # 所以这里必须先判空再拼；直接 `join(None)` 会抛 TypeError 变成 500。
        texts = getattr(result, "txts", None)
        if not texts:
            return ""
        return "\n".join(str(line) for line in texts if str(line).strip())


def _build_engine() -> RapidOcrEngine:
    """真正构造 `RapidOCR`。单独一个函数是为了让测试把它换成假引擎而不碰锁逻辑。"""
    from rapidocr import RapidOCR  # 延迟 import：没走到第二级时不该付出这几十毫秒

    return RapidOcrEngine(RapidOCR())


def get_ocr_engine() -> RapidOcrEngine:
    """取全局单例（首次调用时建）。"""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = _build_engine()
        return _ENGINE


def render_pdf_pages(
    data: bytes, *, max_pages: int = MAX_PAGES, scale: float = OCR_RENDER_SCALE
) -> tuple[int, Sequence[Any]]:
    """把前 `max_pages` 页渲染成位图，返回 `(总页数, 位图序列)`。

    `page.close()` / `bitmap.close()` / `doc.close()` 都显式调用：pypdfium2 对没关的对象
    会打「still open and will be closed now」并交给垃圾回收，一条请求一次的话
    等于让原生内存随请求量漂移。
    """
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except Exception as error:
        # 这一层拿到的异常类型很杂（pypdfium 自己的 + 原生错误），统一折成「读不动」：
        # 对用户而言下一步动作与「不是 PDF」是同一件事，而原始消息里可能带临时路径。
        raise PdfUnreadableError("PDF 无法渲染成图像。") from error

    try:
        pages_total = len(document)
        pages_used = min(pages_total, max_pages)
        images: list[Any] = []
        for index in range(pages_used):
            page = document[index]
            try:
                bitmap = page.render(scale=scale)
                try:
                    images.append(bitmap.to_pil())
                finally:
                    bitmap.close()
            finally:
                page.close()
        return pages_total, images
    except PdfUnreadableError:
        raise
    except Exception as error:
        raise PdfUnreadableError("PDF 渲染失败。") from error
    finally:
        document.close()


class TextLayerThenOcrExtractor(PdfTextExtractor):
    """①文本层 → ②OCR 的两级抽取器（服务层看到的仍然是**一个** `PdfTextExtractor`）。

    做成同一个抽象的实现，而不是在服务层写 if/else：`ResumePdfImportService` 因此
    不需要知道有几级、也不需要拿到引擎——测试想验哪一级就注入哪一级的假件。
    """

    def __init__(
        self,
        text_extractor: PdfTextExtractor | None = None,
        engine: OcrEngine | None = None,
    ) -> None:
        self._text = text_extractor or PypdfTextExtractor()
        # 引擎也是懒的：构造服务时不该把模型拉起来。
        self._engine = engine

    def _ocr_engine(self) -> OcrEngine:
        if self._engine is not None:
            return self._engine
        engine = get_ocr_engine()
        self._engine = engine
        return engine

    def extract(self, data: bytes) -> PdfText:
        try:
            return self._text.extract(data)
        except PdfNoTextError:
            # 只有「有版面、没文字层」这一种失败才值得去认像素；
            # 「不是 PDF」「加密」走 OCR 也一样读不出，让它们直接落到 422。
            logger.info("PDF 导入：文字层为空，转 OCR 兜底")
            return self._extract_by_ocr(data)

    def _extract_by_ocr(self, data: bytes) -> PdfText:
        pages_total, images = render_pdf_pages(data)
        if not images:
            raise PdfUnreadableError("PDF 没有可渲染的页面。")

        engine = self._ocr_engine()
        lines: list[str] = []
        for image in images:
            lines.append(engine.recognize(image))
        text = "\n".join(line for line in lines if line).strip()
        if not text:
            raise PdfUnreadableError("OCR 也没认出任何文字。")

        logger.info(
            "PDF 导入：OCR 完成（pages=%s/%s, engine=%s, chars=%s）",
            len(images),
            pages_total,
            engine.name,
            len(text),
        )
        truncated = len(text) > MAX_TEXT_CHARS
        return PdfText(
            pages_total=pages_total,
            pages_used=len(images),
            text=text[:MAX_TEXT_CHARS],
            text_chars=min(len(text), MAX_TEXT_CHARS),
            truncated=truncated,
            source=SOURCE_OCR,
        )
