# 项目状态（真实版）

> 本文件描述的是“已实现并验证”的状态，不是“已设计”的状态。
> 任何未附测试输出或手动复现步骤的条目，不得写入“已验证”。
> 最后更新：2026-11-02

## 1. 一句话状态

简历切片已具备骨架链路（fake 仓储 + 模板生成 + 前端三栏编辑器）；后端在 T7.1 补齐
注册/登录/JWT 鉴权，认证链路已连真实 MySQL 并有集成测试。**简历生成仍走 fake 仓储，
尚未接真实用户。**

> 历史说明：本节曾写“数据库迁移未建立、测试仅覆盖健康检查”，该描述截至 T7.4 已过期，
> 现行事实以下方表格与第 6 节为准。

## 2. 资产分级

### 2.1 保留复用（已实现，可继续用）

| 资产 | 状态 | 说明 |
|---|---|---|
| 分层目录 `api/services/repositories/integrations` | 已存在 | 依赖方向规则见 AGENTS.md 4.1 |
| 健康检查接口 | 已实现 | 有测试 |
| 配置管理骨架 | 已存在 | 待补 fail-fast 与 `.env.example` |
| `DEVELOPMENT.md` 分层规则 | 已存在 | 待由 import-linter 强制 |

### 2.2 降级为设计稿（已设计，未实现，禁止当真相源）

| 资产 | 新位置 | 说明 |
|---|---|---|
| 领域模型 SQL | `docs/design/schema-draft.sql` | **非迁移源**；Phase 1 只建 5 张表 |
| Redis / ARQ / Celery 规划 | 文档 | Phase 1 不接入 |
| S3 / 对象存储规划 | 文档 | Phase 1 不接入 |
| WebRTC / 模拟面试规划 | 文档 | 冻结项 |
| 教师端 / 学校端规划 | 文档 | 冻结项 |

### 2.3 待补承重（Phase 1 必须完成）

| 项 | 对应任务 | 状态 |
|---|---|---|
| Alembic 基线 + 5 张表迁移 | T4 | 已验证 |
| fake 垂直切片 + 集成测试 | T5 | 已验证 |
| import-linter 架构约束 | T6 | 已验证（5 个 contract 全部通过） |
| 配置 fail-fast + `.env.example` | T6 | 已验证 |
| 统一错误模型 + 测试 | T6 | 已验证（含 404/422/405/500） |
| CI 四件套 | T6 | 已配置，未在 GitHub 运行 |
| T7.4 前端组件与本地状态流转 | T7 | 已验证（typecheck/build 通过） |
| 注册/登录/JWT 鉴权基建 | T7.1 | 已验证（`tests/test_auth.py` 12 passed，连 `resume_mvp_test`） |
| 真实链路 + 真实用户验证 | T7 | 未开始 |

## 3. 已验证 / 未验证

| 项 | 状态 | 证据 |
|---|---|---|
| 进程可启动 | 未验证 | 本次未运行启动或健康检查命令 |
| 数据库迁移可跑通 | **已验证** | `alembic/versions/20261023_0001_initial_phase1.py`、`tests/test_migrations.py`、`uv run pytest` 1 passed、MySQL 8.0.43、Ruff 0.17.1 全绿 |
| 注册/登录写入真实 MySQL | **已验证** | `uv run pytest tests/test_auth.py -v` 12 passed（201 注册 + 库内记录断言、重复用户名/邮箱 400、登录 200 + Bearer、错密码/未知用户 401、`/auth/me` 200、六种无效令牌 401） |
| JWT 生产 fail-fast | **已验证** | `Settings(environment="production")` 缺 `jwt_secret_key` 或不足 32 位均抛 `ValueError` |
| 业务链路可跑通 | **未验证** | 简历生成仍是 fake 仓储 |
| 手机/电脑可用 | **未验证** | 无真实用户记录 |
| AI 生成简历 | **未验证** | 未实现 |

## 4. 当前阻塞项

1. 简历生成仍是 fake 仓储，未落库；T7 的真实链路尚未开始（T7 剩余处理）；
2. 前端未接入登录态，认证接口只有后端与测试消费者；
3. CI 已配置四件套，但仅在本地验证过，未在 GitHub Actions 实际运行。

## 5. 下一步

按 `AGENTS.md` 第 8 节 T1 → T7 顺序执行，一次一个任务，完成一个更新本文件一次。

## 6. 本轮检查记录

- 已完成：修正 `docs/AGENT_HANDOFF.md` 第 5 节，使代码块仅包含 git 状态输出；修正第 6 节，使优先级链条使用 `text` 代码块。
- 已执行：未运行代码、测试、lint、类型检查或迁移命令；本轮仅涉及文档格式。
- 待办：T2.5 仍未执行，需按 `docs/AGENT_HANDOFF.md` 第 7 节的既定范围处理。
- T6 补正：统一错误模型已覆盖 404/422/405/500；import-linter layers 与 API forbidden
  contracts 均通过；已提供仅含占位符的 `.env.example`。
- T7.4：新增三栏简历编辑器、A4 预览、样式辅助面板与 Pinia 本地状态同步；未接入后端 API。
- 前端 TS 配置修复：新增 `@types/node`，移除已弃用 `baseUrl`；`npm run typecheck` 与 `npm run build` 通过。
- T7.1：新增 ADR-004（JWT 决策，含 bcrypt 版本锁定补记）；补充 `jwt_secret_key` /
  `jwt_algorithm` / `jwt_expire_minutes` 配置与 production fail-fast；新增
  `app/core/security.py`、`app/schemas/auth.py`、`app/repositories/user_repository.py`、
  `app/services/auth_service.py`、`app/api/v1/auth.py`，在 `dependencies.py` 增加
  `get_auth_service` / `get_current_user`；新增 `tests/test_auth.py`（12 项，连真实 MySQL
  `resume_mvp_test`，测试前后自动建表/删表）。全量 `uv run pytest -q` 31 passed，
  `ruff` / `mypy` / `lint-imports`（5/5 contract）全绿。未改前端、未改简历业务逻辑、
  未新增表、未 commit。
- T7.1 已知遗留：401 响应未透传 `WWW-Authenticate` 头（需改 `main.py`，不在本轮授权）；
  `docs/API.md` 尚未登记 `/api/v1/auth/*` 三个端点；前端未接入登录态。

## 7. 本轮未做（显式声明）

- 未新增 refresh token、令牌黑名单、登出接口（需新增表，超出 T7.1 范围）。
- 未把 `POST /api/v1/resumes/generate` 改为需要登录态（任务明确本轮不改简历接口）。
- 未修改 `app/integrations/`、`app/repositories/fake_resume_repository.py`、
  `frontend/` 下任何文件。