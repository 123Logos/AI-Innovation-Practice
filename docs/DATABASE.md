# MySQL 数据库设计

## 1. 设计目标

本设计对应 MVP 范围，兼顾第二阶段的成长任务、音视频面试和教学分析：

- 当前项目数据库：`ai_innovation_practice`；
- MySQL 8.0+、`utf8mb4`、UTC 时间；
- 所有租户业务数据带 `organization_id`，服务层所有查询必须带组织条件；
- API 对外使用 UUID 字符串；数据库第一版使用 `CHAR(36)`，便于 SQLAlchemy 和迁移调试；
- 文件正文和音视频不进入 MySQL，只保存对象存储键及必要元数据；
- AI 结果保留任务、模型、提示词版本、来源引用和结构化 JSON，禁止只保存不可解释的分数；
- 用户资料、简历正文、面试内容通过 `consents` 和服务层权限联合控制；
- 业务对象采用软删除时使用 `deleted_at`，审计和授权记录不做软删除。

完整第一版 DDL 见 [`database/schema.sql`](../database/schema.sql)。

> 数据库已由项目环境创建。执行 DDL 前请确认连接到 `ai_innovation_practice`；
> `schema.sql` 保留 `CREATE DATABASE IF NOT EXISTS` 以支持新环境初始化，不会使用旧的数据库名称。

## 2. 领域关系

```text
organizations
  ├─ memberships ─ users
  ├─ departments ─ classes ─ class_members
  ├─ companies ─ job_posts ─ job_skill_requirements ─ skills
  └─ student_profiles ─ student_skills ─ skills
                         └─ evidence_items ─ evidence_skills

job_posts + student_profiles
  └─ match_runs ─ match_results ─ match_result_items
                 └─ growth_plans ─ growth_tasks

student_profiles ─ resumes ─ resume_versions ─ resume_shares
student_profiles ─ interview_sessions ─ interview_turns
                                  └─ interview_reports

所有 AI 操作 ─ ai_jobs ─ ai_job_events
所有敏感访问 ─ consents / audit_logs
```

## 3. 关键表说明

| 模块 | 表 | 说明 |
|---|---|---|
| 组织与权限 | `users`, `organizations`, `memberships`, `roles`, `refresh_tokens` | 账号、租户、成员关系、角色和令牌轮换 |
| 教学组织 | `departments`, `classes`, `class_members` | 院系、班级和学生归属 |
| 学生能力 | `student_profiles`, `skills`, `student_skills`, `evidence_items`, `evidence_skills` | 档案、技能状态和可追溯证据 |
| 岗位 | `companies`, `job_posts`, `job_skill_requirements` | 企业、岗位、岗位硬/软技能要求 |
| AI 分析 | `ai_prompt_versions`, `ai_jobs`, `ai_job_events` | 异步任务、幂等、重试、模型和事件 |
| 测评 | `assessments`, `assessment_questions`, `assessment_attempts`, `assessment_answers` | 题库版本、原始答案和评分 |
| 匹配成长 | `match_runs`, `match_results`, `match_result_items`, `growth_plans`, `growth_tasks` | 版本化权重、解释项、缺口和行动任务 |
| 简历 | `resumes`, `resume_versions`, `resume_shares` | 可编辑版本、岗位定制和字段白名单分享 |
| 面试 | `interview_sessions`, `interview_turns`, `media_rooms`, `interview_reports` | 文本/音视频适配层及证据化复盘 |
| 隐私治理 | `consents`, `files`, `audit_logs` | 授权、对象存储元数据和不可变审计 |

## 4. 状态与一致性约束

- 岗位：`draft -> pending_review -> approved -> published -> closed/expired/rejected`。
- AI 任务：`queued -> running -> succeeded/failed/cancelled`；同一业务操作通过 `idempotency_key` 唯一约束防重复。
- 简历版本不可覆盖：每次编辑新增版本，`version_no` 在同一简历内唯一。
- 授权撤回通过更新 `revoked_at` 立即生效；读取时必须检查 `expires_at IS NULL OR expires_at > UTC_TIMESTAMP()`。
- 删除学生技能只删除 `student_skills` 关联，不删除公共 `skills`。
- 匹配结果的 `weight_version`、权重和 `data_as_of` 必须随结果保存，保证结果可复现。

## 5. 索引与查询原则

1. 列表查询优先使用 `(organization_id, status, created_at)` 或对应业务状态索引。
2. 岗位技能、学生技能和证据使用关联表，避免把可查询技能列表仅存为 JSON。
3. JSON 仅用于 AI 原始输出、解释和可变的简历区块；核心筛选字段使用独立列。
4. 不在数据库层依赖前端隐藏按钮实现授权；Repository 查询和 Service 权限检查必须同时执行。
5. 大表 `audit_logs`、`ai_job_events`、`interview_turns` 后续可按月归档或分区；MVP 先使用时间索引。
6. `files.object_key` 使用 768 字符前缀唯一索引，避免 `utf8mb4` 下超过 InnoDB
   3072 字节索引上限；对象存储键应由应用生成不可重复的前缀，避免仅前 768 字符相同。

## 6. 迁移和备份

- 生产环境通过 Alembic 管理迁移，禁止直接修改已执行 DDL；
- 部署前执行迁移检查，迁移脚本必须支持回滚或明确记录不可逆操作；
- 备份加密，至少保留日备份和恢复演练记录；
- 生产账号只授予应用所需权限，迁移账号与运行账号分离；
- 数据库连接使用 TLS，密码、连接串和密钥只通过环境变量/密钥管理服务注入。
