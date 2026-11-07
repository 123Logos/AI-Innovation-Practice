"""内部测试岗位种子脚本（ADR-013 §9，人类负责人指令 6.1）。

为什么要有这个脚本，而不是我手动点五次「新建岗位」：
岗位广场一旦被清理（11-07 就把两条探针岗位下架过），匹配功能与下一轮真实用户测试都会
退化到「打开是空白页」。脚本让这个状态可以一条命令恢复，也能重放验收。

三条刻意的设计：

1. **走真实 HTTP 路径**。用 `TestClient` 打 `POST /jobs` + `PUT /jobs/{id}/publish`，
   而不是裸 SQL 插库——绕开 `JobWriteRequest`/`JobSkillInput` 的校验就等于用假数据骗自己，
   而且 `origin` 徽标本来就无法手工声明（见第 2 条）。

2. **不声明 `origin`，也不声明 `confidence`**。`origin` 由服务端用
   `is_traceable(evidence_text, description)` 派生；`confidence` 的语义是「模型自报的把握」，
   而这里没有任何模型参与，填了就是造假。所以每条技能要么带一段**确实出现在 JD 正文里**的
   `evidence_text`（→ `jd_verifiable`），要么不带（→ `unverified`，代表「加分项」）。
   `evidence_text ⊂ description` 由本脚本断言，不靠我肉眼抄对。

3. **JD 文本全部自己写**，只用市场调研得到的技能词与行情区间；不逐字复制任何招聘站点，
   不写真实公司名（一律「某…」）。真实 JD 里高频出现的「2027 届」「本科及以上」「无挂科」
   「限男性」「35 岁以下」等表述**一律不写**——前者是年龄的代理变量，后面几条是 PRD `:200`
   禁止进入排序依据的敏感属性。内部测试数据如果带着歧视性筛选条件，匹配功能会在
   「照真实市场写」的名义下把它学会。

⚠️ 账号性质：`seedboard_a` / `seedboard_b` 是**内部测试账号**，不计入真实用户统计；
下一轮真实用户测试前，这些岗位要下架、账号要停用（沿用 preflight_* / nx_* 那批的处置口径）。
密码随机生成，落在 `.dev-secrets/`（已 gitignore），本脚本与任何提交都不含明文。
"""

from __future__ import annotations

import json
import secrets
import string
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# 让 `uv run python scripts/seed_internal_jobs.py` 在任意机器上一条跑通：直接执行脚本时
# sys.path[0] 是 scripts/，`app` 包不在上面。用 -m 或 PYTHONPATH 都能避开这行，
# 但那会让「恢复岗位广场」变成一条需要记住环境变量的命令——脚本存在的意义就是不用记。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app

ACCOUNTS_FILE = Path(".dev-secrets") / "job-seed-accounts.json"
USERNAME_PREFIX = "seedboard_"

# (账号, 岗位) —— 两个账号各挂几份，避免整个广场看起来是同一个人贴的公告板。
SEED_PLAN: list[tuple[str, dict[str, Any]]] = [
    (
        "seedboard_a",
        {
            "company_name": "某 AIGC 创业公司",
            "title": "大模型算法实习生",
            "location": "北京",
            "employment_type": "internship",
            "salary_min": Decimal(4500),
            "salary_max": Decimal(6000),
            "experience_required": "每周到岗 4 天，可连续实习 3 个月以上",
            "deadline": "+20d",
            "description": (
                "岗位职责：\n"
                "1. 参与大模型在业务场景中的数据构建与清洗，整理训练与评测数据集；\n"
                "2. 协助完成监督微调与偏好优化实验，记录指标变化并复盘；\n"
                "3. 搭建 RAG 检索增强链路，优化召回与排序效果；\n"
                "4. 参与模型效果评测与 badcase 分析，提出改进方案；\n"
                "5. 跟踪相关论文并做复现验证。\n"
                "任职要求：\n"
                "1. 计算机、软件工程或人工智能相关专业，Python 基础扎实，掌握常用数据结构与算法；\n"
                "2. 熟悉 PyTorch，做过完整的深度学习项目；\n"
                "3. 理解 Transformer 结构与大模型训练流程，有 Prompt 工程实践或效果调优经历；\n"
                "4. 熟悉 Linux 开发环境，熟练使用 Git 协作，能独立定位环境问题；\n"
                "5. 能阅读英文文献与技术资料，并写成可交接的技术文档；\n"
                "6. 有下列经历之一者优先：模型微调（如 LoRA）、Agent 与工具调用、"
                "向量数据库检索、论文复现、SQL 取数。"
            ),
            "skills": [
                ("Python", "hard", "Python 基础扎实，掌握常用数据结构与算法"),
                ("数据结构", "hard", "Python 基础扎实，掌握常用数据结构与算法"),
                ("PyTorch", "hard", "熟悉 PyTorch，做过完整的深度学习项目"),
                ("Transformer", "hard", "理解 Transformer 结构与大模型训练流程"),
                ("Prompt 工程", "hard", "有 Prompt 工程实践或效果调优经历"),
                ("RAG", "domain", "搭建 RAG 检索增强链路，优化召回与排序效果"),
                ("Linux", "hard", "熟悉 Linux 开发环境，熟练使用 Git 协作"),
                ("Git", "hard", "熟练使用 Git 协作，能独立定位环境问题"),
                ("技术文档", "soft", "并写成可交接的技术文档"),
                ("英文文献阅读", "soft", "能阅读英文文献与技术资料"),
                ("模型微调", "hard", None),
                ("Agent", "hard", None),
                ("向量数据库", "hard", None),
                ("SQL", "hard", None),
            ],
        },
    ),
    (
        "seedboard_a",
        {
            "company_name": "某支付平台",
            "title": "Python 后端开发工程师",
            "location": "深圳",
            "employment_type": "full_time",
            "salary_min": Decimal(9000),
            "salary_max": Decimal(13000),
            "experience_required": "可全职到岗，接受一年内转正目标",
            "deadline": "+45d",
            "description": (
                "岗位职责：\n"
                "1. 参与支付相关后端服务的接口设计与开发，使用 FastAPI 或 Django 实现业务模块；\n"
                "2. 负责 MySQL 表结构设计与数据访问层实现，保证慢查询可定位；\n"
                "3. 配合完成前后端接口联调、灰度发布与线上问题修复；\n"
                "4. 编写单元测试（pytest）并维护模块技术文档；\n"
                "5. 参与服务监控与告警配置。\n"
                "任职要求：\n"
                "1. 计算机相关专业，Python 基础扎实，熟悉常用数据结构与算法；\n"
                "2. 掌握 SQL，能独立完成多表关联查询与索引分析；\n"
                "3. 熟悉 Linux 常用命令与 Git 协作流程；\n"
                "4. 了解 Docker 与容器化部署，或 Redis 等缓存中间件；\n"
                "5. 有完整课程项目、开源贡献或实习经历者优先。"
            ),
            "skills": [
                ("Python", "hard", "Python 基础扎实，熟悉常用数据结构与算法"),
                ("FastAPI", "hard", "使用 FastAPI 或 Django 实现业务模块"),
                ("Django", "hard", "使用 FastAPI 或 Django 实现业务模块"),
                ("MySQL", "hard", "负责 MySQL 表结构设计与数据访问层实现"),
                ("SQL", "hard", "掌握 SQL，能独立完成多表关联查询与索引分析"),
                ("Linux", "hard", "熟悉 Linux 常用命令与 Git 协作流程"),
                ("Git", "hard", "熟悉 Linux 常用命令与 Git 协作流程"),
                ("pytest", "hard", "编写单元测试（pytest）并维护模块技术文档"),
                ("Docker", "hard", "了解 Docker 与容器化部署"),
                ("Redis", "hard", "或 Redis 等缓存中间件"),
                ("数据结构", "hard", "Python 基础扎实，熟悉常用数据结构与算法"),
                ("RESTful API 设计", "hard", None),
                ("消息队列", "hard", None),
            ],
        },
    ),
    (
        "seedboard_a",
        {
            "company_name": "某教育科技公司",
            "title": "前端开发实习生",
            "location": "杭州",
            "employment_type": "internship",
            "salary_min": Decimal(3000),
            "salary_max": Decimal(4500),
            "experience_required": "每周到岗 4 天，实习 3 个月以上",
            "deadline": None,  # 故意的：时效项要能测到「未知」这一档，不是所有岗位都带截止时间
            "description": (
                "岗位职责：\n"
                "1. 按设计稿完成页面重构与组件开发，主要技术栈是 Vue 3 与 TypeScript；\n"
                "2. 与后端联调 REST 接口，处理加载、空态与错误提示等异常状态；\n"
                "3. 优化移动端与桌面端的适配体验，保证窄屏下的可读性；\n"
                "4. 参与前端工程化维护，包括 Vite 构建配置与依赖升级；\n"
                "5. 修复缺陷并补充组件使用文档。\n"
                "任职要求：\n"
                "1. 计算机相关专业，掌握 HTML、CSS 与 JavaScript；\n"
                "2. 熟悉 Vue 3 的组合式 API，做过至少一个完整页面项目；\n"
                "3. 使用 Git 进行分支协作，了解代码评审流程；\n"
                "4. 了解 Element Plus 或 Ant Design 等组件库；\n"
                "5. 有校园项目、开源或个人作品集者优先。"
            ),
            "skills": [
                ("HTML", "hard", "掌握 HTML、CSS 与 JavaScript"),
                ("CSS", "hard", "掌握 HTML、CSS 与 JavaScript"),
                ("JavaScript", "hard", "掌握 HTML、CSS 与 JavaScript"),
                ("Vue 3", "hard", "主要技术栈是 Vue 3 与 TypeScript"),
                ("TypeScript", "hard", "主要技术栈是 Vue 3 与 TypeScript"),
                ("Git", "hard", "使用 Git 进行分支协作，了解代码评审流程"),
                ("Vite", "hard", "包括 Vite 构建配置与依赖升级"),
                ("Element Plus", "hard", "了解 Element Plus 或 Ant Design 等组件库"),
                ("REST 接口", "hard", "与后端联调 REST 接口，处理加载、空态与错误提示等异常状态"),
                ("响应式适配", "soft", "优化移动端与桌面端的适配体验"),
                ("React", "hard", None),
                ("小程序", "hard", None),
            ],
        },
    ),
    (
        "seedboard_b",
        {
            "company_name": "某出行平台",
            "title": "数据分析实习生",
            "location": "上海",
            "employment_type": "internship",
            "salary_min": Decimal(3000),
            "salary_max": Decimal(4000),
            "experience_required": "每周到岗 3 天，可连续实习 3 个月以上",
            "deadline": "+5d",  # 故意的：紧迫档
            "description": (
                "岗位职责：\n"
                "1. 按业务口径抽取并核对数据，用 SQL 从数仓取数并校验一致性；\n"
                "2. 搭建日常报表与可视化看板，供业务方直接使用；\n"
                "3. 围绕单个业务问题做专题分析，输出结论与行动建议；\n"
                "4. 参与数据质量校验与指标口径统一；\n"
                "5. 整理调研数据并支持竞品分析。\n"
                "任职要求：\n"
                "1. 统计、数学、计算机或经济学相关专业；\n"
                "2. 熟练使用 Excel 完成透视表与常用函数；\n"
                "3. 掌握 SQL 的筛选、聚合与多表关联；\n"
                "4. 了解 Python 的数据分析库（如 pandas）者优先；\n"
                "5. 具备基本统计思维，能把结论写成清晰的图表与文字；\n"
                "6. 有 Tableau 或 Power BI 看板经验优先。"
            ),
            "skills": [
                ("SQL", "hard", "掌握 SQL 的筛选、聚合与多表关联"),
                ("Excel", "hard", "熟练使用 Excel 完成透视表与常用函数"),
                ("数据可视化", "hard", "搭建日常报表与可视化看板，供业务方直接使用"),
                ("pandas", "hard", "了解 Python 的数据分析库（如 pandas）者优先"),
                ("Python", "hard", "了解 Python 的数据分析库（如 pandas）者优先"),
                ("Tableau", "hard", "有 Tableau 或 Power BI 看板经验优先"),
                ("Power BI", "hard", "有 Tableau 或 Power BI 看板经验优先"),
                ("统计基础", "hard", "具备基本统计思维，能把结论写成清晰的图表与文字"),
                ("指标口径", "domain", "参与数据质量校验与指标口径统一"),
                ("竞品分析", "soft", "整理调研数据并支持竞品分析"),
                ("机器学习", "hard", None),
            ],
        },
    ),
    (
        "seedboard_b",
        {
            "company_name": "某企业服务软件公司",
            "title": "AI 产品经理实习生",
            "location": "深圳",
            "employment_type": "internship",
            "salary_min": Decimal(4000),
            "salary_max": Decimal(5500),
            "experience_required": "每周到岗 4 天，实习 3 个月以上",
            "deadline": "+60d",
            "description": (
                "岗位职责：\n"
                "1. 参与 AI 功能的需求梳理与场景定义，输出 PRD 与交互流程；\n"
                "2. 设计 Prompt 与效果验收标准，参与 badcase 标注与归因；\n"
                "3. 跟踪主流大模型能力演进与同类产品，输出竞品分析；\n"
                "4. 使用 Axure 或 Figma 完成原型与评审材料；\n"
                "5. 协同研发与运营做上线效果复盘，跟踪核心指标。\n"
                "任职要求：\n"
                "1. 长期高频使用主流大模型产品，理解其能力边界；\n"
                "2. 具备结构化思维与文档写作能力，能把模糊需求写清；\n"
                "3. 会使用至少一种原型工具，例如 Axure、Figma 或 墨刀；\n"
                "4. 了解 RAG、Agent 或 Prompt 工程的基本逻辑者优先；\n"
                "5. 有 SQL 取数或数据分析基础者优先。"
            ),
            "skills": [
                ("PRD", "hard", "输出 PRD 与交互流程"),
                ("需求分析", "hard", "参与 AI 功能的需求梳理与场景定义"),
                ("Prompt 工程", "domain", "了解 RAG、Agent 或 Prompt 工程的基本逻辑者优先"),
                ("竞品分析", "soft", "跟踪主流大模型能力演进与同类产品，输出竞品分析"),
                ("Axure", "hard", "使用 Axure 或 Figma 完成原型与评审材料"),
                ("Figma", "hard", "使用 Axure 或 Figma 完成原型与评审材料"),
                ("RAG", "hard", "了解 RAG、Agent 或 Prompt 工程的基本逻辑者优先"),
                ("Agent", "hard", "了解 RAG、Agent 或 Prompt 工程的基本逻辑者优先"),
                ("文档写作", "soft", "具备结构化思维与文档写作能力"),
                ("SQL", "hard", None),
                ("数据分析", "hard", None),
            ],
        },
    ),
]


def _resolve_deadline(token: str | None, today: date) -> str | None:
    """`+Nd` 记号在运行时展开成绝对日期，脚本重放时窗口不会跟着日历过期。"""
    if token is None:
        return None
    return (today + timedelta(days=int(token.removeprefix("+").removesuffix("d")))).isoformat()


def _make_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24))


def _login_or_register(client: TestClient, username: str) -> tuple[str, dict[str, str]]:
    """可重放：账号已存在（第二轮跑）就用文件里的密码登录，不重复注册。"""
    credentials: dict[str, str] = {}
    if ACCOUNTS_FILE.exists():
        credentials = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))

    if username in credentials:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": credentials[username]},
        )
        if response.status_code == 401:
            raise SystemExit(
                f"{username} 存在但存档密码登不上——密码可能被改过。"
                f"要么修正 {ACCOUNTS_FILE}，要么删掉该条目重新注册。"
            )
    else:
        password = _make_password()
        response = client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": password},
        )
        if response.status_code not in (200, 201):
            raise SystemExit(f"注册 {username} 失败：{response.status_code} {response.text}")
        credentials[username] = password
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_FILE.write_text(
            json.dumps(credentials, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )

    if response.status_code != 200:
        raise SystemExit(f"登录 {username} 失败：{response.status_code} {response.text}")
    data: dict[str, Any] = response.json()["data"]
    return f"Bearer {data['access_token']}", credentials


def _assert_evidence_is_real(job: dict[str, Any]) -> None:
    """JD 里找不到的 evidence_text 一律报错，而不是悄悄落成一枚不可回溯的徽标。"""
    for name, _type, evidence in job["skills"]:
        if evidence is None:
            continue
        if evidence not in job["description"]:
            raise SystemExit(f"{job['title']} 的技能「{name}」证据不是 JD 原文：{evidence}")
        if len(evidence) > 60:
            raise SystemExit(f"{job['title']} 的技能「{name}」证据超过 60 字列宽")


def main() -> None:
    today = date.fromisoformat(datetime.now().date().isoformat())
    with TestClient(app) as client:
        tokens: dict[str, str] = {}
        created: list[tuple[str, str, int, int]] = []

        for username, job in SEED_PLAN:
            if username not in tokens:
                tokens[username], _ = _login_or_register(client, username)
            _assert_evidence_is_real(job)
            headers = {"Authorization": tokens[username]}

            # 幂等判定分两半，各自口径不同：
            # - 自己的草稿：`GET /jobs?status=draft` 本来就只返回本人的（他人草稿是 404 那套谓词），
            #   命中即「上次跑到一半」，补发布即可；
            # - 已发布岗位：这个视图是全库共读的，同名同企业可能是别人手工建的，
            #   所以必须再比 `created_by_user_id`，否则本脚本会把别人的岗位当成自己的跳过，
            #   或反过来在第二轮跑出 10 条重复公告板（第一版就是这么写的，改掉了）。
            mine = client.get("/api/v1/auth/me", headers=headers).json()["data"]["user"]["id"]
            haystack = (
                client.get(
                    "/api/v1/jobs",
                    params={"status": "draft", "keyword": job["title"][:20]},
                    headers=headers,
                ).json()["data"]["jobs"]
                + client.get(
                    "/api/v1/jobs",
                    params={"status": "published", "keyword": job["title"][:20]},
                    headers=headers,
                ).json()["data"]["jobs"]
            )
            owned = [
                item
                for item in haystack
                if item["company_name"] == job["company_name"]
                and item["created_by_user_id"] == mine
            ]
            if any(item["status"] == "published" for item in owned):
                created.append((job["company_name"], job["title"], 0, 1))
                continue
            already = bool(owned)

            payload = {
                "company_name": job["company_name"],
                "title": job["title"],
                "location": job["location"],
                "employment_type": job["employment_type"],
                "description": job["description"],
                "salary_min": str(job["salary_min"]),
                "salary_max": str(job["salary_max"]),
                "experience_required": job["experience_required"],
                "deadline": _resolve_deadline(job["deadline"], today),
                "skills": [
                    {
                        "name": name,
                        "type": skill_type,
                        "evidence_text": evidence,
                        # confidence 刻意留空：这一格语义是「模型自报的把握」，本脚本没有模型。
                    }
                    for name, skill_type, evidence in job["skills"]
                ],
            }

            if already:
                job_id = owned[0]["id"]
                publish = client.put(f"/api/v1/jobs/{job_id}/publish", headers=headers)
                if publish.status_code != 200:
                    raise SystemExit(f"重复发布 {job['title']} 失败：{publish.text}")
                created.append((job["company_name"], job["title"], len(payload["skills"]), -1))
                continue

            response = client.post("/api/v1/jobs", json=payload, headers=headers)
            if response.status_code != 201:
                raise SystemExit(
                    f"创建 {job['title']} 失败：{response.status_code} {response.text[:400]}"
                )
            job_id = response.json()["data"]["job"]["id"]
            publish = client.put(f"/api/v1/jobs/{job_id}/publish", headers=headers)
            if publish.status_code != 200:
                raise SystemExit(f"发布 {job['title']} 失败：{publish.status_code} {publish.text}")
            created.append((job["company_name"], job["title"], len(payload["skills"]), 0))

        # 验收读的是另一个账号的视角，顺带覆盖「published 任何登录用户可读」。
        reader, _ = _login_or_register(client, "seedboard_b")
        listing = client.get(
            "/api/v1/jobs", params={"status": "published"}, headers={"Authorization": reader}
        )
        items = listing.json()["data"]["jobs"]

        traceable = 0
        total_skills = 0
        for item in items:
            detail = client.get(
                f"/api/v1/jobs/{item['id']}", headers={"Authorization": reader}
            ).json()
            for skill in detail["data"]["job"]["skills"]:
                total_skills += 1
                if skill["origin"] == "jd_verifiable":
                    traceable += 1

    print(f"已发布岗位：{len(items)} 条（本次涉及 {len(created)} 条）")
    for company, title, n_skills, _ in created:
        print(f"  - {title}｜{company}｜技能 {n_skills} 条")
    print(f"技能条目合计 {total_skills} 条，其中可回溯原文（jd_verifiable）{traceable} 条")
    print(f"账号凭据：{ACCOUNTS_FILE}（勿提交）")


if __name__ == "__main__":
    main()
