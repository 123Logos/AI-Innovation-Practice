from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

JWT_SECRET_MIN_LENGTH = 32
DEVELOPMENT_JWT_SECRET_KEY = "development-only-insecure-jwt-secret-key-do-not-use"


class Settings(BaseSettings):
    app_name: str = "ai-career-platform"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    environment: str = "development"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:5173"
    allowed_origins: str = "http://localhost:5173"
    enable_api_docs: bool = True
    #: 登录/注册限流开关。默认开启；测试（`tests/conftest.py`）通过环境变量关掉，
    #: 否则同一分钟内反复注册/登录的用例会自己撞上 429。
    rate_limit_enabled: bool = True
    database_url: str | None = None
    #: 第 30 轮（`ADR-033`）：异步任务队列与任务记录的 Redis 连接串。
    #: **默认 None = 任务功能不可用**（两个入队端点 503 `TASK_QUEUE_UNAVAILABLE`），
    #: 绝不静默回落成「内存队列」——那种回落会在多 worker 部署时表现为「任务凭空消失」。
    #: `production` 下缺失直接启动失败（见 `validate_production_dependencies`）。
    redis_url: str | None = None
    #: 三项并发上限（老板第 30 轮点名）。每项各管一条队列，理由见 `ADR-033` §2.2：
    #: - OCR 吃 CPU 且共用一个引擎，4 约等于「8 核留一半给 web」；
    #: - PDF 每条 job 起一个无头浏览器子进程，2 是「不 OOM」与「两个老师同时导出」之间；
    #: - default 是纯 I/O，与 `AI_MAX_CONCURRENCY` 同量级。
    #: 三格都带 `ge=1, le=64`：写 0 会让 worker 一个 job 都不取（任务永远 pending），
    #: 写 10000 会让 OOM 变成必然——两者都在启动时就该失败，而不是留到运行期。
    worker_ocr_max_jobs: int = Field(default=4, ge=1, le=64)
    worker_pdf_max_jobs: int = Field(default=2, ge=1, le=64)
    worker_default_max_jobs: int = Field(default=10, ge=1, le=64)
    #: 单任务超时。导入 6–40 秒、50 份导出约 100–300 秒，900 秒是「真的卡住了」而不是「慢」。
    #: 它同时是 BYOK Key 密文在 Redis 里的 TTL（`ADR-033` §2.4）：任务超时之后
    #: 那把 Key 就算没被读走也已经作废。
    worker_job_timeout_seconds: int = Field(default=900, ge=60, le=86400)
    #: 任务记录（状态 + 结果）保留时长：用户刷新页面/换设备后仍能取到结果。
    worker_task_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    #: 上传暂存与导出产物目录。两个都在仓库根、都进 `.gitignore`；
    #: 多机部署不成立（本地盘），这条写在 `ADR-033` §7 第 5 条。
    worker_import_dir: str = "temp/imports"
    worker_download_dir: str = "downloads"
    #: 批次 B10b（`ADR-029` §2.5）：PDF 导出用的无头 Chromium / Edge 可执行文件。
    #: 这是**系统级工具**不是 Python 依赖，所以不放进 `dependencies`；没配时按候选路径找，
    #: 找不到就明确报 `PDF_RENDERER_UNAVAILABLE`，绝不假装生成成功。
    pdf_browser_path: str | None = None
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=15, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_production_database_url(self) -> "Settings":
        if self.environment == "production" and not self.database_url:
            raise ValueError("database_url is required when environment is production")
        return self

    @model_validator(mode="after")
    def validate_production_redis_url(self) -> "Settings":
        """生产必须有 Redis：`ADR-033` 之后导入与批量导出两条功能挂在队列上。

        与 `database_url` 同一条纪律（缺必填配置启动即失败）：生产环境跑起来却没有队列，
        表现为「用户点导入 → 503」或「任务永远 pending」，两者都不该等到上线才发现。
        """
        if self.environment == "production" and not self.redis_url:
            raise ValueError("redis_url is required when environment is production")
        return self

    @model_validator(mode="after")
    def validate_jwt_secret_key(self) -> "Settings":
        secret_key = self.jwt_secret_key
        if self.environment == "production":
            if not secret_key:
                raise ValueError("jwt_secret_key is required when environment is production")
            if len(secret_key) < JWT_SECRET_MIN_LENGTH:
                raise ValueError(
                    "jwt_secret_key must be at least "
                    f"{JWT_SECRET_MIN_LENGTH} characters when environment is production"
                )
            return self
        if not secret_key:
            self.jwt_secret_key = DEVELOPMENT_JWT_SECRET_KEY
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def jwt_expire_seconds(self) -> int:
        return self.jwt_expire_minutes * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
