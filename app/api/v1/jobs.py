"""岗位库接口（Phase 4 · B1 + C1，ADR-011 1.F / 2.A / 2.D / 2.F / 2.G / 议题 3.C）。

**路由顺序有讲究**：`/trash`、`/extract-skills` 这类静态段必须声明在 `/{job_id}` 之前，
否则 FastAPI 会把 `trash` / `extract-skills` 当成岗位 id 塞进路径参数，
表现为「回收站打不开，404」。同一个坑的既有记录见 `app/api/v1/profiles.py:3-5`。
（`extract-skills` 是 POST，而 `/{job_id}` 上只有 GET/PUT/DELETE，按今天这张路由表
理论上撞不到；仍然把它放在静态段一组里，是因为「撞不到」只取决于当前这张表有没有
一条 POST `/{job_id}`，而 `tests/test_jobs_extract_skills.py` 里的顺序断言
把这件事钉成了契约而不是运气。）

越权处理：不属于当前用户、且未发布的岗位一律 **404（不是 403）**——
403 会暴露「该 id 存在」（`app/api/v1/profiles.py:10` 的先例，理由见 ADR-011 1.F）。

身份只从 JWT 取（`get_current_user`）：`status` / `created_by_user_id` / `origin` /
三列提取溯源戳都不从请求体读，因此本文件没有任何一处把请求体的这些字段传给服务层。

`POST /jobs/extract-skills` 是**纯计算端点**（2.A）：它既不读库也不写库，
与 `/resumes/polish` 同构，因此同样挂 IP 限流（每分钟十次，装饰器顺序照
`app/api/v1/resumes.py:66-67`——`@router.post` 在上、`@limiter.limit` 紧贴函数定义）。
它的三个 AI 异常也不在这里 catch：`AI_KEY_MISSING` / `AI_KEY_INVALID` /
`AI_SERVICE_UNAVAILABLE` 由 `app/main.py` 的专用处理器统一成错误模型，
**唯一的例外**是第 7 情形（输出被服务端的 token 上限切断），它要多带一个
`meta.finish_reason`，所以就地 catch 并自己构造响应体——错误码仍是那三个之一。
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.errors import error_response
from app.core.rate_limit import (
    EXTRACT_RATE_LIMIT,
    MATCH_RATE_LIMIT,
    get_user_or_remote_address,
    limiter,
)
from app.core.responses import request_id, success_response
from app.schemas.auth import UserRead
from app.schemas.job import MAX_PAGE_SIZE, ExtractSkillsRequest, JobWriteRequest
from app.services.dependencies import get_current_user, get_job_service, get_match_service
from app.services.job_service import (
    JobNotFoundError,
    JobService,
    JobSkillExtractTruncatedError,
)
from app.services.match_service import MatchService

router = APIRouter(prefix="/jobs", tags=["jobs"])

JobServiceDep = Annotated[JobService, Depends(get_job_service)]
MatchServiceDep = Annotated[MatchService, Depends(get_match_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]

#: 1.F #1：列表默认只给已发布，自己的草稿要显式传 `status=draft`。
#: 用 `Literal` 而不是自由字符串：白名单外的值由 FastAPI 直接 422，
#: 不拿用户输入拼 SQL（与 `profiles.py` 的 `order_by` 同一做法）。
StatusFilter = Literal["draft", "published"]

#: 第 7 情形的用户动作（ADR-011 附录第 7 行）：与第 4 情形「重试同一请求」不同，
#: 这里必须是「少给点内容 / 少要点条目」，否则用户会把同一个 502 反复点出来。
#: 措辞里也**不能**出现「换 Key」「重新登录」——那两条分别属于第 2 情形与鉴权失败。
EXTRACT_TRUNCATED_MESSAGE = (
    "AI 输出的技能条目过多，被服务端的 token 上限切断了。"
    "请重试，并只粘贴 JD 的核心段落，或减少期望的条数。"
)


def _not_found() -> HTTPException:
    """越权与不存在共用同一个 404（T6 错误模型：404 的 message 由全局处理器统一）。"""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="岗位不存在。")


@router.get("")
def list_jobs(
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
    status: StatusFilter = "published",
    keyword: str | None = Query(default=None, max_length=200),
    include_expired: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> dict[str, Any]:
    """岗位列表（1.F #1）。

    筛选本轮只有 `keyword`（企业名/岗位名）+ `status`（议题 3.C）：
    地点、薪资、经验都**存而不筛**（1.G），字段已经在库里，加筛选只改读路径。

    过期岗位默认隐藏（2.G）：`deadline < 今天` 不出现在默认结果里，
    要一起看到必须显式传 `include_expired=true`；`deadline = 今天` 仍出现。
    """
    jobs, total = service.list_jobs(
        current_user.id,
        status=status,
        keyword=keyword,
        include_expired=include_expired,
        page=page,
        page_size=page_size,
    )
    return success_response(
        request,
        {"jobs": [job.model_dump(mode="json") for job in jobs]},
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobWriteRequest,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """录入岗位（1.F #3）。一律落 `draft`，发布是显式动作。"""
    job = service.create_job(current_user.id, payload)
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.post("/extract-skills")
@limiter.limit(EXTRACT_RATE_LIMIT)
def extract_job_skills(
    payload: ExtractSkillsRequest,
    request: Request,
    response: Response,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """AI 岗位 JD 技能提取（1.F #7、2.A）：**纯计算、不落库**。

    与 `/resumes/polish` 同构：JD 原文进来、候选条目回去，岗位此刻还没落库，
    用户逐条删改后点「保存」才由 `POST/PUT /jobs` 写两张表（草稿态确认制，ADR-010 §5）。
    所以本函数没有任何一处碰仓储——`tests/test_jobs_extract_skills.py` 用挂在
    **Engine** 上的 `before_cursor_execute` 把这句话钉成断言（附录指定的取证方式）。

    `current_user` 的依赖不能省：本端点消耗用户自己的 AI 额度，必须登录；
    返回值里也不含任何用户身份信息。

    第 7 情形（`finish_reason == "length"`）在这里就地转成响应体，因为统一错误模型的
    `meta` 只有 `request_id`，而 ADR 要求 `meta.finish_reason` 回显「是我们的输出
    token 上限把它切断了，不是模型不守格式」。其余 AI 失败（第 1/2/3/4/5/6 情形）
    不在这里 catch，全部交给 `app/main.py` 的三个处理器——**错误码一个都没新增**。

    状态码用 `response.status_code` 就地改（而不是 return 一个 `JSONResponse`）：
    这样响应体仍然只有一份构造路径、错误模型字段与全局处理器完全一致，
    而路由的返回类型也保持与本文件其它端点同形。
    """
    try:
        result = service.extract_skills(payload, request_id(request))
    except JobSkillExtractTruncatedError as error:
        body = error_response(
            request,
            "AI_SERVICE_UNAVAILABLE",
            EXTRACT_TRUNCATED_MESSAGE,
            retryable=True,
        )
        body["meta"]["finish_reason"] = error.finish_reason
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return body
    return success_response(request, result.model_dump(mode="json"))


@router.get("/trash")
def list_job_trash(
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """回收站（1.F #8）：只含**自己**软删除的岗位，过滤在 service/SQL 里做。

    声明位置必须在 `/{job_id}` 之前，见文件头注释。
    """
    jobs = service.list_trash(current_user.id)
    return success_response(
        request,
        {"jobs": [job.model_dump(mode="json") for job in jobs]},
    )


@router.get("/{job_id}")
def read_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """详情（1.F #2）：`published` 任何登录用户可读，他人 `draft` 404。"""
    try:
        job = service.get_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.post("/{job_id}/match")
@limiter.limit(MATCH_RATE_LIMIT, key_func=get_user_or_remote_address)
def match_job(
    job_id: str,
    request: Request,
    service: MatchServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """人岗匹配（ADR-013 §3）：**POST 而不是 GET**，它会向 DeepSeek 发起一次真实调用、
    花用户自己的额度，语义上是「执行一次计算」，与 `/jobs/extract-skills` 同族。

    三条与既有端点不同的地方，都在这个函数体里，不在服务层偷偷做：

    1. **AI 失败不往外抛**。`MatchService` 已经把它们翻译成 `degrade_reason` 并继续给分，
       所以本函数**没有** `except EvidenceScore*`，也没有第 7 情形那种就地构造响应体——
       匹配不像提取那样「一条都拿不到就没意义」。因此这三个 AI 处理器在本端点上永不命中
       （见 `app/main.py` 的那段注释）。
    2. **限流键是用户而不是 IP**（`get_user_or_remote_address`）：按 IP 会让同一出口下
       前两个人把整组人的额度点光，而后面的人没做错任何事。
    3. **不可见岗位 404 不是 403**：与 `read_job` 同一谓词、同一 `_not_found()`；
       403 会确认「该 id 存在」。

    请求体为空是刻意的（§3 决策 2）：v1 只匹配当前用户的**默认档案**，
    而用的是哪一份必须由响应里的 `profile_id` / `profile_title` 说出来，
    不能让用户自己去猜分数是谁的。
    """
    try:
        result = service.match(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, result.model_dump(mode="json"))


@router.get("/{job_id}/requirement-match")
def requirement_match_job(
    job_id: str,
    request: Request,
    service: MatchServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """第 3c 条（ADR-013 §10）：这条岗位的每条要求技能，当前用户处在哪个状态。

    它是**确定性、不调 AI、不产生分数、不落库**的读端点，存在的唯一理由是把
    「你已具备」的判据从前端搬到后端——前端那套 `trim + NFKC + toLowerCase` 认不出
    `Python3` 与 `Python` 是同一个技能，于是角标说「不具备」而匹配卡说「已具备」，
    同一屏两个口径（§1.3 那一屏自相矛盾的第二半）。

    四条刻意的选择：

    1. **GET 而不是 POST**：与 `/match` 相反，这里没有任何出网调用与额度消耗。
       路由表论证：本函数只调 `MatchService.requirement_match`，它不 import
       `app.integrations`、不发一次 HTTP 请求，所以 `X-DeepSeek-Key` 在这条 URL 上
       **没有转发对象**——它因此**不进** BYOK 白名单（`frontend/src/api/client.ts`），
       这条「不许加」由 `byok.spec.ts` 的反向用例钉住。
    2. **不限流**：纯读两张表、按岗位一次一个请求。给它加限额反而造出一种新故障
       （「角标时有时无」），而那种故障比它要防的脚本刷更难排查。
    3. **可见性与 `/match`、`GET /jobs/{job_id}` 同一谓词**（`get_readable`）：他人的 `draft`
       包括回收站里的都是同一个 404。三条读径口径不同会出现「详情读得到、角标读不到」，
       那比没有角标更糟。
    4. **档案语义与 `/match` 同一条**（默认档案，无默认退最新）：角标与分数必须按同一份简历说。

    顺序上没有 `/trash` 那种坑：这条是 `/{job_id}/requirement-match`，
    与 `/jobs/{job_id}` 的模板不同形（路径参数不跨 `/`），已注册路由闭合用例覆盖它。
    """
    try:
        result = service.requirement_match(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, result.model_dump(mode="json"))


@router.put("/{job_id}")
def update_job(
    job_id: str,
    payload: JobWriteRequest,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """整体覆盖（1.F #4，仅创建者）：字段 + 技能子表全量替换，`status` 不变。"""
    try:
        job = service.update_job(current_user.id, job_id, payload)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.put("/{job_id}/publish")
def publish_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """发布（1.F #6，仅创建者）：进入广场，任何登录用户可读。"""
    try:
        job = service.publish_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.put("/{job_id}/unpublish")
def unpublish_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """下架回草稿（1.F #6，仅创建者）：广场立刻读不到，仅创建者可见。"""
    try:
        job = service.unpublish_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """软删除（1.F #5，仅创建者）：进回收站，子表一行不动。"""
    try:
        service.delete_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job_id": job_id, "deleted": True})


@router.put("/{job_id}/restore")
def restore_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """从回收站恢复（1.F #9，仅创建者）：`deleted_at` 清回 `NULL`，`status` 原样保留。

    写法与响应形状对齐 `app/api/v1/profiles.py:221` 的 `PUT /{profile_id}/restore`——
    回收站没有恢复入口就是半个功能（ADR-011 2.A 草图第 9 行 v6 才补上）。
    非创建者、以及「岗位存在但不在回收站」都返回 404：与 1.F 同口径，
    不给「存在但无权」或「存在但没被删」留信息通道。
    """
    try:
        job = service.restore_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})
