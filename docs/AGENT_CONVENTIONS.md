# 项目协作规则

> 本文件是 AGENTS.md 的补充；与 AGENTS.md 冲突以 AGENTS.md 为准；与 MVP_SCOPE.md 冲突以 MVP_SCOPE.md 为准；本文件不定义产品范围、架构分层、技术选型。

> 定位：本文件只补充“进度同步、验证命令、文档引用”三类操作细节。
> 与 AGENTS.md 冲突时，以 AGENTS.md 为准；与 MVP_SCOPE.md 冲突时，以 MVP_SCOPE.md 为准。
>

## 进度同步（强制）

- 每次修改代码、配置、数据库、前端或测试后，必须在同一轮同步更新 [`docs/PROJECT_STATUS.md`](../docs/PROJECT_STATUS.md)。
- 更新进度文档的更新时间、已完成项、待办项，以及实际执行的测试/构建/检查结果。
- 只有实际验证通过的内容才能写入“已完成”；未实现、未运行或被阻塞的内容必须明确标注。
- 新增接口、数据表、依赖、环境变量或命令时，同时更新相关 `docs/` 文档或 [`README.md`](../README.md)。
- 交付前检查代码变更与进度文档是否一致；未同步进度不得声称任务完成。
- 进度同步是 T 级任务收尾动作，不是每次代码提交动作。

## 项目约定

- 文档引用：若 docs/PRD.md / docs/API.md / docs/DATABASE.md / docs/TESTING.md 存在则引用；不存在时以 AGENTS.md 与 MVP_SCOPE.md 为准，禁止自行创建这些文件。当上述文档与 AGENTS.md 或 docs/MVP_SCOPE.md 冲突时，以 AGENTS.md / MVP_SCOPE.md 为准；上述文档不得扩大 Phase 1 范围。
- API 响应、错误码、权限和接口约定以 [`docs/API.md`](../docs/API.md) 为准。
- 数据模型、组织隔离、授权和迁移原则以 [`docs/DATABASE.md`](../docs/DATABASE.md) 为准。
- 测试层级和发布门槛以 [`docs/TESTING.md`](../docs/TESTING.md) 为准。
- 后端使用 Python 3.13、uv、FastAPI；前端技术栈见 ADR-001；本文件不重复定义。
- 不得提交真实密钥、`.env`、数据库备份或对象存储凭据；DeepSeek 密钥只能在后端使用。

## 验证要求

- 后端变更至少运行针对性 `uv run pytest`；涉及格式或静态检查时运行对应 Ruff/类型检查。
- 前端变更至少运行 `npm run typecheck` 或 `npm run build`；涉及样式和交互时补充对应检查。(先cd入前端文件夹)
- 在无法执行验证命令时，必须在进度文档中记录阻塞原因，不能推断为通过。
