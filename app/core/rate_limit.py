"""登录 / 注册接口限流（slowapi）。

**为什么 limiter 实例放在 core 而不是直接在 `app/main.py` 里 new**：
`app/api/v1/auth.py` 要用它当装饰器，而 `main.py` 会 import 路由聚合器——
实例写在 `main.py` 里就构成 `main → api.router → api.v1.auth → main` 的循环导入，
应用根本起不来。因此这里只负责**构造**实例，真正的「初始化」动作仍留在
`main.py`：挂到 `app.state.limiter`、注册 429 异常处理器（转成 T6 统一错误模型）。

限流键默认取客户端 IP（`get_remote_address`），不依赖登录态——注册/登录本来就没有身份。
**一处例外**：`MATCH_RATE_LIMIT` 走 `get_user_or_remote_address`（按用户），理由见那条函数的 docstring。
存储用 slowapi 默认的进程内内存计数：单进程 MVP 够用；多 worker 部署时
每个 worker 各算一份，届时需要换 Redis 存储（`storage_uri`），属于后续任务。
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import get_settings

#: 登录 / 注册的限流规则；认证相关测试也引用这个常量，避免两处硬编码漂移。
AUTH_RATE_LIMIT = "5/minute"

#: AI 润色接口的限流规则（ADR-010）。
#: 这条限流保护的不是我们的服务器，而是**用户自己的钱包**：`/resumes/polish` 每次调用都
#: 花调用者自带 Key 的额度，一个失控的前端循环、一个脚本化的爬虫都能在短时间内烧掉大量 Token。
#: 10 次/分钟够用一次正常的「润色→不满意→换档位再来」，同时把滥用成本抬到不划算。
#: 测试引用这个常量，理由与 `AUTH_RATE_LIMIT` 相同。
POLISH_RATE_LIMIT = "10/minute"

#: 岗位 JD 技能提取的限流规则（ADR-011 2.F），与 `POLISH_RATE_LIMIT` 同族同数。
#: 为什么必须是这个数量级：`/jobs/extract-skills` 和润色一样烧**用户自带 Key** 的额度，
#: 而请求是我们这台服务器转发给第三方的 —— 不限流就等于把服务器开成一个免费的
#: DeepSeek 代理（ADR-010 §2 的原始理由在这里一字不改地成立）。
#: 10 次/分钟够用一次真实的「粘 JD → 提取 → 结果不理想，手动删两句 JD → 再提取」；
#: 提取的重试次数天然比润色高（用户会挪 JD 的段落看哪一段能被核验），所以不往下压。
#: 已知局限与前两条相同：slowapi 是进程内计数，多 worker 部署时每个 worker 各算一份。
#: 测试引用这个常量，理由与 `AUTH_RATE_LIMIT` 相同。
EXTRACT_RATE_LIMIT = "10/minute"

#: 人岗匹配的限流规则（ADR-013 §3）。数量级与前两条一致，**但键不一样**（见下面那条函数）。
MATCH_RATE_LIMIT = "10/minute"


def get_user_or_remote_address(request: Request) -> str:
    """匹配端点的限流键：能认出用户就按用户，认不出就退回 IP。

    为什么这一条例外必须存在（ADR-013 §13 第 1 条）：本端点每次点击都花**调用者自己**的
    DeepSeek 额度，而按 IP 计数会让同一个出口 IP 上的人互相抢额度。局域网测试那批
    5 人共用一个出口是必然场景，按 IP 的结果是「前两个人点光，后面的人拿到 429」——
    被限的人既没做错什么，也没花自己的钱。

    为什么不直接把 `limiter` 的默认 `key_func` 换掉：那会同时改掉登录、润色、提取三条规则的
    语义，而登录恰恰**必须**按 IP（未登录时没有用户可认，且防的就是同一 IP 撞库）。
    所以这里是「装饰器级覆盖」，另三条一个字不动。

    `request.state.rate_limit_user_id` 由 `app/services/dependencies.py::get_current_user`
    写入。它是否真的在限流之前被写上是**测出来的**，不是假设的：
    `tests/test_job_match.py` 里有一条「同一 IP 下两个用户互不挤占」的用例，
    那条用例红就说明键退化回了 IP，届时不要改测试，要改这里的次序。
    """
    user_id = getattr(request.state, "rate_limit_user_id", None)
    if isinstance(user_id, str) and user_id:
        return f"user:{user_id}"
    return get_remote_address(request)


def build_limiter() -> Limiter:
    """按配置构造限流器；`enabled=False` 时装饰器直接放行（测试用）。"""
    settings = get_settings()
    return Limiter(
        key_func=get_remote_address,
        # 不在响应头里回写 X-RateLimit-*：那需要给每个路由补 `response` 参数，
        # 而我们只需要「超限时 429 + Retry-After」，由异常处理器统一给。
        headers_enabled=False,
        enabled=settings.rate_limit_enabled,
    )


limiter = build_limiter()
