"""BYOK 的 Key 跨越进程边界时的加密信封（第 30 轮，`ADR-033` §2.4）。

`ADR-009` 的口径是「Key 只来自请求头、只在本次请求的内存中、不落库、不写日志」。
异步化之后有一条硬事实：**worker 是另一个进程**，用户不会在 worker 里再发一次 Key。
要么放弃异步，要么让 Key 跨进程走一次——本文是那「一次」的最小代价。

**三道闸，缺一条就不该用这个模块**：

1. **不是明文**。落进 Redis 的是 Fernet 密文（AES-128-CBC + HMAC-SHA256，`cryptography`
   是既有依赖，不新增包），密钥由 `jwt_secret_key` 经 HKDF（SHA-256）派生。
   拿到 Redis 快照而没有服务端密钥的人，拿到的是密文。
2. **不是长存**。`SETEX` 的 TTL = `WORKER_JOB_TIMEOUT_SECONDS`，且 worker **读到即删**
   （`GETDEL`），不是任务结束才删。
3. **不进日志、不进响应、不进任务记录、不进 ARQ job 的 args**。本模块唯一的出口是
   `decrypt_secret()`，它返回明文的那个变量由调用方立刻塞进适配器构造函数。

**换 JWT 密钥等于作废所有在途任务的 Key**（派生自它）：这是可接受的——换密钥本身
就是一次强制下线，在途任务拿到 `AI_KEY_MISSING` 让用户重试，比留一把用不了的 Key 好。
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretDecryptError(Exception):
    """密文读不回来（换过 JWT 密钥 / 被截断 / 不是我们写的）。消息里不带任何密文内容。"""


def _fernet_key(secret: str) -> bytes:
    """`jwt_secret_key` → Fernet 要求的 32 字节 urlsafe base64 密钥。

    用 SHA-256 而不是直接截断：JWT 密钥是任意长度的字符串，截断会把 32 位以上的
    熵直接丢掉；`sha256` 之后 base64 是 Fernet 唯一接受的形状。
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def _fernet_for(secret: str) -> Fernet:
    return Fernet(_fernet_key(secret))


def encrypt_secret(value: str) -> str:
    """明文 Key → 可放进 Redis 的密文（ASCII 串）。空值调用方不该走到这里。"""
    secret = get_settings().jwt_secret_key or ""
    return _fernet_for(secret).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    secret = get_settings().jwt_secret_key or ""
    try:
        return _fernet_for(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as error:
        # 原始异常里可能带 token 片段，一个字都不往外带。
        raise SecretDecryptError("任务凭据无法解密") from error
