"""异步任务的文件落点与暂存盘（第 30 轮，`ADR-033` §2.5）。

两个目录，各自只有一个职责：

| 目录 | 谁写 | 谁删 | 盘上文件名 |
|---|---|---|---|
| `WORKER_IMPORT_DIR`（默认 `temp/imports`） | 上传端点 | worker 任务结束时（`finally`） | `<task_id>.pdf` |
| `WORKER_DOWNLOAD_DIR`（默认 `downloads`） | worker | 下载端点发一次就删；worker 启动时兜底扫一遍 | `<task_id>.zip` |

**盘上文件名永远由 task_id 决定，绝不用用户的文件名**：`<task_id>` 是服务端生成的
32 位十六进制，路径里因此不可能出现 `..`、`/`、`\\`、控制字符或超长名字——
这条比任何「消毒函数」都硬，因为它**没有输入**。用户看到的下载文件名（`resumes_….zip`）
存在任务记录里，只在 `Content-Disposition` 里出现，从不参与拼路径。

为什么落盘而不把字节塞进 Redis job：5 MB 的 value 进 Redis 会让一次导入在 AOF 里写两遍，
而 Redis 不是文件系统（`ADR-033` §2.5 第 2 条）。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from app.core.config import get_settings

#: 任务 id 的形状（`uuid4().hex`）。**所有**拼路径的地方都先过它：
#: 这是本模块唯一的安全边界，写宽一格就等于把路径交给调用方。
TASK_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

IMPORT_SUFFIX = ".pdf"
DOWNLOAD_SUFFIX = ".zip"


class InvalidTaskIdError(ValueError):
    """任务 id 形状不对：属于程序错误（api 层已经用 `max_length` 挡过一次）。"""


def is_valid_task_id(task_id: str) -> bool:
    return bool(TASK_ID_PATTERN.fullmatch(task_id))


def _checked(task_id: str) -> str:
    if not is_valid_task_id(task_id):
        raise InvalidTaskIdError(task_id)
    return task_id


def _root(configured: str) -> Path:
    """相对路径按仓库根解析。

    为什么不按进程 cwd：worker 由 `arq` 启动、web 由 `uvicorn` 启动，两者的 cwd 可能不同
    （`docs/DEVELOPMENT.md` 里是两条命令），而「上传写到 A、worker 去 B 找」
    会表现为「文件不存在」这种最难查的错。
    """
    path = Path(configured)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def import_dir() -> Path:
    return _root(get_settings().worker_import_dir)


def download_dir() -> Path:
    return _root(get_settings().worker_download_dir)


def _ensure(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_import_file(task_id: str, data: bytes) -> Path:
    """把上传的字节落到暂存盘，返回路径。调用方保证 `data` 已过体积上限。"""
    path = _ensure(import_dir()) / f"{_checked(task_id)}{IMPORT_SUFFIX}"
    path.write_bytes(data)
    return path


def import_file_path(task_id: str) -> Path:
    return import_dir() / f"{_checked(task_id)}{IMPORT_SUFFIX}"


def read_import_file(task_id: str) -> bytes:
    """读回上传的文件；已被删掉时抛 `FileNotFoundError`（由任务层翻成 `TASK_EXPIRED`）。"""
    return import_file_path(task_id).read_bytes()


def discard_import_file(task_id: str) -> None:
    """删除暂存文件，**不抛**：任务失败路径上再抛一次只会盖掉真正的错误。"""
    if not is_valid_task_id(task_id):
        return
    try:
        import_file_path(task_id).unlink()
    except FileNotFoundError:
        pass


def save_download_file(task_id: str, data: bytes) -> Path:
    """把产物落到下载目录（盘上名 `<task_id>.zip`）。"""
    path = _ensure(download_dir()) / f"{_checked(task_id)}{DOWNLOAD_SUFFIX}"
    path.write_bytes(data)
    return path


def download_file_path(task_id: str) -> Path:
    return download_dir() / f"{_checked(task_id)}{DOWNLOAD_SUFFIX}"


def discard_download_file(task_id: str) -> None:
    if not is_valid_task_id(task_id):
        return
    try:
        download_file_path(task_id).unlink()
    except FileNotFoundError:
        pass


def clear_stale_downloads(max_age_seconds: int) -> int:
    """删掉**已经过期**的产物，返回删掉的个数。

    **只在 PDF worker 启动时调**（`app/worker.py` 的 `make_on_startup`），而且是按
    mtime 判过期，不是把目录清空：

    - 「清空」会误删**正在跑的另一条任务**刚写好的产物——三个 worker 共用同一个目录，
      OCR worker 重启一次就把老师的 ZIP 删了，用户点下载拿到 410；
    - 按 mtime 判能且只能删掉「上一轮遗留」的东西（超过 `WORKER_TASK_TTL_SECONDS`
      已经不可能被合法取走了），这正是本函数的目的。

    不做定时清理的理由见 `ADR-033` §7 第 3 条：定时任务要自己的隔离口径。
    """
    directory = download_dir()
    if not directory.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in directory.glob(f"*{DOWNLOAD_SUFFIX}"):
        try:
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed
