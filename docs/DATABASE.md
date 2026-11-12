> 【范围声明】本文件为数据库设计参考，非 Phase 1 实现依据。Phase 1 数据模型实现范围以 docs/MVP_SCOPE.md 为准；本文件中属于 MVP_SCOPE.md 冻结清单的表或数据结构，Phase 1 不实现、不排期、不建表。与 docs/MVP_SCOPE.md 或 AGENTS.md 冲突时，以后两者为准；本文件不扩大 Phase 1 范围。

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
| 面试 | `interview_sessions`, `interview_turns`（B11a 已建）；`media_rooms`, `interview_reports`（B11b，本期未建） | 文本/音视频适配层及证据化复盘 |
| 隐私治理 | `consents`, `files`, `audit_logs` | 授权、对象存储元数据和不可变审计 |

### 3.1 已落库的组织三表（批次 B5，2026-11-12，`ADR-021` §2.1）

上面 §3 那张表是**领域规划**（含 `memberships` / `roles` / `departments` 等尚未建的名称）。
B5 实际落库的是下面三张，迁移文件 `alembic/versions/20261112_0018_organizations_roles_audit.py`：

| 表 | 关键列 | 与规划名的差异（为什么） |
|---|---|---|
| `organizations` | `id`(VARCHAR36 PK) / `code`(VARCHAR50 唯一) / `name` / `kind`(school/college/company) / `created_at` / `updated_at` | `code` 同时是邀请码，唯一键建在库上；落库前 `strip().upper()` |
| `organization_members` | `id` / `organization_id`(FK→organizations, RESTRICT) / `user_id`(FK→users, CASCADE, **唯一**) / `role`(VARCHAR20) / `created_at` / `updated_at`；索引 `(organization_id, role)` | 规划里把「成员关系」与「角色」分成 `memberships` + `roles` 两张表；实际合并成一张，因为本批只有「一个人在一个组织里一个角色」，拆两张再加连接表是为一格还不存在的泛化付三次迁移成本。放宽成多组织只需把 `user_id` 唯一键改成 `(user_id, organization_id)` |
| `audit_logs` | `id`(BIGINT 自增) / `actor_user_id`(FK→users, SET NULL, 可空) / `organization_id`(FK→organizations, SET NULL, 可空) / `action` / `target_type` / `target_id` / `result`(ok/denied) / `detail`(TEXT) / `ip` / `created_at` | **没有 `updated_at`**：只追加表，可改的审计日志不是审计日志。`detail` 存 JSON 字符串而不是 JSON 列——本批不按 detail 内部字段查询 |

三条一致性约定：

1. **角色挂成员关系、不挂 `users`**：一个人将来既是 A 校教师又是 B 企业导师时不用改表；
2. **审计与业务同事务**：审计写失败 → 业务回滚（`ADR-021` §2.5）；`result=denied` 的越权尝试**也记**；
3. **刻意没有 `(created_at)` 单列索引**：本批没有跨组织的「全平台最近操作」查询，索引只有
   `(organization_id, created_at)` 与 `(actor_user_id, created_at)` 两条复合索引
   （`tests/test_migrations.py` 那条「不许有单列 created_at 索引」判据同时挡着它）。

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

## 7. 面试表（B11a 已实现）

> 2026-11-12 批次 B11a（FR-07，ADR-022）落地**纯文本**面试，只建两张关系表。
> 旧版领域图里的 `media_rooms`、`interview_reports` 本期**未建**，留待 B11b（音视频 + 评估报告）。
> 迁移：`alembic/versions/20261112_0019_interview_sessions.py`，`down_revision="20261111_0017"`，upgrade 建表 / downgrade 删表（先删子表 `interview_turns` 再删父表 `interview_sessions`）。
> 它与批次 B5 的 `0018` 是两条并行分支（同以 `0017` 为父），合入 master 时由
> `alembic/versions/20261112_0020_merge_b5_b11a_heads.py` 收敛成单一 head；
> `upgrade head` / `downgrade 20261111_0017` / `upgrade head` 已在干净测试库双向实测
> （merge point 上 `downgrade -1` 会 `Ambiguous walk`，必须指定明确 revision）。

### 7.1 `interview_sessions`（会话元信息）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `CHAR(36)` | PK | UUID 字符串 |
| `user_id` | `CHAR(36)` | NOT NULL, FK→`users.id` ON DELETE RESTRICT | 会话归属用户；删账号不带走面试历史 |
| `job_id` | `CHAR(36)` | NULL, FK→`jobs.id` ON DELETE RESTRICT | 可选绑定的岗位；删岗位不带走面试历史 |
| `status` | `VARCHAR(16)` | NOT NULL, 默认 `active`, CHECK IN (`active`,`ended`) | 会话状态；加取值只改代码不改表 |
| `target_role` | `VARCHAR(200)` | NULL | 用户填写/从岗位标题派生的目标方向 |
| `created_at` | `DATETIME(6)` | NOT NULL | 应用层写 UTC |
| `updated_at` | `DATETIME(6)` | NOT NULL | 每次收发消息 `touch` 更新，用于列表排序 |
| `ended_at` | `DATETIME(6)` | NULL | 结束时刻；未结束为 NULL |
| `ix_interview_sessions_user_status` | 索引 | (`user_id`, `status`, `updated_at`) | 列表查询优先索引 |

### 7.2 `interview_turns`（逐轮问答，有序事件流）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `CHAR(36)` | PK | UUID 字符串 |
| `session_id` | `CHAR(36)` | NOT NULL, FK→`interview_sessions.id` ON DELETE RESTRICT | 所属会话 |
| `sequence` | `INT` | NOT NULL | 轮次序号，从 1 递增，按它回放 |
| `speaker` | `VARCHAR(16)` | NOT NULL, CHECK IN (`interviewer`,`candidate`) | 发言方；加取值只改代码不改表 |
| `content` | `TEXT` | NOT NULL | 该轮文本（提问或作答） |
| `ai_degrade_reason` | `TEXT` | NULL | **可见降级数据层证据**：非空即代表该轮面试官内容由本地离线模板产出、非真 AI（取值 `key_missing`/`key_invalid`/`timeout`/`upstream`/`bad_output`） |
| `created_at` | `DATETIME(6)` | NOT NULL | 应用层写 UTC |
| `ix_interview_turns_session_sequence` | 索引 | (`session_id`, `sequence`) | 回放 / 取最近 N 轮 |

设计要点（ADR-022）：① 两表而非单 JSON 字段——`turns` 是可审计、可增量写入、可按 `sequence` 回放的有序事件流；② 降级可追踪——`ai_degrade_reason` 是非空即降级，前端据此给出可见提示、绝不静默；③ 外键 RESTRICT——删账号/删岗位都不该带走面试历史；④ 状态/发言方用 VARCHAR + CHECK 而非 MySQL ENUM，新增取值不需改表；⑤ 时间用 `DATETIME(6)`（与 jobs / student_profiles 一致）。
