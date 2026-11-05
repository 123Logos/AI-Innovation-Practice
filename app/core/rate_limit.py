"""登录 / 注册接口限流（slowapi）。

**为什么 limiter 实例放在 core 而不是直接在 `app/main.py` 里 new**：
`app/api/v1/auth.py` 要用它当装饰器，而 `main.py` 会 import 路由聚合器——
实例写在 `main.py` 里就构成 `main → api.router → api.v1.auth → main` 的循环导入，
应用根本起不来。因此这里只负责**构造**实例，真正的「初始化」动作仍留在
`main.py`：挂到 `app.state.limiter`、注册 429 异常处理器（转成 T6 统一错误模型）。

限流键取客户端 IP（`get_remote_address`），不依赖登录态——注册/登录本来就没有身份。
存储用 slowapi 默认的进程内内存计数：单进程 MVP 够用；多 worker 部署时
每个 worker 各算一份，届时需要换 Redis 存储（`storage_uri`），属于后续任务。
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

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
