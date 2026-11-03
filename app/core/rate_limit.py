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
