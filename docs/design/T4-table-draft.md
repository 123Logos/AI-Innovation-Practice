# T4 Phase 1 数据表字段草案

> 状态：待人类确认，非迁移文件。
>
> 本草案仅服务 Phase 1 简历切片。字段设计不采用旧版
> [`docs/DATABASE.md`](../DATABASE.md) 作为真相源；实际迁移必须以人类确认后的字段和 T4 阶段 2 指令为准。

## 1. 设计边界

Phase 1 只建立以下 5 张表：

`users`、`student_profiles`、`skills`、`experiences`、`resumes`

关系最小化为：

```text
users 1 ── 1 student_profiles
student_profiles 1 ── * skills
student_profiles 1 ── * experiences
student_profiles 1 ── * resumes
```

本草案不建立组织、角色、岗位、测评、匹配、面试、AI 任务、文件、审计或其他冻结项相关表。Phase 1 不需要的字段不建。

## 2. MySQL 8.0 约定

- 存储引擎：InnoDB。
- 字符集：数据库和表使用 `utf8mb4`。
- 排序规则：`utf8mb4_0900_ai_ci`。
- 时间类型：使用 `DATETIME(6)`。
- 时间基准：应用写入 UTC；数据库字段不保存时区，读取和序列化时按 UTC 解释。
- 主键：使用 `CHAR(36)` 保存应用生成的 UUID 字符串，便于与现有 FastAPI/SQLAlchemy 骨架及测试对接。
- 软删除：Phase 1 默认不引入 `deleted_at`。当前主路径没有恢复删除数据、回收站或审计需求，删除行为由后续已批准的迁移和服务规则另行定义。
- 表结构只通过 Alembic 迁移创建或修改，不直接执行手写 DDL。

## 3. 字段草案

### 3.1 `users`

账号和认证主体。密码只保存不可逆哈希，不保存明文密码。

| 字段 | MySQL 8.0 类型 | 可空 | 默认值 | 约束/索引 | 最小必要用途 |
|---|---|---|---|---|---|
| `id` | `CHAR(36)` | 否 | 无 | PK | 关联学生档案和登录主体 |
| `username` | `VARCHAR(100)` | 否 | 无 | UNIQUE | 用户名登录 |
| `email` | `VARCHAR(254)` | 是 | `NULL` | UNIQUE（非空值） | 可选邮箱登录；是否必填待确认 |
| `password_hash` | `VARCHAR(255)` | 否 | 无 | 无 | 保存密码哈希 |
| `created_at` | `DATETIME(6)` | 否 | 无 | INDEX | 创建时间 |
| `updated_at` | `DATETIME(6)` | 否 | 无 | 无 | 更新时间 |

最小关系：`users.id` 被 `student_profiles.user_id` 引用。用户名和邮箱至少一个用于登录；具体互斥/必填校验待人类确认。

### 3.2 `student_profiles`

学生用于生成简历的基本资料。

| 字段 | MySQL 8.0 类型 | 可空 | 默认值 | 约束/索引 | 最小必要用途 |
|---|---|---|---|---|---|
| `id` | `CHAR(36)` | 否 | 无 | PK | 档案标识 |
| `user_id` | `CHAR(36)` | 否 | 无 | UNIQUE, FK → `users.id` | 一名用户一个 Phase 1 档案 |
| `full_name` | `VARCHAR(100)` | 否 | 无 | 无 | 简历姓名 |
| `major` | `VARCHAR(100)` | 否 | 无 | 无 | 专业 |
| `grade` | `VARCHAR(50)` | 否 | 无 | 无 | 年级 |
| `contact` | `VARCHAR(255)` | 否 | 无 | 无 | 联系方式 |
| `created_at` | `DATETIME(6)` | 否 | 无 | INDEX | 创建时间 |
| `updated_at` | `DATETIME(6)` | 否 | 无 | 无 | 更新时间 |

关系：`user_id` 外键引用 `users.id`；外键删除行为见 5.5，由人类拍板。

### 3.3 `skills`

学生填写的技能及自评等级。为保持 Phase 1 只建 5 张表，技能记录直接归属学生档案，不另建公共技能表或关联表。

| 字段 | MySQL 8.0 类型 | 可空 | 默认值 | 约束/索引 | 最小必要用途 |
|---|---|---|---|---|---|
| `id` | `CHAR(36)` | 否 | 无 | PK | 技能记录标识 |
| `student_profile_id` | `CHAR(36)` | 否 | 无 | FK → `student_profiles.id`, INDEX | 归属学生 |
| `name` | `VARCHAR(100)` | 否 | 无 | UNIQUE(`student_profile_id`, `name`) | 技能名称 |
| `self_rating` | `TINYINT UNSIGNED` | 否 | 无 | CHECK 1–5 | 自评等级 |
| `created_at` | `DATETIME(6)` | 否 | 无 | INDEX | 创建时间 |
| `updated_at` | `DATETIME(6)` | 否 | 无 | 无 | 更新时间 |

### 3.4 `experiences`

项目或实践经历，直接归属学生档案。

| 字段 | MySQL 8.0 类型 | 可空 | 默认值 | 约束/索引 | 最小必要用途 |
|---|---|---|---|---|---|
| `id` | `CHAR(36)` | 否 | 无 | PK | 经历记录标识 |
| `student_profile_id` | `CHAR(36)` | 否 | 无 | FK → `student_profiles.id`, INDEX | 归属学生 |
| `title` | `VARCHAR(200)` | 否 | 无 | 无 | 项目/实践标题 |
| `start_date` | `DATE` | 否 | 无 | 无 | 起始时间 |
| `end_date` | `DATE` | 是 | `NULL` | 无 | 结束时间；进行中可为空 |
| `description` | `TEXT` | 否 | 无 | 无 | 经历描述 |
| `responsibility` | `TEXT` | 否 | 无 | 无 | 个人职责 |
| `created_at` | `DATETIME(6)` | 否 | 无 | INDEX | 创建时间 |
| `updated_at` | `DATETIME(6)` | 否 | 无 | 无 | 更新时间 |

### 3.5 `resumes`

生成后可编辑、保存并再次打开的简历。

| 字段 | MySQL 8.0 类型 | 可空 | 默认值 | 约束/索引 | 最小必要用途 |
|---|---|---|---|---|---|
| `id` | `CHAR(36)` | 否 | 无 | PK | 简历标识 |
| `student_profile_id` | `CHAR(36)` | 否 | 无 | FK → `student_profiles.id`, INDEX | 归属学生 |
| `title` | `VARCHAR(200)` | 否 | 无 | 无 | 简历名称 |
| `content_html` | `LONGTEXT` | 否 | 无 | 无 | 可预览、可编辑的 HTML 内容 |
| `created_at` | `DATETIME(6)` | 否 | 无 | INDEX | 创建时间 |
| `updated_at` | `DATETIME(6)` | 否 | 无 | 无 | 保存和再次打开时的更新时间 |

本阶段不单独建立简历版本表、分享表、导出文件表或 AI 任务表；版本历史和 PDF 文件存储不属于 T4 的 5 张表最小范围。

## 4. 迁移策略草案

采用一个 initial Alembic migration，`upgrade()` 创建上述 5 张表，`downgrade()` 按外键依赖的逆序删除：

1. `resumes`
2. `experiences`
3. `skills`
4. `student_profiles`
5. `users`

理由：

- 当前数据库迁移尚未建立，单一基线能清楚表达 Phase 1 的初始 schema；
- 方便复现“空库 → upgrade head → downgrade base”；
- 避免把旧设计稿中的非 Phase 1 表带入迁移；
- 后续表结构变更再新增独立迁移，不重写已执行的 initial migration。

## 5. 待人类拍板项

### 5.1 MySQL 驱动候选

| 候选 | 与现有 FastAPI 骨架的匹配 | 需要确认的事项 |
|---|---|---|
| PyMySQL | Python 纯实现，适合当前同步 SQLAlchemy 配置；若使用 SQLAlchemy async engine，需采用对应 async 方言配置 | 是否采用同步数据库访问 |
| asyncmy | 异步 MySQL 驱动，适合 SQLAlchemy asyncio 与 FastAPI async 路由 | 当前骨架没有 async SQLAlchemy session，需同步补齐异步数据库接线 |
| mysqlclient | 基于本地 C 客户端的同步驱动，适合同步 SQLAlchemy | Windows/CI 编译依赖和环境可复现性需确认 |

本草案不选择驱动、不安装驱动，也不修改依赖文件。

### 5.2 迁移测试数据库策略候选

| 候选 | 可复现方式 | 主要约束 |
|---|---|---|
| 本机 MySQL 实例 | 建立专用空测试库，运行 `alembic upgrade head`，检查表后运行 `alembic downgrade base` | 依赖每位开发者本机配置，CI 可复现性较弱 |
| Docker MySQL | 固定 MySQL 8.0 镜像启动临时实例，等待健康后执行 upgrade/downgrade | 需要 Docker 环境和端口/凭据注入 |
| testcontainers | 测试启动 MySQL 8.0 容器，动态取得连接 URL 后执行迁移 | 需要 Docker 和 testcontainers 依赖 |
| CI service | CI 声明 MySQL 8.0 service，迁移测试使用 CI 注入 URL | 本地需要另有等价策略，配置需同步 |

无论选择哪种策略，最低复现流程均为：准备空数据库 → 指向该数据库的 Alembic URL → `alembic upgrade head` → 核对 5 张业务表 → `alembic downgrade base` → 确认业务表清空，仅保留 Alembic 版本表（若迁移工具保留版本表）。

本草案不选择测试数据库策略，不创建数据库、不运行迁移。

### 5.3 ORM models 或 SQLAlchemy Core

| 候选 | 对 T4 的影响 | 对 T5 的影响 |
|---|---|---|
| ORM models | 迁移可由模型元数据辅助生成或手写 Alembic operations；需要建立模型目录和 session 基础 | T5 fake repo 后续替换真实 repo 时可复用模型，类型和关系更直观 |
| 仅 Alembic + SQLAlchemy Core | T4 只维护迁移定义，不新增 ORM 模型 | T5 需要自行定义 Core table/session 查询，repository 接线更轻但模型复用较少 |

本草案不替人类决定 ORM 或 Core 方案。

### 5.4 依赖文件落点

当前依赖现状：

- 运行时依赖位于根目录 [`pyproject.toml`](../../pyproject.toml) 的 `[project].dependencies`；
- 开发依赖位于同一文件的 `[dependency-groups].dev`；
- 未发现 `requirements.txt`、`requirements-dev.txt` 或其他 requirements 文件。

因此，若人类确认新增依赖，候选落点是现有 `pyproject.toml`，但本阶段不修改依赖文件。

### 5.5 外键 `ON DELETE` 行为

四处外键及其当前可空性如下：

| 外键 | 当前可空性 | `RESTRICT` / `NO ACTION` | `CASCADE` | `SET NULL` |
|---|---|---|---|---|
| `student_profiles.user_id → users.id` | 不可空 | 删除用户时拒绝，避免留下无主学生档案；删除用户主路径需由服务层先处理档案 | 删除用户时级联删除档案及其下属数据；Phase 1 没有删除用户主路径，但误删影响范围最大 | 不适用：外键不可空；若改为可空会允许无主档案，需改变一对一完整性 |
| `skills.student_profile_id → student_profiles.id` | 不可空 | 删除档案时拒绝，需先处理技能记录；数据保留更安全但删除流程更复杂 | 删除档案时级联删除技能；Phase 1 没有删除档案主路径，后续删除操作风险较集中 | 不适用：外键不可空；若改为可空会产生无归属技能记录 |
| `experiences.student_profile_id → student_profiles.id` | 不可空 | 删除档案时拒绝，需先处理经历记录；可避免意外丢失简历素材 | 删除档案时级联删除经历；Phase 1 没有删除档案主路径，但会一次删除全部经历 | 不适用：外键不可空；若改为可空会产生无归属经历记录 |
| `resumes.student_profile_id → student_profiles.id` | 不可空 | 删除档案时拒绝，避免保存的简历失去归属；删除流程需要显式处理简历 | 删除档案时级联删除简历；Phase 1 没有删除档案主路径，且可能删除用户已保存内容 | 不适用：外键不可空；若改为可空会产生无归属简历记录 |

Phase 1 无删除用户或学生档案的主路径。四处外键的默认行为倾向由人类确认，草案不代选；迁移阶段必须使用人类确认的统一规则，并显式写入约束。

### 5.6 同步 vs 异步数据库访问

驱动选型与同步/异步访问是同一项决策，不能分开确定。

| 访问方式 | 驱动候选 | 对现有 FastAPI 骨架的影响 | 对 T5 repository 的影响 |
|---|---|---|---|
| 同步 SQLAlchemy | PyMySQL / mysqlclient | 现有 FastAPI 路由可以保持简单的同步 session 接线；mysqlclient 还需考虑 Windows/CI 原生编译，PyMySQL 无此编译要求 | repository 使用同步 session 和同步查询；实现直接，但同步数据库操作会占用请求线程 |
| 异步 SQLAlchemy | asyncmy | 需要新增 async engine、async session 工厂、生命周期管理和异步依赖接线；当前骨架尚无这些组件 | repository 方法和测试均需使用 async session/await，需同步调整 T5 fake repo 替换路径 |

本草案不在同步和异步之间做选择，也不单独选择驱动。

### 5.7 `created_at` 索引必要性

以下是按 Phase 1 当前查询依据的逐表审查；最终是否创建索引由人类确认，不能仅因字段存在就默认保留：

| 表 | 当前 `created_at` INDEX 草案 | Phase 1 查询依据 | 当前建议 |
|---|---|---|---|
| `users` | 有 | 注册后的账号查询可能按创建时间排序，但主路径主要按用户名/邮箱唯一键查询；当前没有按时间过滤或分页需求 | 时间查询依据弱，建议不建，待人类确认 |
| `student_profiles` | 有 | “我的档案”按 `user_id` 唯一查询，不按时间过滤或分页 | 无 Phase 1 时间查询依据，建议不建，待人类确认 |
| `skills` | 有 | 技能列表按 `student_profile_id` 查询，当前不按时间过滤、排序或分页 | 无 Phase 1 时间查询依据，建议不建，待人类确认 |
| `experiences` | 有 | 经历列表按 `student_profile_id` 查询；简历排序按项目日期而非创建时间 | 无 Phase 1 时间查询依据，建议不建，待人类确认 |
| `resumes` | 有 | “我的简历”可按 `student_profile_id` 列表，未来可能按更新时间排序；当前未要求按创建时间过滤或分页 | 当前依据不足，建议不建，待人类确认；若确认需要时间排序/分页再保留或改用合适索引 |

### 5.8 `email`、`contact` 与 `end_date` 约束

#### `users.email` 与 `student_profiles.contact`

二者服务不同语义：`email` 是账号登录/唯一识别候选，`contact` 是可放入简历的联系方式，可能包含电话、邮箱或其他联系信息。待人类从以下选项中确认：

1. **允许不同且均可独立填写**：`email` 用于认证，`contact` 用于简历展示；可能重复但不强制一致。
2. **contact 必填，email 可选**：兼容用户名登录且要求简历有联系方式；账号邮箱不一定展示到简历。
3. **email 必填并复用到 contact**：减少重复输入，但会限制联系方式表达，并需要明确是否允许 contact 覆盖展示值。
4. **email 与 contact 必须一致**：数据规则最严格，但不适合 `contact` 允许电话或其他联系方式的产品路径。

当前草案保留 `users.email` 可空、`student_profiles.contact` 不可空的字段形态；这只是字段草案现状，不代表已替人类决定登录必填性、是否允许不同或是否自动同步。

#### `experiences.end_date`

`end_date` 当前可空，用于表示进行中的项目/实践。待人类确认是否增加：

- **增加 `CHECK (end_date IS NULL OR end_date >= start_date)`**：数据库直接阻止结束日期早于开始日期，保证时间区间一致；迁移和测试需覆盖 MySQL 8.0 CHECK 行为。
- **不增加 CHECK**：字段约束更宽，兼容历史或不完整输入，但日期合法性完全依赖 Pydantic/API 和后续 repository 校验，数据库可能保存无效区间。

本草案不代选 `end_date` CHECK 方案。

## 6. 阶段 1 明确不做

- 本草案不含实现代码。
- 未安装 Alembic、SQLAlchemy、MySQL 驱动或测试容器依赖。
- 未创建迁移文件或 Alembic 配置。
- 未创建数据库或执行 DDL。
- 未修改 FastAPI、配置、API、前端或任何业务代码。
- 未建立第 6 张表，也未引入冻结清单相关字段。
