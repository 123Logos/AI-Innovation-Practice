# ADR-021 组织、角色与审计日志（批次 B5）

- 状态：已采纳（2026-11-12）
- 相关：`docs/PRD.md` FR-01、`docs/API.md` §2、`docs/ROADMAP.md` §1 B5 行

## 1. 背景

### 1.1 今天有什么、缺什么

`users` 表只有 `id / username / email / password_hash / created_at / updated_at`，**没有组织、没有角色**。
`app/` 下 `organization`、`role`、`audit` 三词零命中。于是：

- 所有注册者都是同一种身份。`POST /auth/register` 收到 `role` / `organization_code` 时，
  Pydantic 把这两个字段**静默丢掉**（`RegisterRequest` 没有 `extra="forbid"`），
  客户端能提交、服务端不认，而响应里没有任何信号说明「你声称的教师身份没生效」。
  这是本批必须一并关掉的口子：**静默降级比明确拒绝更坏**，因为用户会以为自己已经是教师。
- `GET /me` 只回 `{id, username, email, created_at}`，前端没有任何依据决定「该不该看到组织管理入口」。
- 教师端的一切（学情看板 B10a、候选人推荐 B10b）都缺一个前置：**谁属于哪个组织、他是什么角色**。

### 1.2 约束

- `AGENTS.md` §4.6：新表必须先写 ADR —— 本文就是。
- `AGENTS.md` §3：服务层不依赖 FastAPI；权限判断若写成 HTTP 异常就已经越层。
- 不新增第三方依赖（不引 Casbin / OPA 这类策略引擎）。
- 现有 366 条前端用例、后端全套集成测试今天全绿，本批**不许把它们改成红**再「修好」。

## 2. 决策

### 2.1 三张表：`organizations` / `organization_members` / `audit_logs`

| 表 | 关键字段 | 为什么需要它 |
|---|---|---|
| `organizations` | `id`(uuid) / `code`(唯一，邀请码即组织码) / `name` / `kind`(school/college/company) / `created_at` / `updated_at` | 「账号绑定组织」与「跨组织不可见」的锚点。`code` 唯一且大小写不敏感（`utf8mb4_0900_ai_ci` + 落库前 `strip().upper()`），这样它可以直接当邀请码用，不必再发一套 token |
| `organization_members` | `id` / `organization_id` / `user_id` / `role`(student/teacher/school_admin) / `created_at` / `updated_at`，`(user_id)` 唯一、`(organization_id, role)` 普通索引 | **角色挂成员关系，不挂 `users`**。同一个人将来可能既是 A 校教师又是 B 企业导师；把 `role` 写进 `users` 就是把「身份」钉死成「全局属性」，第一次出现双身份就得改表 |
| `audit_logs` | `id`(bigint 自增) / `actor_user_id`(可空，系统动作为空) / `organization_id`(可空) / `action` / `target_type` / `target_id` / `result`(ok/denied) / `detail`(JSON 文本，可空) / `created_at` / `ip`(可空) | FR-01 要求「登录、改密、角色变更和管理员操作写入审计日志」。它是只追加的事实表，**没有 `updated_at`**——可改的审计日志不是审计日志 |

`(user_id)` 在 `organization_members` 上唯一，是本次的**业务边界**：一个账号当前只能属于一个组织。
这不是偷懒，是因为跨组织边界一旦允许，所有查询都要带 `organization_id` 的多值判断，
而 B10a 的看板还没有任何一种「跨校可见」的需求。真出现双身份时，把唯一键放宽成
`(user_id, organization_id)` 即可，表结构不用重做——这也是角色挂在成员关系上、而不是挂在用户上的第二个理由。

### 2.2 `RegisterRequest`：`extra="forbid"` **和** 显式校验都要

这两件事解决的不是同一个问题，`docs/API.md` §2 把它们并列提问，其实是两个口子：

- `extra="forbid"` 挡的是**未知字段**：客户端拼错 `organiztion_code`、或塞了 `is_admin`，
  今天会被静默忽略（甚至可能被未来的某次改动意外接住）。改成 `forbid` 后这类请求直接 422，
  错误位置精确到字段名。
- 显式校验挡的是**已知字段的非法值**：`role="teacher"` 却不给组织码、
  组织码不存在、`role` 不在枚举里，都必须 400 并说清是哪一条不成立。

结论：**两个都加**。只加 `forbid` 会让「role 给了但组织码是乱写的」继续静默通过；
只加显式校验则拼错字段名依旧无声无息。ADR-004 之后本仓的 422/400 分工不变：
结构不合法 → 422；结构合法但业务上不成立 → 400 + 人话。

### 2.3 角色与权限：静态映射表，不引策略引擎

`app/core/permissions.py` 里一张 `ROLE_PERMISSIONS: dict[UserRole, frozenset[str]]`：

```
student      → profile:read, profile:write, resume:read, resume:write, job:read, interview:own
teacher      → 上述只读 + org:member:read, job:review
school_admin → teacher 的全部 + org:member:manage, audit:read
```

`GET /me` 返回 `role` + `permissions`（数组）。前端据此决定菜单，**但前端不是权限边界**：
每个端点自己用 `require_permission("...")` 依赖再判一次。两处同源（都读同一张映射表），
所以不会出现「前端给了按钮、后端 403」的口径分叉。

为什么不引 Casbin/OPA：本批的权限规则是一张 12 行的静态表，引入策略引擎等于
为一个常量表背上一个依赖 + 一套 DSL + 一次「策略文件与代码不同步」的新故障面。
`AGENTS.md` §4.6 也要求新依赖先写 ADR —— 这次没有理由写那一篇。

### 2.4 组织绑定：**注册时可选，声称教师/管理员时必填且必须有效**

`docs/PRD.md` FR-01 写的是「账号必须绑定组织」。本批**不强制学生绑定**，理由是可验证的：

- 今天全部存量账号都没有组织。若注册强制组织码，现有用户一旦重新注册/改档案就会撞墙，
  而邀请码的发放（学校管理员建组织 → 发码）本身就是 B6 的成员邀请功能。
- 强制绑定应该由**邀请制**落地（管理员发码 → 学生凭码加入），而不是靠注册表单上
  一个可以随便填的文本框。用一个「谁都能填任意字符串」的字段假装实现了「必须绑定」，
  比不做更危险。

所以本批的规则是：

| 场景 | 行为 |
|---|---|
| `role` 缺省 | `student` |
| `role="student"` + 无组织码 | 允许，`organization` 为 `null` |
| `role="student"` + 有组织码 | 码必须存在，否则 400；有效则绑定 |
| `role="teacher"` / `"school_admin"` + 无码 | **400**，文案说明「教师/管理员账号需要组织码」 |
| `role="teacher"` / `"school_admin"` + 码不存在 | **400**，文案不区分「码不存在」与「码已停用」（避免枚举） |
| `role` 不在枚举内 | 422（Pydantic 枚举校验） |
| `role="platform_admin"` | **不接受自助注册**；该角色只能由运维在库里授予 |

**这条与 FR-01 的差距如实登记**：FR-01「账号必须绑定组织」由 B6 的邀请制补齐，
在 B6 落地前，「跨组织不可见」只在**有组织的账号之间**成立。这一格不许在 ROADMAP 里记成已达成。

### 2.5 审计写入与业务同事务

`audit_logs` 的写入放在**业务事务内**（同一个 `session_scope()`），不做异步旁路：

- FR-01 的诉求是「关键操作均可在审计日志中定位操作者、对象、时间和结果」。
  若审计写入失败而业务成功，就会出现一次不可追溯的角色变更——那正是审计要防的事。
- 代价是「审计表不可用 → 业务也不可用」。这个代价是可接受的：审计表与业务表同库同实例，
  它不可用时业务本来也已经不可用。**不接受的代价是「业务成功但无痕」**。

`result` 字段区分 `ok` / `denied`：被拒绝的管理操作（例如教师试图改别人的角色）**也要记**，
这正是「谁在什么时候试图越权」的唯一证据。

本批记录的 action 白名单（写死枚举，不用自由字符串）：
`user.register`、`user.login`、`organization.create`、`member.join`、`member.role_change`。
`user.password_change` 属 B6，`job.review` 属 B6 的审核流。

### 2.6 `GET /me` 的结构

```json
{"user": {"id": "...", "username": "...", "email": null, "created_at": "..."},
 "role": "teacher",
 "organization": {"id": "...", "code": "AI2026", "name": "示例大学", "kind": "school"},
 "permissions": ["profile:read", "org:member:read", "..."]}
```

`role` / `organization` / `permissions` **放在 `data` 顶层，不塞进 `user`**：
`user` 是「这个人是谁」，角色与组织是「他和某个组织的关系」，两者生命周期不同
（换组织不改用户）。塞在一起会让 `UserRead` 同时承担两种来源的数据，`model_validate(orm_user)`
就再也对不上——这也是本批要给 `UserRead` 加默认值、另起 `CurrentUserRead` 的原因。

## 3. 后果

### 3.1 好的

- 「声称是教师却被静默降级」这种无声故障**从结构上不可能再发生**（`forbid` + 显式校验 + 审计）。
- 教师端有了前置：B10a 的看板可以从 `organization_members` 直接拿「这个班的教师是谁」。
- 审计表从第一天就是只追加的，后面加「授权记录」「AI 任务」时不必回头改语义。

### 3.2 代价与风险

- **`extra="forbid"` 是行为变更**：任何今天往注册体里多塞字段的客户端会从「静默忽略」变成 422。
  已核对前端注册请求体只有 `username/password/email` 三个字段，无影响；但这是一次
  需要写进交付说明的对外行为变化。
- 登录现在多两次写（审计 + refresh token），并发登录高时 `audit_logs` 会成为写热点。
  本批接受：量级（校内几百人）远未触及；真成热点时把 `user.login` 改成采样或异步，
  但那时必须有另一条「登录可追溯」的路径，不能直接删。
- `organization_members.user_id` 唯一键限制了一个账号只能属于一个组织，见 §2.1 的放宽路径。

### 3.3 本批**不做**（明确划界，避免下一个人按 ROADMAP 重做）

- 院系 / 班级的层级（`departments` / `classes`）——属 B10a 的前置，本批不建。
- 成员邀请的完整流程（发码、过期、撤销）——B6。
- 跨组织数据过滤的**查询实现**：本批只落「归属 + 权限判断入口」，
  真正的 `WHERE organization_id = ?` 过滤在 B10a 的看板里做。**但这不等于隔离已经成立**：
  在 B10a 落地前，教师端端点一个都没有，所以也没有可被越权读到的跨组织数据。
- 平台管理员的后台（`platform_admin` 只是枚举值，没有对应页面与端点）。

## 4. 替代方案

| 方案 | 为什么不选 |
|---|---|
| `users.role` 单列 | 一个人只能有一个角色，且换组织要改用户表。第一次出现「既是教师又是企业导师」就得迁移 |
| 角色用多对多 `user_roles` 表 | 比 `organization_members` 更泛化，但本批没有任何「同一组织内多角色」的需求；泛化的代价是每次判断都要聚合 |
| 审计日志走应用日志（写文件 / stdout） | 无法按操作者/对象查询，也无法在管理页展示，且与「同事务」的要求冲突 |
| 审计走消息队列异步 | 新增外部依赖（`AGENTS.md` §4.6 要先写 ADR），且「业务成功、审计丢失」正是要防的事 |
| 注册强制组织码 | 见 §2.4：现有账号全无组织，且邀请码机制还没做，强制等于用一个可随便填的文本框假装实现了绑定 |
| 引 Casbin 做 RBAC | 见 §2.3：12 行静态表不值得一个依赖 + 一套 DSL |
