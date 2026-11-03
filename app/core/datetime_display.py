"""对外展示用的时间统一口径：东八区。

库里的 `created_at` / `updated_at` 是 naive UTC（MySQL DATETIME 不带时区，
写入时统一取 `datetime.now(UTC)` 的 naive 值）。直接把它当成 ISO 串返回，
前端 `new Date(...)` 会按**浏览器本地时区**解析，东八区用户就会看到比真实
时间早 8 小时的时刻。

因此所有返回给前端的时间字段都在这一层转成带 `+08:00` 的 ISO 串：

    2026-11-02T21:25:00+08:00

带偏移量的字符串是绝对时刻，前端无论怎么解析都不会再差 8 小时；
需要展示成本地格式时用 `Intl.DateTimeFormat`（见前端 `utils/datetime.ts`）。

`edu_start` / `edu_end` / `start_date` / `end_date` 是 DATE 列，本身不含时刻，
不存在时区问题，Pydantic 已按 `YYYY-MM-DD` 输出，这里不再改动。

同一个口径也用于「时间不能是未来」的校验基准（`shanghai_today`）：
若拿服务器本地 `date.today()` 当基准，部署在 UTC 机器上就会在
北京时间 08:00 之前把「今天」判成昨天，用户的今天被误拦。

反过来，**写入**库里的时间统一用 `utc_now()`（见下）：MySQL DATETIME 不带时区，
存 UTC 的 naive 值，读取时再由上面的函数转东八区展示。写入口径曾分散在
三个仓储文件里各写一遍，T7.7 收敛到本模块，避免三处实现悄悄漂移。
"""

from datetime import UTC, date, datetime, timedelta, timezone

#: 产品面向国内用户，展示时区固定东八区（不依赖服务器本地时区设置）。
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def to_shanghai(value: datetime) -> datetime:
    """把库里的 naive UTC 时间转成带东八区偏移的 aware 时间。

    对已经带时区的值只做换算，不重复补 UTC，避免二次偏移。
    """
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(SHANGHAI_TZ)


def format_shanghai_datetime(value: datetime) -> str:
    """`2026-11-02T21:25:00+08:00`：秒级精度，够展示且不带微秒噪声。"""
    return to_shanghai(value).isoformat(timespec="seconds")


def shanghai_today() -> date:
    """东八区的「今天」，作为「时间不能是未来」的判定基准。

    不依赖服务器本地时区：UTC 机器上 `date.today()` 在北京时间 00:00–08:00
    之间会比国内日期早一天，把用户合法选中的「今天」误判成未来。
    """
    return datetime.now(SHANGHAI_TZ).date()


def utc_now() -> datetime:
    """写入数据库用的「现在」：UTC 的 naive 时间。

    MySQL `DATETIME` 不带时区，所以库里一律存 UTC naive 值；
    返回给前端前由 `format_shanghai_datetime()` 转东八区。
    """
    return datetime.now(UTC).replace(tzinfo=None)
