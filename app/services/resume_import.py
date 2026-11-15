"""外部 PDF 简历导入服务（第 28 轮，`ADR-031`）。

**这是一条纯计算路径**：字节进来、草稿回去，本文件一次都没有碰仓储——
与 `/resumes/polish`（`ADR-010` §5）和 `/jobs/extract-skills`（`ADR-011` 2.A）同族。
理由不是「保守」，而是 `ADR-031` 2.1 的那条：AI 把教育经历写成三段假的，
只要它没落库，用户点一次不保存就完全回到原点；一旦落库，回退点就没了。

分工：

| 层 | 干什么 | 不干什么 |
|---|---|---|
| `integrations/pdf_text.py` | 字节 → 文本（3 页 / 12000 字两封顶） | 不知道前端上限，不知道哪格必填 |
| `integrations/resume_structurer.py` | 文本 → 模型给的那个对象 | 不裁剪、不判合法性 |
| **本文件** | 归一、裁剪、丢弃计数、把五种失败翻译成一个 422 加一个码 | 不读写库 |

**为什么归一在这里**（2.4）：`educations.edu_start` 是库里 NOT NULL、`degree` 有 CHECK、
`contact` 必须是大陆 11 位、技能 8 条经历 3 条教育 5 条——这些全是**写侧**的规则，
把它们塞进适配器就等于让最底层去猜上层的契约（`AGENTS.md` §4.1 的依赖方向）。

五个码各给一句修得动的话（2.5），且**刻意不降级成模板**：生成简历可以纯模板兜底，
导入不行——兜底等于凭空造一份「看起来填好了」的档案，而它一个字都没读过那份 PDF。
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import ClassVar

from pydantic import ValidationError

from app.core.datetime_display import shanghai_today
from app.core.error_messages import ErrorMessage
from app.core.skill_names import skill_name_key
from app.integrations.pdf_text import PdfTextExtractor, PdfUnreadableError
from app.integrations.resume_structurer import (
    ResumeStructurer,
    StructureAuthError,
    StructuredEducation,
    StructuredExperience,
    StructuredProfile,
    StructuredSkill,
    StructureLimits,
    StructureOutputError,
    StructureUnavailableError,
)
from app.schemas.profile import (
    MAX_PROFILE_EDUCATIONS,
    MAX_PROFILE_EXPERIENCES,
    MAX_PROFILE_SKILLS,
    PHONE_PATTERN,
    Degree,
    EducationInput,
    ExperienceInput,
    ProfileUpsertRequest,
    SkillInput,
)
from app.schemas.resume import clamp_to_polish_contract
from app.schemas.resume_import import PdfImportDropped, PdfImportMeta, PdfImportResult

logger = logging.getLogger(__name__)

#: 单份 PDF 的体积上限（2.3 第一条封顶，老板指定 5MB）。
#: 检查点在路由侧：那里是流式累计读到超限就中止，不会先把 20 MB 收进内存。
MAX_PDF_BYTES = 5 * 1024 * 1024

#: 「仍在进行」的写法。抽到这些一律折成 `None`（= 库里「至今」那一条口径）。
_OPEN_ENDED = frozenset({"至今", "现在", "目前", "至今-", "present", "now", "ongoing", "current"})

#: 学历的同义写法 → 库里 CHECK 允许的四个值。
#: **只做确有把握的映射**：`学士` 就是本科、`大专` 就是专科；`高中`、`中专`、`MBA`
#: 这些库里装不下的学历不猜、不折算，直接丢这一条并计入 `dropped.educations`
#: ——把「高中」写成「专科」是伪造事实，比少一段经历严重得多。
_DEGREE_ALIASES: dict[str, Degree] = {
    "专科": "专科",
    "大专": "专科",
    "高职": "专科",
    "本科": "本科",
    "学士": "本科",
    "大学本科": "本科",
    "本科毕业": "本科",
    "硕士": "硕士",
    "研究生": "硕士",
    "硕士研究生": "硕士",
    "硕士毕业": "硕士",
    "博士": "博士",
    "博士研究生": "博士",
    "博士毕业": "博士",
    # 英文简历上的写法：`bachelor` 与 `master` 这几个词没有歧义，也一并收进来。
    # key 一律按 `casefold()` 之后的形状写，因为查表前会先折一次。
    "bachelor": "本科",
    "bachelor's degree": "本科",
    "undergraduate": "本科",
    "master": "硕士",
    "master's degree": "硕士",
    "postgraduate": "硕士",
    "doctor": "博士",
    "doctorate": "博士",
    "phd": "博士",
    "associate degree": "专科",
    "college": "专科",
}

#: 年 / 月 / 日三段：月与日都可以缺（`2021`、`2021-09`、`2021年9月`、`2021.9.15` 都要能吃下）。
#: 分隔符允许 0–3 个非数字字符，所以 `202109` 这种粘连写法也能折出「2021 年 09 月」。
_DATE_RE = re.compile(r"(\d{4})(?:\D{0,3}(\d{1,2})(?:\D{0,3}(\d{1,2}))?)?")


class PdfImportError(Exception):
    """导入失败的基类：`code` 是给前端的分叉依据，`message` 是受信目录里的句子。

    文案**一律来自 `ErrorMessage`**，不拼 `str(上游异常)`（G3 的既有纪律）：
    pypdf 与 openai 的异常文本里可能有对象名、状态码，甚至用户自己的 Key。
    """

    code: ClassVar[str] = "PDF_INVALID"
    message: ClassVar[ErrorMessage] = ErrorMessage.PDF_INVALID
    retryable: ClassVar[bool] = False


class PdfTooLargeError(PdfImportError):
    """超过 `MAX_PDF_BYTES`：换小一点的文件。"""

    code = "PDF_TOO_LARGE"
    message = ErrorMessage.PDF_TOO_LARGE


class PdfInvalidError(PdfImportError):
    """魔数不对、结构坏了、加密打不开，或一页文字都抽不出来（扫描版/图片版）。

    这四种原因对外同一句：用户能做的动作是同一个——换一份有文字层的 PDF，或手动填。
    区分开来只会让他去猜「扫描件」和「加密」哪个才是真原因。
    """

    code = "PDF_INVALID"
    message = ErrorMessage.PDF_INVALID


class PdfKeyMissingError(PdfImportError):
    """没带 Key，或 Key 被上游拒绝（2.5 第 2 条：都归这个码，文案仍指向设置页）。"""

    code = "AI_KEY_MISSING"
    message = ErrorMessage.AI_KEY_MISSING


class PdfAiUnavailableError(PdfImportError):
    """超时 / 上游 4xx-5xx / 空返回：稍后再试一次。"""

    code = "AI_SERVICE_UNAVAILABLE"
    message = ErrorMessage.AI_UNAVAILABLE
    retryable = True


class PdfAiOutputInvalidError(PdfImportError):
    """输出不是合法 JSON、结构不符，或被我们自己的 token 上限切断。"""

    code = "AI_OUTPUT_INVALID"
    message = ErrorMessage.AI_OUTPUT_INVALID
    retryable = True


def _collapse(value: object, max_length: int) -> str:
    """压平成单行文本并裁到上限。

    PDF 抽出来的文字到处是硬换行（一行版心就断一次），不压平的话「中国计量\n大学」
    会带着换行进表单。`max_length` 与写侧各字段的长度上限同源，
    超了裁而不是丢——裁短是可编辑，丢掉是看不见。
    """
    if isinstance(value, str):
        text = value
    elif value is None:
        return ""
    else:
        text = str(value)
    return " ".join(text.split())[:max_length]


def parse_partial_date(value: object, *, is_end: bool = False) -> date | None:
    """把模型给的日期写成 `date`，解析不出就 `None`（调用方决定丢弃还是留空）。

    接受的形状：`2021-09`、`2021/9`、`2021.09`、`2021年9月`、`202109`、`2021-09-15`、
    裸年份 `2021`、数字 `2021`。「至今 / present」这类进行态折成 `None`，
    与库里 `end_date IS NULL` 的语义对齐。

    只有年份时补哪一天是**有方向**的：开始补 1 月 1 日、结束补 12 月 31 日——
    简历上「2020 至今」本来就该覆盖整年，补年中反而会把「2020-06 入职」这样的
    同年开始判成 `end < start` 而整条丢掉。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    if not text or text.casefold() in _OPEN_ENDED:
        return None

    number = _coerce_year_only(text)
    if number is not None:
        return date(number, 12, 31) if is_end else date(number, 1, 1)

    match = _DATE_RE.search(text)
    if match is None:
        return None
    year = int(match.group(1))
    raw_month, raw_day = match.group(2), match.group(3)
    month = _to_int(raw_month, low=1, high=12)
    day = _to_int(raw_day, low=1, high=31)
    if (raw_month is not None and month is None) or (raw_day is not None and day is None):
        # 看见了「一个像月份 / 日的数字」但越界（`2024-13`、`2024-06-99`）：
        # 判成「读不出日期」而不是退化成「那年的一月」——后者是替用户编一个月他没写。
        return None
    try:
        if month is None:
            return date(year, 12, 31) if is_end else date(year, 1, 1)
        return date(year, month, day or 1)
    except ValueError:
        # 「2024-02-31」这样的日子不存在，`0000` 这样的年份越界——两者都是模型会从 PDF 里
        # 原样抄出来的形状。不接住它就是一颗 ValueError 穿出服务层变成 500，
        # 而正确的回答是「这一条我们读不出日期」，由调用方丢弃并计数。
        return None


def _coerce_year_only(text: str) -> int | None:
    digits = "".join(char for char in text if char.isdigit())
    if not (4 <= len(digits) <= 6):
        return None
    if len(digits) in (5, 6):
        # 5–6 位交给正则处理（`202109` 是年月，`20219` 也是），这里只接纯 4 位年份。
        return None
    year = int(digits)
    return year if 1900 <= year <= 2100 else None


def _to_int(raw: str | None, *, low: int, high: int) -> int | None:
    if raw is None:
        return None
    value = int(raw)
    return value if low <= value <= high else None


def normalize_degree(value: object) -> Degree | None:
    """把模型写的学历收成库里 CHECK 允许的四个值；收不进来返回 `None`（调用方丢这条）。"""
    text = _collapse(value, 20)
    if not text:
        return None
    folded = text.casefold()
    return _DEGREE_ALIASES.get(folded) or _DEGREE_ALIASES.get(text)


def _normalize_contact(value: object) -> tuple[str, bool]:
    """手机号只留数字；不是大陆 11 位则**置空**并回报「丢了一个非法值」。

    为什么置空而不是原样带回去让 PUT 去 422：`ADR-031` 2.4 的口径是
    「后端会拒的值不该由 AI 造出来」。表单里留一个 `+86 138-0013-8000` 之类的串，
    用户点保存才吃一句「手机号格式不正确」，比导入时就告诉他「这份 PDF 里的号码不是
    大陆手机号」难懂得多。

    `86` 前缀要先摘掉：PDF 里 `+86 138 0013 8000` 与 `13800138000` 是同一个号码，
    把前者判成非法值等于把一份填得好好的手机号丢进「已丢弃」——那是我们读得懂、
    但没照抄的错误。摘掉前缀后仍按同一个 `PHONE_PATTERN` 判，不放宽一位。
    """
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return "", False
    if len(digits) > 11 and digits.startswith("86") and PHONE_PATTERN.fullmatch(digits[2:]):
        return digits[2:], False
    if PHONE_PATTERN.fullmatch(digits):
        return digits, False
    return "", True


def _normalize_skills(items: list[StructuredSkill], limit: int) -> tuple[list[SkillInput], int]:
    """技能：压平、按 `skill_name_key` 去重、裁到 `limit`。返回条目与被丢弃的条数。

    判定键必须是 `skill_name_key`（NFKC + casefold + 去空白），不能是裸相等：
    库里 `uq_skills_profile_name` 建在大小写不敏感的排序规则上，
    `Python` 与 `python` 在这里放过就是一次 1062 → 500（`ADR-011` §2.D.2 的同一个坑）。
    """
    kept: list[SkillInput] = []
    seen: set[str] = set()
    dropped = 0
    for item in items:
        name = _collapse(item.name, 100)
        if not name:
            # 空白条目不计入 dropped：那是模型给的噪音，不是用户简历里被丢掉的内容。
            continue
        key = skill_name_key(name)
        if not key or key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(SkillInput(name=name))
    if len(kept) > limit:
        dropped += len(kept) - limit
        kept = kept[:limit]
    return kept, dropped


def _normalize_experiences(
    items: list[StructuredExperience], limit: int
) -> tuple[list[ExperienceInput], int, bool]:
    """经历（`experiences` 与 `projects` 合并后进来）：锚点、日期、文案三段各有一道闸。

    丢整条的四种情形都对应「PUT 会拒」：标题为空（`title` 是这段经历存在的锚点）、
    开始日期解析不出（库里 NOT NULL）、开始日期在未来、结束早于开始。
    留下但截断的是描述与职责，与润色共用 `clamp_to_polish_contract` 那一对数字。
    """
    kept: list[ExperienceInput] = []
    dropped = 0
    clamped = False
    today = shanghai_today()
    for item in items:
        title = _collapse(item.title, 200)
        start = parse_partial_date(item.start_date)
        if not title or start is None or start > today:
            dropped += 1
            continue
        end = parse_partial_date(item.end_date, is_end=True)
        if end is not None and end < start:
            dropped += 1
            continue
        description, responsibility, was_clamped = clamp_to_polish_contract(
            _collapse(item.description, 4000), _collapse(item.responsibility, 4000)
        )
        clamped = clamped or was_clamped
        kept.append(
            ExperienceInput(
                title=title,
                start_date=start,
                end_date=end,
                description=description,
                responsibility=responsibility,
            )
        )
    if len(kept) > limit:
        dropped += len(kept) - limit
        kept = kept[:limit]
    return kept, dropped, clamped


def _normalize_educations(
    items: list[StructuredEducation], limit: int
) -> tuple[list[EducationInput], int]:
    """教育经历：`school` + `edu_start` + `degree` 三格都齐才留（库里三列都是硬要求）。"""
    kept: list[EducationInput] = []
    dropped = 0
    today = shanghai_today()
    for item in items:
        school = _collapse(item.school, 200)
        start = parse_partial_date(item.edu_start)
        degree = normalize_degree(item.degree)
        if not school or start is None or start > today or degree is None:
            dropped += 1
            continue
        end = parse_partial_date(item.edu_end, is_end=True)
        if end is not None and end < start:
            dropped += 1
            continue
        kept.append(
            EducationInput(
                school=school,
                major=_collapse(item.major, 100),
                degree=degree,
                edu_start=start,
                edu_end=end,
            )
        )
    if len(kept) > limit:
        dropped += len(kept) - limit
        kept = kept[:limit]
    return kept, dropped


def normalize_profile(raw: StructuredProfile) -> tuple[ProfileUpsertRequest, PdfImportDropped]:
    """模型给的对象 → 能直接 PUT 的档案草稿 + 一张「丢了什么」的账。

    `projects` 并入 `experiences`、经历在前项目在后（2.4）：前端没有独立的「项目」区块，
    纸面上就是「项目经历」那一节；加一格等于加一个区块。
    """
    skills, dropped_skills = _normalize_skills(raw.skills, MAX_PROFILE_SKILLS)
    contact, contact_discarded = _normalize_contact(raw.contact)
    experiences, dropped_experiences, text_clamped = _normalize_experiences(
        [*raw.experiences, *raw.projects], MAX_PROFILE_EXPERIENCES
    )
    educations, dropped_educations = _normalize_educations(raw.educations, MAX_PROFILE_EDUCATIONS)

    profile = ProfileUpsertRequest(
        full_name=_collapse(raw.full_name, 100),
        contact=contact,
        target_role=_collapse(raw.target_role, 200) or None,
        skills=skills,
        experiences=experiences,
        educations=educations,
    )
    dropped = PdfImportDropped(
        skills=dropped_skills,
        experiences=dropped_experiences,
        educations=dropped_educations,
        contact_discarded=contact_discarded,
        text_clamped=text_clamped,
    )
    return profile, dropped


def _profile_from_model(raw: StructuredProfile) -> tuple[ProfileUpsertRequest, PdfImportDropped]:
    """`normalize_profile` 的「失败也要有码」版本。

    归一规则（日期、学历、手机号、上限）本来就照着写侧契约写，所以这里的
    `ValidationError` 正常走不到；它挡的是「模型给了一条我们没预料到的形状」——
    那种情况该回一句「AI 没能整理成可用的档案，请再试一次或手动填」，
    而不是 500 加一个 request_id 让用户去找维护人员。
    """
    try:
        return normalize_profile(raw)
    except ValidationError as error:
        logger.warning(
            "PDF 导入：归一后的档案仍不合写侧契约（fields=%s）",
            sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]}),
        )
        raise PdfAiOutputInvalidError from error


class ResumePdfImportService:
    """装配两个适配器，把「一份 PDF」翻译成「一份可编辑的档案草稿」。

    不接仓储、不接 session：本端点没有任何一条写路径（`ADR-031` 2.1）。
    Key 缺失时 `structurer` 就是 `None`（与 `get_job_service` 同一装配法），
    翻译成的码由本类决定，而不是让依赖装配层去猜业务。
    """

    def __init__(
        self,
        pdf_extractor: PdfTextExtractor,
        structurer: ResumeStructurer | None,
    ) -> None:
        self._pdf = pdf_extractor
        self._structurer = structurer

    def import_profile(self, data: bytes) -> PdfImportResult:
        """字节 → 结果。失败时抛 `PdfImportError` 的某个子类，由 `main.py` 统一成 422。"""
        # Key 的判断放在读文件之前：它最便宜，而且没 Key 的时候抽再多页也是白抽。
        if self._structurer is None:
            raise PdfKeyMissingError

        try:
            extracted = self._pdf.extract(data)
        except PdfUnreadableError as error:
            # 只记异常类型，不把 PDF 正文或 pypdf 的对象名带进日志。
            logger.info("PDF 导入：文件读不出文字（reason=%s）", type(error).__name__)
            raise PdfInvalidError from error

        limits = StructureLimits(
            skills=MAX_PROFILE_SKILLS,
            experiences=MAX_PROFILE_EXPERIENCES,
            educations=MAX_PROFILE_EDUCATIONS,
        )
        try:
            raw = self._structurer.structure(extracted.text, limits)
        except StructureAuthError as error:
            # 上游拒绝这把 Key：用户能修的只有那一格，所以仍归 `AI_KEY_MISSING`（2.5 第 2 条）。
            logger.info("PDF 导入：DeepSeek 拒绝该 Key")
            raise PdfKeyMissingError from error
        except StructureUnavailableError as error:
            logger.warning("PDF 导入：AI 服务不可用（reason=%s）", type(error).__name__)
            raise PdfAiUnavailableError from error
        except StructureOutputError as error:
            logger.warning("PDF 导入：AI 输出不合契约（reason=%s）", type(error).__name__)
            raise PdfAiOutputInvalidError from error

        profile, dropped = _profile_from_model(raw)
        logger.info(
            "PDF 导入完成（pages=%s/%s, chars=%s, truncated=%s, prompt=%s, dropped=%s）",
            extracted.pages_used,
            extracted.pages_total,
            extracted.text_chars,
            extracted.truncated,
            self._structurer.prompt_version,
            (dropped.skills, dropped.experiences, dropped.educations),
        )
        return PdfImportResult(
            profile=profile,
            meta=PdfImportMeta(
                pages_total=extracted.pages_total,
                pages_used=extracted.pages_used,
                text_chars=extracted.text_chars,
                text_truncated=extracted.truncated,
                prompt_version=self._structurer.prompt_version,
                dropped=dropped,
            ),
        )
