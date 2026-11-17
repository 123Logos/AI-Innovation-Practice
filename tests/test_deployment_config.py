"""部署配置的耦合闸（第 32 轮）。

`ADR-008` 那句话没变：**开发主机没装 Docker，这些文件从未真机跑过**。所以本模块不
去证明「`up` 能起来」——那要一台真有 Docker 的机器，第一次上线仍然可能要修。

它证明的是另一件事：这五处配置都不是孤立字符串，而是**另一端代码的镜像**。改一端忘了
另一端，这里必须红。每条断言都写成耦合，而不是「文档里有没有某个词」：

1. `docker/nginx.conf` 的 `proxy_pass` 主机名，必须在两份编排里都解析得到服务；
   生产那一份靠的是 `web` 上的网络别名。别名丢了不会报错，只会所有 `/api/` 变 502，
   而 `docker compose ps` 全绿——最难猜的那类故障。
2. `TZ` 在 `x-app-env` 这块共享环境里，web 与两个 worker 都读得到；`db` 读不到
   （库里时间统一 naive UTC，MySQL 留在 UTC 才对得上 `utc_now()`）。
3. `Dockerfile.backend` COPY 的两份纸面样式，必须就是
   `app/core/teacher_tools_rules.py` 运行时拼出来的那两个路径。
4. 任务卷的容器路径同时出现在三处：编排的挂载、镜像的降权步骤、README 的赋权命令，
   且 UID 就是镜像创建的那个 `appuser`。
5. worker 不继承 HTTP 健康检查、重启只给 10 秒；`web` 的健康检查留着，因为
   `frontend` 靠 `service_healthy` 等它。

10 秒宽限的代价（在途任务落成 `TASK_LOST`）不在这里验——那是
`tests/test_tasks.py::test_lost_job_becomes_failed_and_stays_failed` 的活，
本文件只保证配置与它说的一致。

依赖说明：解析 YAML 用的 `pyyaml` **不是本项目声明的直接依赖**，它由直接依赖
`rapidocr`（→ `omegaconf`）与 `uvicorn[standard]` 带进来，并被 `uv.lock` 钉在 6.0.3。
把它提升为显式 dev 依赖是 §4.6 的 ADR 事件，本轮不擅自加；如果它哪天从传递链上掉下去，
这个文件会在收集阶段就报 `ModuleNotFoundError`——不会静默跳过，也就不会发假绿证书。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core import teacher_tools_rules as paper
from app.main import app
from app.services.resume_import import MAX_PDF_BYTES

ROOT = Path(__file__).resolve().parents[1]
PROD_COMPOSE = ROOT / "docker-compose.prod.yml"
DEV_COMPOSE = ROOT / "docker-compose.yml"
NGINX_CONF = ROOT / "docker" / "nginx.conf"
BACKEND_DOCKERFILE = ROOT / "Dockerfile.backend"
DOCKERIGNORE = ROOT / ".dockerignore"
README = ROOT / "README.md"

#: 两份编排。负责人第 32 轮裁定「不能留一个同样会踩坑的开发版」，所以三件加固项
#: （`TZ` / 禁用 worker 的 HTTP 健康检查 / 10 秒宽限）**两个文件都要过**。
COMPOSE_FILES = {"prod": PROD_COMPOSE, "dev": DEV_COMPOSE}
#: 读同一份应用环境的容器（生产故意不起 `worker-default`，那条队列今天没有任务注册）。
APP_CONTAINERS = {
    "prod": ("web", "worker-ocr", "worker-pdf"),
    "dev": ("backend", "worker-ocr", "worker-pdf", "worker-default"),
}
#: 后台队列：不监听 HTTP，且重启只给 10 秒。
QUEUE_WORKERS = {
    "prod": ("worker-ocr", "worker-pdf"),
    "dev": ("worker-ocr", "worker-pdf", "worker-default"),
}
#: 提供 HTTP 的那个服务（`frontend` 的 depends_on 等的就是它的健康检查）。
API_SERVICE = {"prod": "web", "dev": "backend"}

APP_CONTAINER_CASES = [(label, name) for label, names in APP_CONTAINERS.items() for name in names]
QUEUE_WORKER_CASES = [(label, name) for label, names in QUEUE_WORKERS.items() for name in names]
TOPOLOGY_CASES = list(COMPOSE_FILES)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(_text(path))


def _services(label: str) -> dict[str, Any]:
    return _load(COMPOSE_FILES[label])["services"]


def _prod_services() -> dict[str, Any]:
    return _services("prod")


def _network_aliases(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks")
    if not isinstance(networks, dict):
        return set()
    aliases: set[str] = set()
    for cfg in networks.values():
        if isinstance(cfg, dict):
            aliases.update(cfg.get("aliases") or ())
    return aliases


def _nginx_upstream_host() -> str:
    match = re.search(r"proxy_pass\s+http://([^:/;\s]+):(\d+)", _text(NGINX_CONF))
    assert match is not None, "nginx.conf 里找不到 proxy_pass http://<host>:<port>"
    return match.group(1)


def _paper_paths() -> tuple[PurePosixPath, ...]:
    """运行时真正会去读的那两份源码，相对仓库根的 posix 路径。"""
    return tuple(
        PurePosixPath(p.relative_to(paper.PROJECT_ROOT).as_posix())
        for p in (paper.GLOBAL_CSS, paper.PREVIEW_VUE)
    )


def _task_volume_names() -> set[str]:
    """编排里挂在任务目录上的命名卷（不含 `mysql-data` / `redis-data`）。"""
    declared = set(_load(PROD_COMPOSE)["volumes"])
    names: set[str] = set()
    for item in _prod_services()["web"].get("volumes") or ():
        source = item.partition(":")[0]
        if source in declared:
            names.add(source)
    return names


def _task_volume_paths(label: str = "prod") -> set[str]:
    """那些卷在容器里的挂载点，例如 `/app/temp`。"""
    paths: set[str] = set()
    for name in APP_CONTAINERS[label]:
        for item in _services(label)[name].get("volumes") or ():
            _, _, target = item.partition(":")
            paths.add(target)
    return paths


# ------------------------------------------------------------------ 1. nginx


def test_nginx_caps_the_upload_at_the_app_limit_and_falls_back_to_the_spa() -> None:
    """`client_max_body_size` 与 history 模式的 `try_files` 都还在，且上限不早于应用自己。"""
    text = _text(NGINX_CONF)

    limit = re.search(r"^\s*client_max_body_size\s+(\d+)([km])\s*;", text, re.MULTILINE)
    assert limit is not None, (
        "nginx.conf 没有 client_max_body_size：上传退回默认 1 MB，"
        "导入 PDF 会以 413 失败在 nginx 那一层，FastAPI 的校验根本轮不到跑"
    )
    cap = int(limit.group(1)) * (1024 if limit.group(2) == "k" else 1024 * 1024)
    assert cap >= MAX_PDF_BYTES, (
        f"nginx 只放 {cap} 字节，而应用允许 {MAX_PDF_BYTES}："
        "边界上的 PDF 会在 nginx 那里被吞掉，报错和重试口径都对不上"
    )

    assert re.search(r"^\s*try_files\s+\$uri\s+\$uri/\s+/index\.html\s*;", text, re.MULTILINE), (
        "nginx.conf 没有 try_files 回落：vue-router 是 history 模式，"
        "刷新 /resumes/123 这类深链接会直接 404"
    )


def test_nginx_upstream_resolves_in_both_compose_topologies() -> None:
    """`proxy_pass` 的主机名必须在两份编排里都指得到后端——生产靠 `web` 的别名。"""
    host = _nginx_upstream_host()

    for label, path in (("生产", PROD_COMPOSE), ("开发", DEV_COMPOSE)):
        services = _load(path)["services"]
        if host in services:
            continue
        aliased = {name for name, svc in services.items() if host in _network_aliases(svc)}
        assert aliased, (
            f"{label}编排里没有名为 {host} 的服务，也没有任何服务给默认网络挂 "
            f"{host} 别名：nginx 会解析不到上游，所有 /api/ 请求 502，"
            "而 docker compose ps 全绿、直连 8000 也是 200"
        )


def test_backend_healthcheck_points_at_a_url_the_app_actually_answers() -> None:
    """镜像的 HEALTHCHECK 打的 URL 必须真的回 200，否则探针永远红、永远没人看它。"""
    match = re.search(r"urlopen\('http://127\.0\.0\.1:8000([^']+)'", _text(BACKEND_DOCKERFILE))
    assert match is not None, "Dockerfile.backend 里没有打到 127.0.0.1:8000 的健康检查"
    probed = match.group(1)

    # 走 TestClient 而不是翻 app.routes：`/api/v1` 是挂进来的，路由表里没有平铺的全路径。
    response = TestClient(app).get(probed)
    assert response.status_code == 200, f"健康检查打的是 {probed}，实际得到 {response.status_code}"


# ------------------------------------------------------------------------ 2. 时区


def test_timezone_lives_in_the_shared_env_block() -> None:
    """生产的 TZ 写在 `x-app-env` 里，而不是抄在某个服务上：web 与 worker 必须同一个口径。"""
    assert _load(PROD_COMPOSE)["x-app-env"]["TZ"] == "Asia/Shanghai"


@pytest.mark.parametrize("label,name", APP_CONTAINER_CASES)
def test_app_containers_run_on_the_product_timezone(label: str, name: str) -> None:
    """两份编排里的每一个应用容器都拿到东八区（开发形态第 32 轮补齐，不留双标）。

    修的是没被 `shanghai_today()` 覆盖的那批 `date.today()` 调用（岗位过期判定、匹配、
    简历服务）：UTC 机器在北京时间 00:00–08:00 之间「今天」还是昨天，那天该下线的
    岗位会多挂 8 小时。审计与导出的时间基准也走同一个口径。
    """
    assert _services(label)[name]["environment"]["TZ"] == "Asia/Shanghai"


@pytest.mark.parametrize("label", TOPOLOGY_CASES)
def test_database_container_stays_on_utc(label: str) -> None:
    """`db` 不能有 TZ：库里时间统一是 naive UTC（`utc_now()`），MySQL 跟着 UTC 才对得上。"""
    assert "TZ" not in _services(label)["db"]["environment"]


def test_the_image_can_actually_resolve_the_timezone_it_is_told() -> None:
    """`TZ: Asia/Shanghai` 只有 glibc 查得到 `/usr/share/zoneinfo` 才作数。

    这台机器没装 Docker，Docker Hub 也不可达（`auth.docker.io` 连接超时），所以
    「底座 `python:3.13-slim` 到底带不带 zoneinfo」本机**核实不了**。能核实的是
    bookworm 的 Packages 索引：tzdata 是 `Priority: required`，下载 0.3 MB、
    Installed-Size 2.5 MB（连同 Depends 共 2 个包、0.4 MB / 3.0 MB）——底座很可能已经带。
    这一条断言钉的是「镜像显式装了它」，不是「底座没有」：把一个可能静默回落 UTC 的
    失败换成百 KB 级的确定性，代价与收益都写在 Dockerfile 的注释里。
    """
    tz = _load(PROD_COMPOSE)["x-app-env"]["TZ"]
    assert tz == "Asia/Shanghai"

    assert re.search(r"apt-get install[^\n]*\btzdata\b", _text(BACKEND_DOCKERFILE)), (
        f"编排要求 TZ={tz}，但 Dockerfile.backend 没有显式装 tzdata："
        "这行 TZ 能不能生效就变成看底座脸色"
    )


def test_the_timezone_data_is_not_baked_only_into_the_pdf_browser_branch() -> None:
    """tzdata 必须在**无条件**那一步：`INSTALL_PDF_BROWSER=1` 只有 `worker-pdf` 传。

    装进那条 `if` 里，web 与 worker-ocr 用的是同一份 Dockerfile 的另一支，这层保险
    就只剩 worker-pdf 有——三条容器读同一个 `TZ`，却只有一条保证解析得动。
    （第 32 轮初稿就把它写进了 `if` 里，所以单独钉一条。）
    """
    text = _text(BACKEND_DOCKERFILE)
    branch = re.search(
        r'^RUN if \[ "\$INSTALL_PDF_BROWSER" = "1" \].*?^\s*fi', text, re.MULTILINE | re.DOTALL
    )
    assert branch is not None, "Dockerfile.backend 里找不到那个按 INSTALL_PDF_BROWSER 分支的 RUN"
    assert "tzdata" not in branch.group(0), (
        "tzdata 被装进了只给 worker-pdf 的分支：web 与 worker-ocr 拿不到这层保险"
    )

    unconditional = text[: branch.start()] + text[branch.end() :]
    assert re.search(r"apt-get install[^\n]*\btzdata\b", unconditional), "tzdata 不在无条件那一步里"


def test_the_backend_image_never_lets_apt_pull_in_recommends() -> None:
    """`--no-install-recommends` 一条都不能少。

    按 bookworm 的 Packages 索引算闭包（Depends，或项取第一个候选）：
    `chromium fonts-noto-cjk fontconfig tzdata` = 230 个包，下载 256 MB /
    Installed-Size 820 MB；放进 recommends = 466 个包，333 MB / 1151 MB。
    多出来的 331 MB **不是** GTK 那一类——libgtk / libgbm / libcups2 是 chromium 的
    硬 Depends，装浏览器就躲不掉——而是 Qt5 整套、perl、samba-libs、udev、cpp-12、
    python3.11 这些与「把页面打成 PDF」无关的东西。
    """
    installs = re.findall(r"apt-get install[^\n]*", _text(BACKEND_DOCKERFILE))
    assert installs, "Dockerfile.backend 里没有 apt-get install"

    bare = [line for line in installs if "--no-install-recommends" not in line]
    assert not bare, f"这几条 apt-get install 没带 --no-install-recommends：{bare}"


# ------------------------------------------------------------------ 3. 纸面样式


def test_backend_image_copies_the_paper_styles_the_runtime_reads() -> None:
    """镜像里那两份 COPY 的源路径，必须就是运行时拼出来的那两个——改名要两边一起改。"""
    text = _text(BACKEND_DOCKERFILE)
    workdir = re.search(r"^WORKDIR\s+(\S+)\s*$", text, re.MULTILINE)
    assert workdir is not None, "Dockerfile.backend 没有 WORKDIR，COPY 的落点无法推断"

    for rel in _paper_paths():
        copy = re.search(rf"^COPY\s+{re.escape(rel.as_posix())}\s+(\S+)\s*$", text, re.MULTILINE)
        assert copy is not None, (
            f"Dockerfile.backend 没有 COPY {rel}：镜像里读不到它，"
            "教师端导出与看板会以 PaperStylesUnavailableError 失败，而不是换一套后端自抄的排版"
        )
        # 落点 = WORKDIR + 目标目录（docker 语义），必须还原出运行时那个路径
        assert copy.group(1) == f"./{rel.parent}/"
        assert (ROOT / rel.as_posix()).is_file(), (
            f"{rel} 在仓库里不存在，构建会在 COPY 这一步直接失败"
        )


# ------------------------------------------------------------------ 4. 卷与权限


def test_backend_image_grants_the_run_user_write_access_before_dropping_root() -> None:
    """降权之前建目录并 chown：`task_files` 只会 mkdir，不会补权限。"""
    text = _text(BACKEND_DOCKERFILE)
    assert "RUN useradd" in text and "USER appuser" in text

    start, end = text.index("RUN useradd"), text.index("USER appuser")
    assert start < end, (
        "chown 发生在 USER appuser 之后：那一行以 appuser 身份跑，改不动 root 的目录"
    )
    step = text[start:end]

    uid = re.search(r"--uid (\d+)", step)
    assert uid is not None, "useradd 没有显式 --uid：README 里的赋权命令就无法与镜像对齐"

    mounts = _task_volume_paths()
    mkdir = re.search(r"mkdir -p([^\n&]*)", step)
    assert mkdir is not None, (
        "镜像里没有 mkdir：卷挂载点不存在时 Docker 不会把属主带进新卷，"
        "appuser 随后写盘就是 PermissionError"
    )
    created = set(mkdir.group(1).split())
    for path in sorted(mounts):
        assert any(item == path or item.startswith(f"{path}/") for item in created), (
            f"编排把卷挂在 {path}，镜像的 mkdir 里没有它（只有 {sorted(created)}）"
        )

    chown = re.search(r"chown -R appuser:appuser([^\n&]*)", step)
    assert chown is not None, "镜像里没有 chown -R appuser:appuser"
    owned = set(chown.group(1).split())
    assert mounts <= owned, (
        f"编排挂载的 {sorted(mounts - owned)} 没有交给 appuser，写盘就是 PermissionError"
    )


def test_readme_volume_section_matches_the_compose_mounts_and_the_image_uid() -> None:
    """README 的赋权说明不能自己一套数字：卷名、容器路径、UID 全部来自编排与镜像。"""
    readme = _text(README)
    heading = re.search(r"^### \d+\. 卷权限.*$", readme, re.MULTILINE)
    assert heading is not None, "README 的生产部署一节没有「卷权限」小节"
    tail = re.search(r"^### \d+\. ", readme[heading.end() :], re.MULTILINE)
    assert tail is not None
    section = readme[heading.start() : heading.end() + tail.start()]

    uid = re.search(r"--uid (\d+)", _text(BACKEND_DOCKERFILE)).group(1)
    chown = re.search(r"chown (\d+):(\d+)([^\n]*)", section)
    assert chown is not None, (
        f"README 没给出对已存在卷的 chown 命令（应为 chown {uid}:{uid} <挂载点>）"
    )
    assert chown.group(1) == chown.group(2) == uid, (
        f"README 里的属主 {chown.group(1)}:{chown.group(2)} 与镜像的 appuser uid={uid} 不一致"
    )
    assert set(chown.group(3).replace('"', "").split()) == _task_volume_paths(), (
        "README 的 chown 目标与编排的挂载点不是同一组目录"
    )

    for volume in sorted(_task_volume_names()):
        # 标题行必须点名这两条卷：只在下文某处提一次不算「说明到位」，
        # 运维照着目录找小节时看到的就是这一行。
        assert volume in heading.group(0), f"README 的卷权限小节标题没有提到 {volume} 卷"


# ------------------------------------------------------------------ 5. worker


@pytest.mark.parametrize("label,name", QUEUE_WORKER_CASES)
def test_queue_workers_do_not_inherit_the_http_healthcheck(label: str, name: str) -> None:
    """worker 不监听 8000，沿用镜像那条检查会让它们在 `ps` 里永远挂着 unhealthy。"""
    assert _services(label)[name].get("healthcheck") == {"disable": True}, (
        f"{label}/{name} 没有 disable 掉镜像的 HTTP 健康检查：它会一直显示 unhealthy，"
        "而那条状态与「worker 是不是在消费任务」毫无关系"
    )


@pytest.mark.parametrize("label", TOPOLOGY_CASES)
def test_api_service_keeps_its_http_healthcheck_because_the_frontend_waits_on_it(
    label: str,
) -> None:
    """只有那个真会 HTTP 的服务保留健康检查，`frontend` 等的是它。"""
    services = _services(label)
    api = API_SERVICE[label]

    assert "healthcheck" not in services[api], (
        f"{label} 的 {api} 覆盖了镜像的 HEALTHCHECK：frontend 的 depends_on 是 "
        "service_healthy，没有健康检查它就永远等不到，界面起不来"
    )
    assert services["frontend"]["depends_on"][api]["condition"] == "service_healthy"
    assert re.search(r"^HEALTHCHECK\b", _text(BACKEND_DOCKERFILE), re.MULTILINE), (
        "Dockerfile.backend 不再有 HEALTHCHECK，API 服务就没有 readiness 信号了"
    )


@pytest.mark.parametrize("label,name", QUEUE_WORKER_CASES)
def test_queue_workers_restart_within_ten_seconds(label: str, name: str) -> None:
    """10 秒是「重启要快」的裁定；被硬杀的在途任务由轮询收敛成 TASK_LOST，不会假装成功。"""
    assert _services(label)[name]["stop_grace_period"] == "10s"


def test_both_topologies_mount_the_task_volumes_on_the_same_paths() -> None:
    """开发形态与生产形态必须挂同样的容器路径：镜像里那一步 chown 只认一套路径。

    路径分叉的表现不是「开发能跑生产不能」，而是**两边都写不进去**：
    `Dockerfile.backend` 只在降权前 chown 了 `/app/temp` 与 `/app/downloads`。
    """
    assert _task_volume_paths("prod") == _task_volume_paths("dev")


def test_dockerignore_does_not_starve_the_paper_styles_copy() -> None:
    """.dockerignore 里只要有一条排到 frontend/src，构建就在 COPY 那一步直接失败。"""
    patterns = [
        line.strip()
        for line in _text(DOCKERIGNORE).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    for rel in _paper_paths():
        blocked = {str(parent) for parent in rel.parents} | {rel.as_posix()}
        hits = sorted(
            {
                pattern
                for pattern in patterns
                if pattern in blocked or pattern == f"*.{rel.suffix.lstrip('.')}"
            }
        )
        assert not hits, f"{rel} 会被 .dockerignore 的 {hits} 排除，镜像里就没有纸面样式了"
