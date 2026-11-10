# ADR-017：岗位过期/可用性的跨端点契约（B3 剩下的那一半）

- 日期：2026-11-09
- 状态：**已生效（含实现：本 ADR 落地一条跨端点一致性集成测试）**
- 决策人：**本 ADR 由我拍板**，授权同 `ADR-016`（老板 2026-11-09 第 3 点「ADR-A / ADR-B 我给你全部决策权与操作权」）。他此前对 B3 的要求原话是「所有读岗位的端点——列表、详情、match、requirement-match——必须走同一个过期过滤函数」，并建议「v1 选读时标注（零依赖，正确）；规模应标中，因为要加统一过滤 + 一条『过期岗位在所有端点一致』的集成断言；写时触发不建议 v1」。**本 ADR 采纳他的方向，只把「同一个过滤函数」精确成「同一个判据 + 各自的角色」**，理由见 §2.2 与 §3 的取证。
- 关联：ADR-011 2.G（过期三态的原始裁定）、ADR-013 §7.5（`remaining_time`）、ADR-014（全面解冻）、ADR-016（同批交付）

## 1. 现状（已经交付的部分，别再当新功能估）

| 事实 | 出处 |
|---|---|
| 唯一判据：`deadline` 非空且**严格早于**今天；`NULL` = 长期有效 | `app/core/deadline.py:15-21` |
| 服务层两处都调它（列表项 / 详情），**没有第二处 `deadline < today`** | `app/services/job_service.py:483-490`，被 `:515`（`JobSummary`）与 `:538`（`JobRead`）共用 |
| `is_expired` 是对外字段（详情与列表两种模型都有） | `app/schemas/job.py:235`、`:275` |
| **只有列表默认过滤**过期行，`include_expired=true` 才一起给 | `app/repositories/job_repository.py:488-490`、参数在 `app/api/v1/jobs.py:80` |
| 详情按 id 打得开过期岗位（200 + `is_expired=True`），且这条**已有测试锁着** | `tests/test_jobs.py:899`（三态）与 `:937`（陌生人读详情） |
| 匹配侧的过期表达是 `terms[remaining_time] = {value 0.0, computed True, note "expired"}` | `app/services/match_scoring.py:625-626` |
| 前端**不自己比日期**，只读后端那个布尔 | `frontend/src/stores/job.ts:32-33`、`JobDetailView.vue:6`、`JobBoardView.vue:195` |

所以「统一判据」这件事**早就成立了**，`ADR-011` 2.G 明确禁止两处各写一份。**没有成立的只有两件**：① 过期在四个读端点上**是否互相说得通**没有断言；② `include_expired` 这个概念只存在于列表，别处没有对应物，于是「过期可见性」天然按端点各自为政。

## 2. 决策

### 2.1 判据仍只有一处，且**每个读端点都带同一个过期事实**

- `core/deadline.is_expired` 继续是唯一判据。**服务层、仓储层、前端任何一处再写一次 `deadline < today`（含等价写法）都算违规**，这条由测试而不是由纪律守（§2.4）。
- 四个读端点各自的表达方式写死，**不许再造第五个字段**：
  | 端点 | 过期怎么表达 |
  |---|---|
  | `GET /jobs` | 默认**不出现**；`include_expired=true` 出现且 `is_expired=true` |
  | `GET /jobs/trash` | 出现且 `is_expired=true`（回收站不过滤 deadline，见 §2.3） |
  | `GET /jobs/{id}` | `200` + `is_expired=true` |
  | `POST /jobs/{id}/match` | `200` + `terms[remaining_time] = {0.0, true, "expired"}`；**分数照给**（过期不摧毁可比性，ADR-013 §7.5 已经把过期当成 0 分而不是「不可评估」） |
  | `GET /jobs/{id}/requirement-match` | `200`，且 `coverage` **与过期与否无关**（角标回答「这份简历命中了哪些要求」，那是写法与证据问题，不是时效问题） |

### 2.2 「过滤」只属于列表，其余端点只「标注」——这是角色差异，不是口径漂移

老板那句「必须走同一个过期过滤函数」按字面做会出事，取证两条：

1. `get_readable` 是详情/match/requirement-match **共用**的读入口（`job_repository.py:198-206` + `_visibility_clause:456-461`），一旦往里加 deadline 谓词，**写完回读也会中招**：`_reload` 走的就是 `get_readable`（`app/services/job_service.py:404-410`）。于是一个「创建时就填了过去的截止日期」的岗位会**保存成功然后立刻 404**——用户刚存的东西自己看不见，而 POST 的响应体也没了。
2. `tests/test_jobs.py:937` 锁的正是「陌生人读过期详情 = 200 + `is_expired=True`」。这条是 `ADR-011` 2.G 的原意（过期是**状态**，不是**删除**）。

所以：**过滤 = 列表的职责（它决定「现在还有什么可投」）；标注 = 其余读端点的职责（它们决定「这一条本身是什么样子」）**。判据同一个，动作不同，这不是双口径——双口径指的是同一个问题有两个答案，而这里四个端点回答的是四个不同问题。

### 2.3 回收站**不**过滤 deadline（写死，防止将来有人"顺手统一"）

回收站是 owner 自己的账本，过期与被删是两件事；把 deadline 过滤加进去，等于让「回收站里过期的岗位」凭空消失，`GET /jobs/trash` 的既有语义（`job_repository.py:284-292`，只按 `deleted_at IS NOT NULL` + 归属）会被悄悄改写。

### 2.4 一致性由一条跨端点集成断言守

实现物就一条测试（`tests/test_job_match.py::test_expired_job_reads_agree_across_every_endpoint`），**同一 fixture、同一条过期岗位、四个真实端点**，断言：

- 列表默认集合里**没有**它；`include_expired=true` 集合里**有**它且 `is_expired is True`；
- 详情 `200` 且 `is_expired is True`，`deadline` 回显与写入值逐字相同；
- 匹配 `200` 且 `terms[remaining_time]` 恰为 `{0.0, computed True, note "expired"}`，`score` 非空（过期≠不可评估，与「岗位没填要求技能 ⇒ 整个不评估」那条必须不混淆）；
- 角标 `200`，且它的 `coverage` 名字集合与详情 `skills` 名字集合**逐字相同**（复用老板给 3c 定的判据 (a)，过期与否不许改变它）；
- 反面哨兵：**同一条岗位改成「今天截止」时**，列表默认必须出现它、`is_expired is False`、`remaining_time` 的 note 变 `urgent`——防止「过期判断恒真」这种测试永远绿的空检。

## 3. 替代方案（都否了）

- **四个端点都过滤**：§2.2 的取证 1 直接把它否掉（保存成功后立刻 404 是要用户负责的 bug）。
- **写时触发自动下架**（改 `status` 或加 `closed_at`）：需要一条定时任务或一次全表扫描，Redis/Celery 至今没引入（`ADR-014` 把「禁令」变成「事实与选择」，但没把「没有必要」变成「有必要」）；而且 `status` 现在是 `draft/published` 两态 + CHECK 约束（`20261105_0011:84-87`），`tests/test_jobs_constraints.py:265` 还**主动禁止**写入 `expired` 状态。真要下架必须是新列，那是另一个 ADR + 一次迁移，v1 不做。
- **match 响应里再加一个 `meta.is_expired`**：第五个字段回答第四个问题，`ADR-013` §8 刚把「不重复表达」立为口径。
- **前端自己比日期**：`ADR-011` 2.G 与 `stores/job.ts:32-33` 都禁止，且各端时区不一致会直接产生分歧读数。

## 4. 后果与未决

- 本 ADR 的实现物是**一条测试**，不改任何行为：四个端点今天的读数本来就互相说得通，只是没人钉住它。因此**不新增错误码、不动契约字段、不迁移数据库**。
- B3 还剩**另一半**：`generated_at` 与数据新鲜度（FR-05）。`MatchMeta` 今天确实没有这格（字段我逐个核过）。它需要一个「这份岗位/这份档案最后一次被读侧组装的时刻」，来源只能是应用时钟 + 落库的 `jobs.updated_at`，**不是**「分数被算出来的时刻」——那是缓存失效问题，我们现在没有缓存。这一半我按批次留给 B3 的实现 PR，因为它牵动 `docs/API.md` 第 6 条与前端文案，比加一条测试大。
- 未决一项（需要老板，不是我拍得了的）：**过期岗位要不要继续出现在「可投递」语义里**。当前是「出现但标注」，产品口径是「已过期」角标 + `remaining_time` 0 分。如果他要的是「过期就不该再被匹配」，那是 §3 的第一条替代方案，代价见 §2.2 的取证。
