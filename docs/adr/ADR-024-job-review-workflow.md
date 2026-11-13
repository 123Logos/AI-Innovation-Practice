# ADR-024：岗位审核流（批次 B6 另一半的「审核流」子项）

- 状态：已实现（2026-11-13）
- 关联：`docs/PRD.md` FR-03 / §6.2、`ADR-011` 1.B / 2.G、`ADR-017` §5、`ADR-021` §2.3 / §2.5、
  `docs/ROADMAP.md` §1 B6、第 18 轮最终裁定 ① / ③

## 1. 背景：`ADR-011` 砍掉的三态，现在必须装回来

`ADR-011` 1.B 把 `schema-draft.sql:203` 的七态砍成 `draft` / `published`，理由写得明确：

1. `pending_review` / `approved` / `rejected` **需要审核人**，而「没有后台」（`MVP_SCOPE.md` §9）；
2. `closed` / `expired` 需要状态流转任务，而定时任务仍冻结 —— 过期改为读侧判定（2.G）。

第 1 条前提已经消失：B5（`ADR-021`）交付了 `organizations` / `organization_members` /
`audit_logs` 与四角色，`teacher` / `school_admin` / `platform_admin` 都是真实存在的角色，
`app/core/permissions.py` 是一张可判定的权限表。第 2 条前提**没有**消失，本 ADR 不动它。

同时有两条产品口径压在这一批上：

- `docs/PRD.md` FR-03 逐字：「**未经审核的岗位不可进入推荐池；过期岗位自动下架。**」
- 第 18 轮裁定 ③：「审核通过 / 驳回这种改变世界状态的操作，如果关联的岗位已过期，
  直接按 `app/core/deadline.py` 的 `is_expired` 判据拦截。」`ADR-017` §5 把这条记成
  「动作层今天没有实现物」的开工约束，本批就是它第一次有实现物。

**先说清现状的窟窿**：今天 `PUT /jobs/{job_id}/publish` 由创建者直接调用，一次请求把
未审内容推上广场，而广场是 `GET /jobs` 与匹配端点的唯一来源。也就是说「未审核的岗位不可
进入推荐池」这句话，在当前实现里**没有任何东西在守** —— 不是守得不好，是没有。

## 2. 决策

### 2.1 四态，不是七态；`approved` 不单列

| 状态 | 谁能进 | 含义 | 是否进广场 |
|---|---|---|---|
| `draft` | 创建者（新建即是） | 自己还没提交 | 否 |
| `pending_review` | 创建者提交审核 | 等审核，内容锁定（见 §2.5） | 否 |
| `published` | **审核人**通过 | 审核通过且上架 | 是 |
| `rejected` | **审核人**驳回（带理由） | 被打回，可修改后重新提交 | 否 |

状态机（全部是显式端点，没有一条是别的动作的副作用）：

```
draft ──submit-review──▶ pending_review ──approve──▶ published ──unpublish──▶ draft
  ▲                             │                                              │
  └──────── 编辑 ───────────────┘                                              │
  ▲                             │                                              │
  └──── 编辑 ──── rejected ◀─reject──────────────────────────────────────────┘
```

- **不设 `approved`**：审核通过的唯一意义就是「可以上架」，把它拆成 `approved` 再要一次
  「上架」动作，等于造一个没有入口的中间态，而每次多余的状态都是漏判的机会。
- **不设 `closed` / `expired`**：过期仍是读侧判定（`ADR-011` 2.G）。已发布岗位过了截止时间，
  库里还是 `published`，列表默认不返回、匹配带上 `is_expired` 标注 —— 这条一行不改。
  「自动下架」在本仓的实现就是「读侧不出现」，`ADR-011` 2.G 已经这么写了，本批不新增定时任务。

### 2.2 审核人：同组织的 `teacher` / `school_admin`，或跨组织的 `platform_admin`

`docs/PRD.md:28` 写「**教师**：……审核岗位和推荐结果」，`:31` 写「**平台管理员**：处理内容审核」。
因此新增权限 `JOB_REVIEW`，授予 `teacher` / `school_admin` / `platform_admin`（`student` 不含）。

- 判定分工沿用 `ADR-021` §2.3：路由层 `require_permission(JOB_REVIEW)` 判「有没有这个权限」，
  服务层判「这条岗位在不在你的审核范围」。
- **范围靠 `jobs.organization_id`**：没有这一列，「同组织审核」无法表达，只能退化成
  「任何教师可审任何学校的岗位」。该列**可空**、由服务层在**提交审核时**从
  `organization_members` 取，请求体够不到（与 `created_by_user_id` 同一做法）。
- `platform_admin` 是唯一跨组织的角色（它本来就是全平台内容审核）。
- **不能审自己**：`job.created_by_user_id == 审核人` 时拒绝（409 `JOB_SELF_REVIEW`）。
  教师角色可由邀请码获得（`ADR-023` §2.1），不挡这层，一个账号点两下就把自己的岗位
  送进广场——与 B5「不能改自己的角色」同一个理由：审自己等于没有审。
- **存量 `published` 行 `organization_id` 为 `NULL`**：不回溯、不重审、不影响可读性。
  第 18 轮裁定 ①「组织归属永久可空、不刷存量数据」在这里照办。

### 2.3 提交审核必须先绑定组织 —— 第 18 轮裁定 ① 的第一个落地点

裁定原文点名「发布岗位」属于「必须依托组织才能完成的动作」。所以：

- 没有组织的账号提交审核 → `400` + `ErrorMessage.JOB_ORGANIZATION_REQUIRED`
  （「发布岗位需要先绑定组织…」），**不**静默降级、**不**自动建组织；
- 有组织但岗位 `organization_id` 为空（存量草稿）→ 提交时补写当前组织；
- 编辑不要求组织：草稿可以随便写，只有「让世界看见」这一步被拦。

这条与 `ADR-021` §2.4 的差距登记一致：注册仍不强制填码，拦截发生在动作上。

### 2.4 `is_expired` 在动作层只此一处，三个动作都过它

`app/core/deadline.is_expired(deadline, today)` 是唯一判据，`today` 来自应用时钟。

| 动作 | 过期时 | 为什么 |
|---|---|---|
| 提交审核 | `409 JOB_EXPIRED` | 否则队列里堆的是「审核人只能驳回」的岗位，等于把无效劳动塞给别人 |
| 审核通过 | `409 JOB_EXPIRED` | **裁定 ③ 的本体**：改变世界状态前先判过期 |
| 审核驳回 | `409 JOB_EXPIRED` | 同样改变状态；且「驳回过期岗位」是假动作，真正要做的是更新截止日期 |

驳回与通过**都必须过**：裁定写的是「审核通过 / 驳回这种改变世界状态的操作」。
被 `is_expired` 拦下的审核尝试写 `result=denied` 审计（`ADR-021` §2.5 的延续），
理由记 `job_expired` —— 有人反复点通过时，这条是唯一证据。

### 2.5 编辑待审核岗位 = 退回草稿

审核人批的必须是最终内容。因此 `PUT /jobs/{id}` 命中 `pending_review` 时，先把状态退回
`draft` 再覆盖内容：审核队列里立刻看不到它，创建者改完要重新提交。

`rejected` 的岗位可以直接编辑（编辑后仍是 `rejected`，提交时进 `pending_review`）；
`published` 的岗位编辑**不改状态**（维持 `ADR-011` 1.F #4 的既有口径：内容修改不是下架）。

### 2.6 API：删掉一条直发通道，加三条，其余不动

| 端点 | 变化 | 说明 |
|---|---|---|
| `PUT /jobs/{id}/publish` | **删除** | 它是绕过审核的唯一通道。保留它，审核就只是装饰 |
| `POST /jobs/{id}/submit-review` | 新增 | 创建者；`draft` / `rejected` → `pending_review` |
| `POST /jobs/{id}/review` | 新增 | 审核人；`{decision: approve\|reject, note?}` |
| `GET /jobs/review-queue` | 新增 | 审核人的待审列表，同组织（`platform_admin` 全部） |
| `PUT /jobs/{id}/unpublish` | 保留 | `published` → `draft`；重新上架必须重新过审 |
| `GET /jobs`、`GET /jobs/{id}` | 保留 | 可见性不变：只有 `published` 对所有人可读，其余状态仅创建者与同组织审核人 |

`JobRead` 增 `review_note`（驳回理由，≤200 字，仅 `rejected` 时有值，创建人与审核人可见）。
`status` 值域从两个变四个，前端 `JobStatus` 同步。

**这是破坏性变更，如实登记**：任何按旧契约直接调 `PUT /publish` 的客户端会拿到 404。
本仓唯一调用方是自家前端，同批改完；`docs/API.md` §4 与 §7 同步修订。

### 2.7 审计扩三个动作，越权照例记 `denied`

`job.submit_review` / `job.approve` / `job.reject`。与 B5 / B6 同口径：业务与审计同一事务，
由服务层统一 `commit()`；失败整体回滚。跨组织审核尝试记 `denied` + `cross_organization`。

### 2.8 迁移 `0022`：加两列、换一条 CHECK，`downgrade` 有损但可执行

- `jobs.organization_id` `VARCHAR(36) NULL`，FK → `organizations.id` `ON DELETE RESTRICT`；
- `jobs.review_note` `VARCHAR(200) NULL`（`NULL` = 没被驳回过）；
- `ck_jobs_status` 换成四值；
- 新索引 `ix_jobs_org_status (organization_id, status)` 服务审核队列；
  `ix_jobs_status_created` / `ix_jobs_creator` 一律保留（广场与「我的草稿」还在用）。

`downgrade` 必须**先**把 `pending_review` / `rejected` 归成 `draft`，再加回两值 CHECK ——
否则库里只要有一行新状态，加约束当场失败。这一步**有损**：状态信息丢失，内容不丢，
所以在迁移 docstring 里写明，而不是假装可逆。

## 3. 后果

- 正面：「未经审核不可进入推荐池」第一次有实现物；审核动作第一次过 `is_expired`；
  「发布岗位需要先绑定组织」这条裁定第一次有拦截点；谁批的、什么时候、批的哪条，
  审计里有据可查。
- 负面：创建者的路径从「一键发布」变成「提交 → 等审核」，单人自娱自乐的场景变慢；
  多两条列、一条索引、一次状态判断。
- 风险：审核人是**同学**（教师角色可由邀请码获得，`ADR-023` §2.1），审核可能流于形式。
  本 ADR 只保证「动作留痕、范围受限」，不保证审核质量 —— 那是产品与运营问题。
- 未做（不许记成已交付）：邮箱 / 手机号作登录标识、改密。二者是 B6 的另一格，
  照第 18 轮裁定「直接走 ADR 排期」，本批零代码。

## 4. 替代方案

1. **保留 `PUT /publish`，只给管理员用** → 创建者永远发不出去，且管理员替人发布等于
   跳过提交动作，审计里分不清「谁录的」和「谁发的」。
2. **`approved` 与 `published` 分开** → 中间态没有动作入口，要么再补一个「上架」端点
   （多一次状态流转与一次权限判定），要么变成永远停在 `approved` 的死数据。
3. **不引 `organization_id`，审核人全局** → 教师可审别校岗位，与 `ADR-021` §2.4
   「跨组织不可见」直接冲突。
4. **只按裁定字面在 approve / reject 判过期，提交不判** → 队列会被过期岗位灌满，
   且每个人都得点一次才知道白干。本 ADR 把提交也拦，属同一判据的延伸，已在此显式登记。

## 5. 验收清单（判据在哪）

| 规则 | 判据 |
|---|---|
| 无组织提交审核被拦、提示绑定组织 | `tests/test_job_review.py::test_submit_requires_organization_membership` |
| 状态机非法流转 409 | `…::test_submit_only_from_draft_or_rejected`、`…::test_review_only_from_pending` |
| 学生无审核权 403 | `…::test_review_requires_review_permission` |
| 录入者不能审自己的岗位 | `…::test_creator_cannot_review_own_job` |
| 跨组织审核 404 且写 denied 审计 | `…::test_reviewer_cannot_review_another_organization` |
| 过期岗位 approve / reject 被 `is_expired` 拦 | `…::test_review_of_expired_job_is_blocked` |
| 提交过期岗位被拦 | `…::test_submit_of_expired_job_is_blocked` |
| 驳回理由回显给创建者 | `…::test_reject_stores_note_and_creator_sees_it` |
| 审核队列只出本组织、只出 pending | `…::test_review_queue_is_scoped_and_pending_only` |
| 编辑待审岗位退回草稿 | `…::test_editing_pending_job_returns_it_to_draft` |
| 三个审计动作 | `…::test_review_actions_are_audited` |
| 旧直发通道已消失 | `tests/test_jobs.py` 的 `test_publish_endpoint_is_gone` |
