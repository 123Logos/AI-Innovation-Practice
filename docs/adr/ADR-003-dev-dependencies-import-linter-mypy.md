# ADR-003：开发依赖与架构/类型检查

- 状态：已接受
- 日期：2026-10-24
- 决策人：本项目人类负责人

## 背景

T6 需要将 AGENTS.md 4.1 的分层规则落到可执行检查中，并满足完成定义中的
type check 要求。当前项目已有 `app/api`、`app/services`、`app/repositories`
和 `app/integrations` 分层，但尚未配置 import-linter，也没有类型检查命令。

## 决策

1. 将 `import-linter>=2.3.0` 加入 `[dependency-groups].dev`，用它执行分层
   contract 检查。
2. 将 `mypy>=1.13.0` 加入 `[dependency-groups].dev`，用它执行 Python 类型检查。
3. mypy 首阶段采用宽松配置：Python 版本为 3.13，忽略缺失的第三方 stub，
   不强制所有函数具备显式类型标注，并开启未使用配置检查。后续如需收紧，
   另行记录 ADR 和分阶段改动。
4. import-linter contract 按 AGENTS.md 4.1 执行：API 依赖 services，
   services 依赖 repositories/integrations；API 不直接依赖 repositories 或
   integrations；services 不依赖 FastAPI；repositories 不依赖 services。
   `schemas` 与 `core` 作为共享层，不纳入分层 contract。
5. CI 使用 GitHub Actions，执行 Ruff、mypy、import-linter、pytest 和迁移检查。
   T6 提供 `.env.example`，仅占位符，不含真实凭据。

## 后果

- 本地开发需要通过 uv 同步新增开发依赖。
- CI 增加架构检查和类型检查，违规或类型检查失败会阻止流水线通过。
- mypy 初始配置不会要求现有代码立即完成严格类型重构；后续仍需逐步收紧。
- import-linter 会把分层方向从约定变成可执行检查。
- mypy 当前排除 `tests/test_migrations.py` 与 `tests/test_resumes_generate.py` 两个既有测试文件；
  将这两份测试纳入全量 type check 列为后续任务。

## 替代方案

- **使用手写 pytest 架构测试替代 import-linter**：不采用，因为 T6 明确要求
  import-linter，且专用工具更直接表达依赖 contract。
- **使用 pyright 替代 mypy**：不采用，本轮选择 mypy 作为项目 type check 命令，
  以减少工具选择并满足 T6 已拍板要求。
- **不做类型检查**：不采用，因为 AGENTS.md 的完成定义要求 type check，
  CI 还必须包含 type check。
