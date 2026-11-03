"""测试全局前置。

**关闭登录/注册限流**：T7.7 给 `/auth/register`、`/auth/login` 加了 5 次/分钟的
IP 限流，而认证用例会在同一分钟内反复注册+登录（TestClient 的 client.host 恒为
`testclient`，所有请求共用同一个限流键），跑不了几条就会自己撞上 429，
与「验证鉴权逻辑」这件事无关。

这里用环境变量在**导入 app 之前**关掉限流（`Settings` 的 env 优先级高于 `.env`）。
限流本身不是不测：`tests/test_rate_limit.py` 会在用例内临时打开限流，
断言第 6 次请求返回 429 且响应体符合 T6 统一错误模型。
"""

import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
