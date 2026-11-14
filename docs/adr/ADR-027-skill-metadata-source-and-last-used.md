# ADR-027：技能元数据——来源与最近使用时间（批次 B7）

- 状态：已实现（2026-11-13）
- 关联：`docs/PRD.md` FR-02 与 §98（技能含熟练度 / 最近使用时间 / 来源 / 证据）、
  `ADR-013` §1.2 与 §4（`self_rating` 的删除）、`ADR-015` §2.1（全量替换 + 软删除）、
  `ADR-011` §1.E（学生 `skills` 与岗位 `job_skills` 不合并）、
  `docs/ROADMAP.md` §2 B7、`ADR-028`（B9 测评，本批的「测评」来源占位由它兑现）

## 1. 背景：技能今天只有一个名字

`skills` 表现在是 `student_profile_id / name / created_at / updated_at`（+ `ADR-015` 的
`deleted_at` / 生成列 `active_marker`）。PRD 要的四格是「熟练度 / 最近使用时间 / 来源 / 证据」，
其中：

- **熟练度**（1–5）已在 2026-11-07 连同 `self_rating` 列一起删除（`ADR-013` §4）；
- **证据**要指向项目 / 测评 / 教师确认的凭据，而这三类实体今天要么没有 id 引用、要么还没实现；
- 剩下来源与最近使用时间，就是 B7 的全部范围。

### 1.1 坑①：「自评」这一档与被删掉的 `self_rating` 是什么关系

`self_rating` 是 1–5 的**强度**；`source='self_reported'` 是**出处**（这句话是谁说的）。
两者维度不同，不能互相顶替。所以本 ADR 的第一条决策是**明确否认「换个壳重建」**：

| | `self_rating`（已删） | `source='self_reported'`（本批） |
|---|---|---|
| 回答的问题 | 你有多熟 | 这条技能是谁说的 |
| 取值 | 1–5，连续 | 四个互斥来源之一，离散 |
| 为什么删 / 为什么加 | UI 不采集、渲染不用、库里恒为 3 —— **零信息常量** | 用户一句话能答；渲染、画像、将来的筛选都要读它；它有真实默认值（没说就是自评） |

三条口径写死，防止下一个人把壳装回去：

1. **`source` 不产生任何分值**。不许出现「自评算 0.3 分」「项目来源加权 1.2」这类换算——
   那正是 `ADR-013` §507 拒绝过的东西（把删掉的常量换个名字放回公式里）。
2. **画像里的三态不由 `source` 单独决定**（`ADR-028` §2.3）：`self_reported` 只是
   「还没有外部证据」的标记，它对应「待验证」，但「待验证」不等于「差」。
3. **不重建熟练度**。将来真要做，是**另一列**，且必须先有可信来源（否则又是零信息常量）。

### 1.2 坑②：「最近使用时间」今天无处可取

写入是**全量替换**：`PUT /profiles/{id}` 先把所有活跃技能盖 `deleted_at=now`，再按名称插新行
（新 UUID）。`_staggered()` 把同批 `created_at` 错开 1 微秒，**只为让读取顺序稳定**。
于是今天 `created_at == updated_at == 上次保存时间`——它回答的是「我上次点保存是什么时候」，
与「我上次用 Python 是什么时候」毫无关系。

三种做法，只有第三种成立：

| 做法 | 为什么不行 |
|---|---|
| 让 `updated_at` 改表「最近使用」 | 一列两种含义；「保存」本身不是「使用」。改完全仓再没人能回答「这份档案上次保存是什么时候」——而软删除、回收站、审计都在依赖那个含义 |
| 从项目经历正文里推断（技能名最近出现的经历结束时间） | 只覆盖「恰好写在经历里」的技能；用户改一个字就变；推错了没法解释，用户也不知道该改哪里 |
| **用户自己填 + 系统保住「首次加入」** | 「最近使用」本来只有用户知道，产品不该替他知道 |

### 1.3 「首次加入」为什么值得单独一列

用户问的第二个问题通常是「这条技能我什么时候开始写的」。全量替换会让这个答案每次保存都被清零，
而这恰恰是**写入历史能答**的（不是推断）：按 `skill_name_key(name)` 在所有历史行
（含软删除行）里找最早的一条，取它的 `first_added_at`；找不到才算 `now`。

**为什么不为它单开一张 `skill_events` 表**：事件流要有生产者。今天没有任何地方在产生
「使用技能」的事件——项目经历不回写技能、测评还没有（`ADR-028` 才有）。建一张表只会得到
一张空表加一句「以后填」的谎话。等 B9 的测评真回写时，事件表才有第一条数据（§5 遗留）。

## 2. 决策

### 2.1 数据模型：`skills` 加三列 + 一个 CHECK（迁移 `0025`）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `source` | `VARCHAR(24)`，`NOT NULL`，`server_default 'self_reported'` | 否 | `ck_skills_source` ∈ `self_reported` / `assessment` / `project` / `teacher_confirmed` |
| `last_used_on` | `DATE` | 是 | **用户声明**的最近使用日（前端按月份选，库按日期存） |
| `first_added_at` | `DATETIME` | 是 | 首次加入这份档案的时间，跨全量替换保留 |

三条实现口径：

1. **`source` 用字符串而不是 TINYINT 枚举**：与 `paper_size` / `theme` 同一口径
   （加一种来源不必迁移），白名单在 pydantic `Literal` 与 DB `CHECK` 两处，读侧遇到未知值
   一律降级成 `self_reported`（不抛 500）。
   **宽度 24 是被测试逼出来的**：第一版按「看着差不多」写成 `VARCHAR(16)`，
   而 `teacher_confirmed` 是 **17 个字符**——`test_writable_sources_round_trip`
   第一次跑就 `1406 Data too long`。留 24 是给将来的第五个取值留余量；
   `VARCHAR` 在 InnoDB 按实际长度存，宽一点不占空间，窄一点就是一次写入失败 + 一次紧急迁移。
2. **存量回填**：`first_added_at = created_at`，`source` 走默认值，`last_used_on` 留 `NULL`
   （不知道就说不知道，不拿保存时间冒充）。
3. **`downgrade` 是有损的**：三列直接 drop，用户填过的日期与来源会丢。迁移注释里写明，
   不允许写成「可回退」了事。

### 2.2 来源的语义与可写集合

| 值 | 含义 | 谁可以写 |
|---|---|---|
| `self_reported`（默认） | 用户自己说会，没有任何外部证据 | 客户端 |
| `project` | 来自项目经历 | 客户端**选**（不做自动溯源，见 §5） |
| `teacher_confirmed` | 教师确认过 | 客户端**选**（B7 只校验「在写自己的档案」，不校验「谁给的确认」——那要一条确认记录） |
| `assessment` | 标准化测评验证过 | **仅服务端**（`ADR-028` §2.6 的提交动作） |

`assessment` 在 B7 是**枚举占位**：读得到，客户端**写不进去**——传了就 422。
理由：允许客户端自己声明「已被测评验证」，等于让人在没有测评的情况下伪造一个更强的来源；
而「先收着以后再说」正是本批要关掉的那种静默降级。

### 2.3 端点契约变化

- `SkillInput` 增加 `source`（可选）与 `last_used_on`（可选 `date`）；
- `SkillRead` 回 `source` / `last_used_on` / `first_added_at`（`first_added_at` 只读）；
- `PUT /profiles/{id}` 仍是整体覆盖，但**两格新元数据的省略语义不同**：

| 字段 | 省略时 | 为什么 |
|---|---|---|
| `last_used_on` | **清空** | 纯用户数据，与 `target_role` 同一条：「没说就是没有」 |
| `source` | **沿用库里同名技能已有那一档**（新技能落 `self_reported`） | 四档里有一档是**服务端写的**（`assessment`）。若省略也当清空，用户测完评之后改一个字再保存，测评写下的来源就被自己的保存动作抹成「自评」——`ADR-028` 的提交动作会因此变成一次性装饰。客户端对这类行**整格不传**（它本来也没有资格重新声明） |

这不是「又发明了一条特例」：`paper_size` / `theme` / `layout_config` 早就是三态
（`_resolve_*` 的「不传即保留、显式 null 即恢复默认」），`source` 只是第三个沿用这条
既有先例的字段；而 `last_used_on` 走的是 `target_role` 那条覆盖语义。两者都写在这里，
是因为**同一张请求体里两种省略语义并存**，不写下来下一个人一定会按错的假设实现。

实现上「省略即沿用」只解析一次（`ProfileService._save_content`，要读一次活跃行），
仓储拿到的 `SkillDraft.source` 已经是具体值——同一条规则两处实现必然分叉。
顺带一条同样的坑：`ProfileService._content_unchanged` 的 no-op 检测必须比
`(名称, 来源, 最近使用)` **三元组**，只比名称会让「只改了来源」被判成没变更、
静默丢弃用户的编辑（第一版就是这么写的，被 `test_metadata_only_change_is_saved` 撞出来）。

两条 422：

| 情形 | 结果 |
|---|---|
| `source` 不在可写集合里（含 `assessment`、拼错的值） | `422 VALIDATION_ERROR`，字段级文案说清「测评来源由测评提交时写入，不能手动选择」 |
| `last_used_on` 晚于今天（东八区） | `422 VALIDATION_ERROR`，与经历的「开始时间不得早于今天」同一口径 |

### 2.4 渲染与匹配今天都不读 `source`

- 生成器只渲染技能名（`ADR-013` §1.2 的现状不变），所以本批**不影响任何已生成的简历**；
- 匹配公式不按 `source` 加权（见 §1.1 第 1 条）。「自评技能算不算数」是画像与匹配**共同**的产品问题，
  单方面在匹配里打折会让同一份档案在简历页与匹配页得到两种人格。

## 3. 后果

**好处**

- 「这条技能谁说的」可解释，且默认值是真话（没人核过就是自评）；
- 「最近使用」有了唯一可信来源（用户自己）；
- 「首次加入」不再被保存动作冲掉——这是本批唯一需要跨历史行查询的地方；
- 三列全部可空/有默认值，存量与旧客户端零破坏。

**代价**

- `first_added_at` 的保留要在每次全量替换时多一次按名称分组的查询；
- 用户可能把 `last_used_on` 填错，而系统**没有**纠正手段（没有证据链），只能靠用户自己改；
- `source` 今天大多是默认值——这是诚实的现状，不是功能没做完；
- 迁移的 downgrade 有损。

## 4. 验收判据

| 判据 | 用例 |
|---|---|
| 不传 `source` 时落库为 `self_reported` | `tests/test_skill_metadata.py::test_source_defaults_to_self_reported` |
| 三个可写来源原样落库并回读 | `::test_writable_sources_round_trip` |
| **只改来源也要落库**（no-op 检测比三元组） | `::test_metadata_only_change_is_saved` |
| 客户端传 `assessment` → 422（且文案可动手） | `::test_assessment_source_is_not_writable_by_clients` |
| 未知来源值 → 422 | `::test_unknown_source_is_rejected` |
| **省略 `source` 沿用服务端写的 `assessment`**（显式自评仍可覆盖） | `::test_omitted_source_keeps_server_written_assessment` |
| 读侧未知来源降级成 `self_reported`，不 500 | `::test_unknown_source_is_read_as_self_reported` |
| `last_used_on` 如实落库、可清空 | `::test_last_used_on_round_trip_and_clear` |
| `last_used_on` 是未来 → 422（今天放行） | `::test_last_used_on_cannot_be_in_the_future` |
| **反复保存不清零 `first_added_at`** | `::test_first_added_at_survives_resaves` |
| **删掉再加回来仍取最早那条** | `::test_first_added_at_survives_removal_and_readdition` |
| 存量行回填 `first_added_at = created_at`（且列精度是 6） | `::test_migration_backfills_first_added_at` |
| 来源不影响生成物（同名同渲染） | `::test_source_does_not_change_rendered_resume` |

## 5. 遗留与下一步

1. **`source='assessment'` 的生产者**：`ADR-028` §2.6 的测评提交会写它（只改已有技能，不新建）；
2. **`source='project'` 的自动溯源**：需要「经历 → 技能」的引用（`evidence_type/evidence_id`
   或一张关联表），今天只有标题匹配，属证据链；
3. **教师确认的真实性**：要有确认记录 + 教师身份校验（B5 有角色，但没有确认动作）；
   → **2026-11-14 已交付**：`POST /api/v1/teacher/skills/confirm`（`app/api/v1/teacher_tools.py::confirm_skill`）。
   落成形态与本文的口径对齐：唯一新增的 `skills.source` 生产者就是它（写 `teacher_confirmed`），
   权限是独立的 `skill:confirm`（不与「审岗位」「看板」「下载」共用），动作层先过
   `require_organization`（`OrganizationAction.CONFIRM_SKILL`），**跨组织一律 404**；
   档案归属的判定写在 SQL 里（`TeacherToolsRepository.student_profile_in_organization`），
   确认记录进 `audit_logs`（`skill.teacher_confirm`，`detail` 记 `previous_tag_source` /
   `previous_skill_source`——标签是覆盖写，不记就查不到「原来那条证据是什么」）。
   两处「只升不建」：不新建技能行（学生没写的技能不会被老师塞进简历），
   但 `profile_tags` 会新增一条 `source='teacher'` 的标签（那是老师的断言，不是学生的声明）。
4. **熟练度**：若重新引入，必须先有可信来源，且不得回到「UI 不采集、库恒为 3」的老路；
5. **`skill_events` 事件表**：等测评真的回写技能之后再建，届时它才有第一条数据。
