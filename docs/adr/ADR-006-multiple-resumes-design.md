# ADR-006：多份简历与档案库的技术方案选型（草案）

- 状态：**草案（待人类拍板，尚未选定任何方案）**
- 日期：2026-11-03
- 决策人：本项目人类负责人
- 关联需求：`docs/PHASE2_BACKLOG.md` §二「多份简历 / 档案库」
- 关联文档：`docs/adr/ADR-005-phase2-high-priority-design.md`（同批 Phase 2 选型）、
  `docs/adr/ADR-004-auth-jwt.md`（鉴权/归属语义）

> **本文档不替人类做决定。**
> 每个决策点只提供「代码事实 → 选项 → 代价」三样东西。
> 凡标 **【硬约束】** 的，是技术上绕不过去的依赖，不是倾向；
> 凡标 **【待拍板】** 的，是偏好问题（成本 vs 语义、交互习惯），一律留空，收在第 9 节清单里。
>
> **请先读第 2 节。** 它纠正了两处会直接影响选型的事实误解：
> 一是「现在是 1:1」并不准确（`resumes` 其实已经是 1:N，只是 N 行内容同源）；
> 二是「多份简历」在用户侧其实是**两个不同需求**，只满足其中一个的方案会被误判为「做完了」。

---

## 1. 背景与范围

需求池原文：

> **多份简历 / 档案库**
> - 现状：用户与简历是一对一关系。
> - 需求：允许一个用户创建并管理多份不同版本的简历（如「投后端」「投算法」两版）。
> - 待评估：数据模型从一对一改为一对多，涉及主表唯一约束、前端切换与命名交互。

本轮任务：只读评估 + 起草 ADR。**不写业务代码、不改库结构、不新增迁移、不 commit。**

---

## 2. 只读核实结论（代码事实，非推测）

### 2.1 「5 张表」实际是 6 张业务表

需求文档与任务描述都提到 5 张表；实测 `alembic/versions/` 建了 **6 张业务表**，
外加 Phase 2 · D2.1 的 `refresh_tokens`（与本次改造无关，仅列全）：

| 表 | 主键 | 关键约束 | 关联 |
|---|---|---|---|
| `users` | `id` | `UNIQUE(username)`、`UNIQUE(email)` | — |
| `student_profiles` | `id` | **`UNIQUE(user_id)`** ← 关键 | `user_id` → `users.id`，`ON DELETE RESTRICT` |
| `skills` | `id` | `UNIQUE(student_profile_id, name)`、`CHECK self_rating BETWEEN 1 AND 5` | `student_profile_id` → `student_profiles.id`，RESTRICT |
| `experiences` | `id` | `CHECK end_date IS NULL OR end_date >= start_date` | 同上，RESTRICT |
| `educations` | `id` | `CHECK degree IN ('专科','本科','硕士','博士')`、`CHECK edu_end >= edu_start` | 同上，RESTRICT |
| `resumes` | `id` | **无唯一约束**，`content_html` 为 `LONGTEXT` | `student_profile_id` → `student_profiles.id`，RESTRICT |
| `refresh_tokens` | `id` | `UNIQUE(token_hash)` | `user_id` → `users.id`，`ON DELETE CASCADE` |

来源：`alembic/versions/20261023_0001_initial_phase1.py`（前五张）、
`20261102_0002_educations.py`、`20261103_0006_refresh_tokens.py`。

真实的基数关系：

```
users 1 ──── 1 student_profiles 1 ──── N resumes
                    │
                    ├── N skills
                    ├── N experiences
                    └── N educations
```

**真正卡住「多份档案」的不是 `resumes`，而是 `student_profiles.user_id` 上的 `UNIQUE`**
（DDL 第 53 行 `sa.UniqueConstraint("user_id")`；映射层 `student_profile_repository.py:27`
写成 `unique=True`）。技能/经历/教育的所有行都靠 `student_profile_id` 归属，
所以「档案只能有一份」这一条约束，把整棵子树锁成了单例。

### 2.2 现在的「1:N」是假象：N 行内容同源

`resumes` 本身没有唯一约束，`POST /resumes/generate` 每次都 `INSERT` 一行新记录
（`resume_repository.py:33 create_resume`，全类仅此一个方法）。但请求体只收一个 `title`：

```python
# app/schemas/resume.py:58
class ResumeGenerateRequest(BaseModel):
    """生成请求体只剩标题；档案、技能、经历由服务层按登录用户从库中读取。"""
    title: str = Field(min_length=1, max_length=200)
```

服务层按 `user_id` 反查唯一档案，取全部技能/经历/教育交给生成器
（`resume_service.py`）。因此同一用户连点三次「生成简历」，会得到
**3 行 `content_html` 完全相同、只有 `title` 与 `id` 不同的记录**。

同时，`resumes` 目前**没有读取入口**：

| 能力 | 现状 |
|---|---|
| 列出某用户的简历 | 无（`ResumeRepository` 没有 `list`） |
| 按 id 读一份简历 | 无 |
| 重命名 / 删除 | 无 |
| 覆盖已生成的内容 | 无（生成永远是新建行） |

**结论：现在的「多份」是多次快照的审计痕迹，不是可独立编辑的版本。**
要做「投后端 / 投算法两版」，改的必须是**数据源头**，而不是加一个列表接口。

### 2.3 写入是「全量替换 + 子表先删后插」，子记录 id 每次都变

`student_profile_repository.py:184-188`：

```python
self._session.execute(delete(Skill).where(Skill.student_profile_id == target.id))
self._session.execute(delete(Experience).where(Experience.student_profile_id == target.id))
self._session.execute(delete(Education).where(Education.student_profile_id == target.id))
# 随后全部用 uuid4() 重新插入
```

这条事实直接排除了一类方案（见 §3.4 的「多对多共享内容」）：
**任何以「子记录 id」为外键指向 `skills` / `experiences` / `educations` 的设计，
在用户点一次「保存修改」后就会全部悬空。** 想共享，必须先让子记录 id 稳定下来
（改成按业务键 upsert），那是另一项改造，不在本 ADR 默认范围内。

### 2.4 前端状态是彻底的「单例」

| 位置 | 事实 |
|---|---|
| `stores/resume.ts:363` | `const draft = reactive<ProfileDraft>(createEmptyDraft())` —— 全应用一份 |
| `stores/resume.ts:364` | `savedProfile` 只有一个 `ref<ProfileRead \| null>` |
| `stores/resume.ts:72,207` | 本地草稿 key 是 `resume_draft:${ownerId}`，**只有用户维度，没有简历维度** |
| `stores/resume.ts:691` | `generate(title = '我的简历')`，默认标题写死 |
| `components/StylePanel.vue:56` | 调用处**硬编码** `store.generate('我的简历')` |
| `router/index.ts:18-41` | 只有 `/`（编辑器）、`/login`、`/register`，无列表页、无详情页 |
| `views/ResumeEditorView.vue:83` | 布局 `grid-template-columns: 340px minmax(420px, 1fr) 220px` |

`stores/resume.ts:821` 的 `reset()` 注释已经把切换时的坑写明了：

```ts
function reset(): void {
  // 先解绑归属，再清空 draft：否则监听器会把空白骨架写成上一个人的草稿。
  draftOwnerId.value = null
  ...
}
```

**切换简历与切换用户是同一类问题，而且更严重**：key 里没有简历 id，
A 的草稿会被 B 读到；反过来，切换后若不先解绑就回填，监听器会把 B 的空骨架
写进 A 的草稿槽位。这块必须是显式设计，不能靠「切完再 load 一下」蒙过去。

### 2.5 完整性校验有两处，都假定「只有一份」

| 位置 | 语义 |
|---|---|
| 后端 `resume_service.py` | `full_name` / `contact` 为空 → 400；无技能或无经历 → 400 |
| 前端 `stores/resume.ts:535 validateForGenerate()` | 同一套规则的中文版，另含邮箱格式、日期未来限制 |
| 前端 `stores/resume.ts:606 validateForSave()` | 只管「能不能落库」，不拦完整性（保存 422 修复轮的产物） |

改造后这两处都要从「当前用户的档案」改成「当前这份简历的内容」，
且 `ProfileIncompleteError`（`resume_service.py`）的判定基准会变。
这不是附带的文案调整，是**语义迁移**。

### 2.6 先把「多份」拆成两个需求（这一节决定选型方向）

用户说「投后端一份、投算法一份」，这句话背后可能指两件不同的事：

| 编号 | 需求 | 用户行为 | 必要能力 |
|---|---|---|---|
| **R1** | 多份**可独立编辑**的简历草稿 | 打开「后端版」改技能顺序；再打开「算法版」换项目描述；两份互不影响 | 内容（技能/经历/教育）必须能按版本各自编辑 |
| **R2** | 一份档案 + 多个**生成快照/归档** | 把当前档案导出成「投后端 11 月版」「投算法 11 月版」留档、对比、重新下载 | 只要生成物可列表、可重命名、可回看即可 |

这两个需求的实现代价差别极大：

- **R2 几乎不用改数据模型**，只要给 `resumes` 加读接口 + 列表页；
- **R1 必须动 `student_profiles.user_id` 的唯一约束**，并决定内容归属到哪一层。

需求池写的是「创建并管理多份不同版本的简历（投后端/投算法）」，字面更接近 R1。
但**这份 ADR 不替人类判定到底要 R1 还是 R2** —— 这是第 9 节的 Q1。
下面三个方案按「R1 满足度」从高到低排列，选 R2 的话方案选择会大幅简化。

---

## 3. 决策一：数据模型

### 3.1 方案 A（彻底重构）：简历成为聚合根，档案退化为「基本信息」

**思路**：`student_profiles` 只留「人的基本信息」（姓名 / 手机 / 邮箱），
内容（技能、经历、教育）全部下沉到「简历」层，每份简历各自持有一份。

**表结构变更**

| 动作 | 表 | 说明 |
|---|---|---|
| 保留 | `student_profiles` | 只保留 `full_name` / `email` / `contact`（`grade` 早已下架，可顺手删列） |
| 新建 | `resume_documents` | `id` / `user_id` / `title` / `target_role`(可空) / `content_html`(可空，未生成时为 NULL) / `is_default` / `created_at` / `updated_at` |
| 新建 | `resume_skills` | 结构同 `skills`，外键改 `resume_id`，`UNIQUE(resume_id, name)` |
| 新建 | `resume_experiences` | 结构同 `experiences`，外键改 `resume_id` |
| 新建 | `resume_educations` | 结构同 `educations`（含 `degree` CHECK），外键改 `resume_id` |
| 废弃 | `skills` / `experiences` / `educations` | 过渡期保留只读，一个发布周期后删除 |
| 改造 | `resumes` | 与 `resume_documents` 合并（见下方【待拍板】），或保留为「历史生成物」 |

新子表的外键建议 `ON DELETE CASCADE`（与 `refresh_tokens` 一致），
而不是现状的 `RESTRICT`：否则「删除一份简历」要应用层先删三张子表，漏一张就报 1451。

**【待拍板】`resumes` 与 `resume_documents` 是否合并成一张？**
- 合并：`resume_documents` 就是新的 `resumes` 表，`content_html` 变可空（草稿态还没生成）。
  好处是一张表说完「一份简历」；代价是重命名旧表 / 或原地加一堆列。
- 不合并：`resume_documents`（可编辑内容）+ `resumes`（生成物，保留 `resume_id` 语义）。
  好处是「编辑」与「生成」解耦，历史生成物天然保留；代价是两层 id，前端要同时持两个。

**API 与前端改动点（详见 §4 / §5）**

- 读：`GET /resumes` → 列表；`GET /resumes/{id}` → 完整内容（基本信息 + 三组子项）。
- 写：`PUT /resumes/{id}`（全量替换，同今天 `PUT /profiles/me` 的语义）。
- 基本信息：`PUT /profiles/me` 保留，但**只写姓名/手机/邮箱**。
- 前端：`draft` 拆成 `profileDraft` + `resumeDraft` 两部分；「保存修改」一次点击
  要么发两个请求（基本信息 + 内容），要么后端提供 `PUT /resumes/{id}` 顺带写基本信息。
  **这是 A 最不显眼的一笔成本**：今天的「一次点击 = 一次 PUT」会被打破。

**利**

1. 语义最干净：一份简历是一个自包含文档，另一份简历改什么都不影响它。
2. 完整满足 R1，也为 R2 打好基础（生成物天然挂在文档上）。
3. 未来扩展顺：模板选择、投递记录、版本历史、按岗位定制，都能挂在 `resume_documents` 上。
4. 手机号/邮箱只存一份（在 `student_profiles`），不会出现「三份简历三个手机号」。
5. 删掉 `grade`、清掉 `major` 这类历史包袱的机会窗口。

**弊**

1. **改动面最大**：新 4 张表 + 3 张旧子表退役 + 仓储/服务/接口/前端全面重写。
2. 迁移是**搬数据**（INSERT ... SELECT 三次），不是加列，出问题回滚成本高。
3. 基本信息与内容分表后，「保存」从单请求变双请求，错误处理与 `dirty` 语义都要重排。
4. 一次生成要读 5 张表而不是 4 张，`ResumeService` 构造器再添一个仓储。
5. 过渡期代码里同时存在 `skills` 与 `resume_skills`，新人容易写错表。

**回滚**：可回滚但**有损**。`downgrade` 只能把「每用户的默认简历」合并回
`student_profiles` 子树，用户已建的其余简历内容会丢。必须在 downgrade 文档里写明。

---

### 3.2 方案 B（轻量改造）：主档案 + 生成快照

**思路**：`student_profiles` 仍是唯一可编辑的内容源；`resumes` 增加一份
**内容快照**，让每条生成记录能独立复现，并补齐列表/重命名/删除能力。

**表结构变更**

| 动作 | 表 | 说明 |
|---|---|---|
| 加列 | `resumes` | `content_snapshot JSON NULL`（生成时的技能/经历/教育/基本信息） |
| 加列 | `resumes` | `snapshot_version SMALLINT NOT NULL DEFAULT 1`（结构演进时可判读） |
| 加列 | `resumes` | `profile_updated_at DATETIME(6) NULL`（生成时档案的 `updated_at`，可判「快照已过期」） |
| 加列 | `resumes` | `is_archived TINYINT(1) NOT NULL DEFAULT 0`（归档不删） |
| 索引 | `resumes` | `INDEX (student_profile_id, created_at)` 支撑列表倒序 |
| **不动** | `student_profiles` / 三张子表 | 一行不改（**这是 B 最大的卖点**） |

**为什么 B 不能靠「关联表」共享**：见 §2.3 —— 子记录 id 每次保存都变，
外键必然悬空。所以 B 的快照只能是 **JSON 冻结副本**，不能是 id 引用。

**利**

1. 表结构改动 = 4 个 `ADD COLUMN` + 1 个索引，**零数据搬运**，迁移极轻、易回滚。
2. 后端改动集中在 `ResumeRepository`（新增 `list` / `get` / `rename` / `delete`）
   与一个 `POST /resumes`（或复用 `generate`），`ProfileService` 与三张子表零改动。
3. 快照冻结后，档案后来被改也不会让历史简历「变样」，且能显示「这份快照基于
   2026-11-03 的档案」。
4. 顺带把 R2 做满：列表、重命名、归档、对比、重新下载都成立。

**弊（关键，必须点明）**

1. **它不满足 R1。** 用户仍然只有一份可编辑内容。想「投后端」和「投算法」两版并存，
   只能改一次档案 → 生成一次 → 再改回去 → 再生成一次。两份无法**并行编辑**。
   如果人类判定需求是 R1，B 只算做了一半。
2. 「列表里 3 条长得一模一样的简历」这个现状问题，B 只是把它们变成「3 条带快照的
   历史记录」，用户仍然要靠 `title` 和 `created_at` 区分。
3. `content_snapshot` 是 JSON，无法用 SQL 查询/约束（比如「哪份简历写了 Python」），
   也无法做字段级校验；只能整包读写。
4. 同一份内容在 `skills` 表与 `content_snapshot` 里各存一遍，存在漂移风险，
   需要明确的「以谁为准」规则（生成物只读、只作留档）。
5. 生成即冻结，用户若想「在已生成简历上微调一行字」，B 没有后路
   （因为编辑入口仍是主档案，改了会波及所有未冻结的语境）。

**回滚**：最轻。`downgrade` 删列删索引即可，`student_profiles` 从未被动过，数据无损。

---

### 3.3 方案 C（最省）：放开 1:1，让「档案」本身成为版本

**思路**：不新建任何表，把 `student_profiles` 从「用户的唯一档案」改成
「用户的一份简历档案」——即允许同一 `user_id` 有多行。

**表结构变更**

| 动作 | 表 | 说明 |
|---|---|---|
| 删约束 | `student_profiles` | `DROP INDEX user_id`（该唯一索引由 `sa.UniqueConstraint("user_id")` 生成，索引名需 `SHOW INDEX FROM student_profiles` 实测确认，不要照抄） |
| 加列 | `student_profiles` | `title VARCHAR(200) NOT NULL DEFAULT '我的简历'` |
| 加列 | `student_profiles` | `is_default TINYINT(1) NOT NULL DEFAULT 0` |
| 加索引 | `student_profiles` | `INDEX (user_id, is_default)`，支撑「列表 + 取默认」 |
| **不动** | `skills` / `experiences` / `educations` / `resumes` | 一行不改，`resumes` 仍挂 `student_profile_id`，天然成为「这一份简历的生成物」 |

**这是三个方案里改动面最小的**：三张子表、`resumes`、生成链路、`PUT /profiles/me`
的请求体格式全部保持不变，只是 URL 从 `/profiles/me` 变成 `/profiles/{id}`。

**利**

1. 迁移几乎只有 3 条 DDL，**零数据搬运**，存量每个用户自动就是「一份默认简历」。
2. `PUT /profiles/me` 的整份请求体可以原样复用（`ProfileUpsertRequest` 不动），
   前端 `toUpsertPayload()` / `validateForSave()` / `validateForGenerate()` 全部保留。
3. 生成链路零改动（`ResumeService` 仍按 `student_profile_id` 取子表），只是入参换成
   `profile_id` 而非 `user_id`。
4. 完整满足 R1：两份简历是完全独立的两行，编辑互不影响。
5. 删掉一份简历 = 删一行 + 三张子表级联，`resumes` 用 RESTRICT 也不会挡（只要先删生成物或干脆保留）。
6. 可在一次迭代内落地，风险可控，失败可快速回退。

**弊**

1. **命名失真**：`student_profiles` 从此实际是「简历」，字段 `student_profile_id`
   遍布四张表。要么接受这个名不副实（靠注释兜），要么再加一次重命名迁移（成本上升）。
2. **基本信息重复**：姓名/手机/邮箱在每份简历里各存一份。用户改手机号要改 N 遍，
   否则就会出现「投后端那份还是旧号码」。这是 C 最真实的产品缺陷，
   也是用户能直接感知到的 bug 来源。
3. `is_default` 是「应用层保证唯一」的软约束，MySQL 无法用部分唯一索引表达，
   并发下可能出现两条 `is_default=1`，需要事务内加锁或后续判重修复。
4. 未来若要加「投递记录」「版本历史」「模板选择」，这些字段会全部堆进
   `student_profiles`（它已经背了 `grade` 这种历史包袱），继续膨胀。
5. `resumes` 的语义变模糊：它是「某份档案的生成记录」，而档案已经是简历，
   等于多了一层不必要的嵌套。

**回滚**：删列删索引即可，但**若用户已建多份，重新加回 `UNIQUE(user_id)` 会失败**。
downgrade 必须先按 `is_default` 删掉多余行（**数据删除，必须在 downgrade 里显式
声明并让运维知道**），否则迁移直接报 1062。

---

### 3.4 方案 D（已评估后不推荐）：多对多「内容库 ↔ 简历」

**思路**：技能/经历/教育成为用户级「内容库」，简历与内容库之间建关联表，
一份经历可被多份简历引用并各自排序。

**为什么在现状下不可行**（这是事实判断，不是偏好）：

1. `replace_profile()` 每次保存都 `delete` + `uuid4()` 重插（§2.3），
   关联表里的 `skill_id` 会全部指向已删除的行。要支持共享，必须先把子表写入
   改成「按业务键 upsert、id 稳定」，这是一项独立改造。
2. 共享语义与用户预期相悖：用户在「投后端」版里删掉某段经历，
   会连带影响「投算法」版 —— 而 R1 的核心诉求恰恰是「互不影响」。
3. 关联表还要解决排序（`position` 列）、孤儿清理、内容库管理 UI，
   工期与 A 相当但用户价值更低。

如果要的是「一处维护、多版引用」，那属于另一个需求（内容库同步），
应单独立项。**本 ADR 只把它列为「已考虑并排除」，不参与第 9 节的拍板。**

### 3.5 四方案对比

| 维度 | A 彻底重构 | B 主档案+快照 | C 放开唯一约束 | D 多对多 |
|---|---|---|---|---|
| 满足 R1（两份独立编辑） | ✅ | ❌ | ✅ | ⚠️（共享即互相影响） |
| 满足 R2（多快照/归档） | ✅ | ✅ | ✅（天然多行） | ⚠️ |
| 新增表 | 4 | 0 | 0 | 1 |
| 改动/新建的列 | 多（含删列） | 4 个 `ADD COLUMN` | 2 个 `ADD COLUMN` | 1 个 `ADD COLUMN` |
| 数据搬运（INSERT…SELECT） | 3 次 | 无 | 无 | 无 |
| 后端改动面（文件数，估算） | ≈ 20 | ≈ 10 | ≈ 9 | ≈ 18 |
| 前端改动面（文件数，估算） | ≈ 9 | ≈ 7 | ≈ 7 | ≈ 8 |
| 基本信息重复存储 | 否 | 否 | **是**（N 份副本） | 否 |
| 一次「保存」是否仍为单请求 | **否**（写两张表） | 是 | 是 | 是 |
| 迁移回滚是否无损 | 否 | **是** | 是（需删多余行后加回约束） | 是 |
| 语义清晰度 | 高 | 中 | 低（命名失真） | 中 |
| 未来扩展（模板/投递/版本） | 顺 | 中（JSON 挡路） | 差（字段继续堆） | 中 |
| 与现有代码的兼容度 | 低 | **高** | 高 | 低 |

> 文件数用 `≈` 是因为它取决于「是否顺手清理 `grade`」等可选动作，只作为量级参考，
> 不是承诺。

---

## 4. 决策二：API 契约变更

### 4.1 资源命名：`/profiles` 还是 `/resumes`？

现状是两个 router 并存：`app/api/v1/profiles.py`（prefix `/profiles`）与
`app/api/v1/resumes.py`（prefix `/resumes`），由 `app/api/v1/__init__.py` 汇总。
这意味着**`/resumes` 这个前缀已经被「生成」占用了**，直接往上加 CRUD 会造成
「`GET /resumes` 是列表、`POST /resumes/generate` 是生成、`GET /resumes/{id}` 是详情」的混杂。

三个可选走法：

| 走法 | 形态 | 适合 |
|---|---|---|
| N1 复用 `/resumes` | `GET/POST /resumes`、`GET/PUT/DELETE /resumes/{id}`、`POST /resumes/{id}/generate` | 方案 A（简历是聚合根） |
| N2 换到 `/profiles` | `GET/POST /profiles`、`GET/PUT/DELETE /profiles/{id}`、生成仍 `POST /resumes/generate`（body 加 `profile_id`） | 方案 C（档案即版本） |
| N3 双前缀并存 | `/profiles/{id}` 管内容，`/resumes` 管生成物 | 方案 B（主档案 + 快照） |

【待拍板】这里没有技术优劣，只有「前端调用习惯」与「文档一致性」的取舍。
但有一条 **【硬约束】**：不管选哪个，`{id}` 路径参数都必须做归属校验，
且**查询语句就该带 `user_id` 条件**，不能「先按 id 查出来、再在 service 里比 user_id」——
后者一旦有人新写一个 handler 忘了比，就是水平越权。建议仓储签名直接写成
`get_for_user(user_id, resume_id)`，让「漏判」在编译期就写不出来。

### 4.2 端点草案（按方案分列，不预设采用哪套）

**A 版（`/resumes` 为聚合根）**

```
GET    /resumes                      → { items: [ {id, title, is_default, updated_at, generated_at} ] }
POST   /resumes                      → 新建（body 可选 copy_from: <id>）
GET    /resumes/{id}                 → { resume: { ...基本信息, skills, experiences, educations, content_html } }
PUT    /resumes/{id}                 → 全量替换内容（body 同今天的 ProfileUpsertRequest）
DELETE /resumes/{id}                 → 删除
POST   /resumes/{id}/generate        → 生成并写入该简历的 content_html
GET    /profiles/me                  → 降级为「默认简历的基本信息」只读别名（带 Deprecation 头）
```

**C 版（`/profiles` 为聚合根）**

```
GET    /profiles                     → { items: [ {id, title, is_default, updated_at} ] }
POST   /profiles                     → 新建（body 可选 copy_from: <id>）
GET    /profiles/{id}                → 同今天的 GET /profiles/me 返回结构，多 title/is_default
PUT    /profiles/{id}                → 同今天的 PUT /profiles/me 请求体，一字不改
DELETE /profiles/{id}                → 删除（连带子表）
POST   /resumes/generate             → body 从 {title} 变成 {profile_id}（或不传=默认）
GET    /profiles/me                  → 保留为「默认那份」的别名，前端可暂不改
```

**B 版（内容与生成物分离）**

```
GET    /profiles/me                  → 不变
PUT    /profiles/me                  → 不变
GET    /resumes                      → 列表 { items: [ {resume_id, title, created_at, snapshot_version} ] }
GET    /resumes/{id}                 → 详情（content_html + content_snapshot）
PATCH  /resumes/{id}                 → 只改 title / is_archived
DELETE /resumes/{id}
POST   /resumes/generate             → 不变（生成时额外落一份 snapshot）
```

### 4.3 【待拍板】生成语义：新建一行，还是覆盖当前简历？

现状是**每次生成都 INSERT 新行**。到了「一份简历」的模型里，这个行为会让
「我的后端版简历」在列表里变成 5 条同名记录。

| 选项 | 行为 | 代价 |
|---|---|---|
| G1 覆盖 | 生成 = 更新当前简历的 `content_html`，一条简历永远只有一份最新生成物 | 丢失「历史生成物」；想留档要另加版本表 |
| G2 新建 | 保持现状，每次生成一行 | 列表被同名记录污染，需靠 `created_at` 区分；要么加「归档」要么定期清理 |
| G3 覆盖 + 保留最近 K 份 | 生成覆盖主记录，历史进 `resume_generations` 子表 | 多一张表，但两头兼顾 |

`tests/test_resumes_generate.py` 现有断言是「生成后 `resumes` 表 +1 行」，
选 G1 会直接改写这条断言（不是 bug，是语义变更，须在 PR 里显式说明）。

### 4.4 边界与错误语义（三个方案共用）

| 场景 | 建议 | 理由 |
|---|---|---|
| 访问别人的简历 id | **404**，不是 403 | 403 会泄露「该 id 存在」 |
| 删除唯一一份 | 【待拍板】拒绝 400 / 自动补一份空白 | 拒绝更可预期；自动补会掩盖误操作 |
| 每用户份数上限 | 建议 20，超出 400 | 限流只在 `/auth/*`（`app/core/rate_limit.py`），列表接口无限流，防滥用 |
| `title` 重复 | 允许重名（不加唯一约束） | 「投后端」建两次是合理行为；消歧靠 `updated_at` |
| 空 `title` | 400，沿用 `Field(min_length=1, max_length=200)` | 与 `ResumeGenerateRequest` 一致 |
| 并发编辑同一份 | 现状无乐观锁，后写覆盖先写 | 多份会放大这个既有缺陷，是否加版本号见 Q7 |

---

## 5. 决策三：前端交互

### 5.1 切换入口：三选

| 方案 | 形态 | 利 | 弊 |
|---|---|---|---|
| U1 顶栏下拉 | 在 `AppTopBar.vue` 的 `.user-area` 左侧插入下拉 | 改动最小、不占版面、随时可见 | >5 份后难扫读；重命名/删除放不下，还得再进列表页 |
| U2 独立列表页 | 新增 `/resumes` 路由（卡片/表格 + 新建/重命名/删除），点进 `/resumes/:id` | 管理能力强，符合「档案库」的心智 | 多一次跳转；编辑器首屏从「直接可编辑」变成「先选一份」 |
| U3 左侧常驻栏 | 在 340px 左栏顶部加列表 | 一屏内可切换 | **会挤压 A4 预览**：现在是 `340px + minmax(420px,1fr) + 220px`，总宽上限 1440；A4 正文可用宽度已接近下限，再切一栏就得改网格与打印边距 |

可组合（U1 快切 + U2 管理），但那是两套 UI，成本按两倍算。
另：`ResumeEditorView.vue` 的响应式断点在 1100px / 760px，U3 会让两档断点都要重排。

### 5.2 新建：空白还是复制？

| 选项 | 行为 | 利 | 弊 |
|---|---|---|---|
| C1 空白 | 建一份空骨架（同 `createEmptyDraft()`） | 干净、无脏数据 | 「投算法」版和「投后端」版 80% 内容相同，用户要重录 |
| C2 复制当前 | 服务端 `copy_from` 复制整份内容 | 命中「改一改就投」的真实场景 | 复制来的旧内容用户容易忘记删，投错版本 |
| C3 复制可选 | 新建弹窗里给「空白 / 复制现有」两个按钮 | 两头兼顾 | 多一个弹窗，多一条交互路径 |

**【硬约束】** 选 C2/C3 时必须走**服务端复制**（`POST /resumes?copy_from=<id>`），
不能让前端「GET 一份 → 改 id → PUT 回去」：前者是一次事务，后者会出现
「复制到一半失败留下一份半成品」和「复制期间源简历被改」两个竞态。

【待拍板】默认标题：`我的简历 2` 还是 `未命名简历`？后者需要额外做「首次保存时提示命名」，
否则列表里会出现一堆「未命名简历」。

### 5.3 Store 的安全切换（这是最容易出事的一段）

现状 store 有 4 个会互相干扰的状态：`draftOwnerId`、`autosaveTimer`（闭包私有）、
`stopDraftWatcher`（闭包私有）、`pendingDraft`。切换顺序必须是：

```
1. 若 dirty → 【待拍板】拦截（提示先保存/丢弃）还是自动保存后再切
2. stopDraftAutosave()            // 解除 watcher
3. cancelScheduledWrite()         // 取消未落盘的 400ms 定时器（当前是闭包内私有函数，
                                  //   切换流程要用到，需在 return 里暴露或封装成 switchTo()）
4. persistNow() 或 clearDraft()   // 明确「这一份的未保存改动」怎么处置
5. pendingDraft.value = null       // 否则 A 的「检测到草稿」横幅会出现在 B 里
6. generatedHtml/generatedResumeId/generatedTitle = null   // 否则 B 显示 A 的生成结果
7. currentResumeId = 新 id
8. await loadFromServer()          // 换成 GET /resumes/{id}
9. detectLocalDraft(新 key)
10. startDraftAutosave(新 key)
```

漏掉 2/3/5 中任意一步，都会复现 `reset()` 注释里那个「把空白骨架写成草稿」的 bug，
只是这次是「把 B 的骨架写进 A 的槽位」。

**另外两个必改点：**

- **本地草稿 key 必须加简历维度**：`resume_draft:${userId}` → `resume_draft:${userId}:${resumeId}`
  （`stores/resume.ts:72,207`）。旧 key 里的草稿在改造后成为孤儿，建议
  「读到旧 key 就提示恢复一次，之后迁移到新 key 并删除旧 key」，而不是静默丢弃。
- **并发切换要序号 guard**：用户快速点了两次下拉（A→B→C），B 的响应可能晚于 C 到达，
  把 draft 覆盖成 B 的内容。需要一个 `requestSeq`，只接受最新一次请求的结果。
  这个 bug 手动测试很难复现，但线上一定会遇到。

### 5.4 路由与守卫

现状只有 `/`（`router/index.ts:18`）。改造后建议：

- `/` → 重定向到「默认简历」或 `/resumes`（【待拍板】二选一）；
- `/resumes/:id` → 编辑器，`meta: { requiresAuth: true }`，沿用现有守卫逻辑；
- `/resumes` → 列表页（若采用 U2）；
- `/:pathMatch(.*)*` 现有兜底会 `redirect` 到 `resume-editor`，路由名改后要同步，
  否则非法 id 会被静默吞成一个 404 跳首页，用户以为「简历丢了」。

`auth.ensureUser()` 在守卫里每条跳转都会跑一次真实 `/auth/me`，
多一层路由意味着每次切换都多一次请求——可接受，但要知道它会和「切换简历」的
两个请求叠加（`/auth/me` + `/resumes/{id}`）。

### 5.5 生成按钮与「当前简历」的绑定

- `StylePanel.vue` 的 `store.generate('我的简历')` 必须改成用当前简历的 `title`；
- `generatedResumeId` 目前只是展示用的 `#id`，改造后必须明确：
  它是「当前简历最近一次生成物」，切走再切回时**要不要重新展示**？
  建议切回时清空（宁可让用户重按一次生成，也不要显示一份可能已过期的 HTML）。
- `ResumePreview.vue` 里 `generatedBody` 的 `DOMParser` 逻辑与版式无关，不用改。

---

## 6. 决策四：存量数据迁移

### 6.1 先摸清家底（本轮未执行，因为禁止改动）

迁移方案取决于真实数据量，三条只读查询即可定档（**本 ADR 未运行，仅列 SQL**）：

```sql
SELECT COUNT(*) AS users FROM users;
SELECT COUNT(*) AS profiles, COUNT(DISTINCT user_id) AS distinct_users FROM student_profiles;
SELECT user_id, COUNT(*) AS resume_rows
FROM resumes r JOIN student_profiles p ON p.id = r.student_profile_id
GROUP BY user_id ORDER BY resume_rows DESC LIMIT 10;
```

第三条是关键：若最大用户只有 1~2 行 `resumes`，§2.2 说的「同名快照污染」就还只是隐患；
若有用户已经 10 行，迁移时必须处理重复。

### 6.2 方案 A 的迁移（搬数据，7 步）

```
1. 建 resume_documents / resume_skills / resume_experiences / resume_educations（空表）
2. 每个有 student_profiles 行的用户，插一条默认简历：
   INSERT INTO resume_documents (id, user_id, title, is_default, created_at, updated_at)
   SELECT UUID(), user_id, '我的简历', 1, NOW(6), NOW(6) FROM student_profiles
   WHERE NOT EXISTS (SELECT 1 FROM resume_documents d WHERE d.user_id = student_profiles.user_id);
   -- 【硬约束】幂等：WHERE NOT EXISTS 不能省，否则重跑会翻倍
3. INSERT … SELECT 把 skills/experiences/educations 拷到新的三张表（按 resume_id 关联）
4. student_profiles 收敛为基本信息（删 grade 列；若各用户已有数据则保留列不删更稳）
5. resumes.content_html 的归属：见下方【待拍板】
6. 旧三张子表标记只读（不再被任何代码写入），一个发布周期后 DROP
7. 观察期结束后删旧表、删 student_profiles.grade
```

**【待拍板】旧 `resumes` 的多行怎么办？** 三种处理，代价差别很大：

| 处理 | 结果 | 风险 |
|---|---|---|
| 只保留最新一行 → 挂到默认简历 | 列表干净 | **删数据**，不可逆 |
| 每行都生成一份独立简历文档 | 一份档案瞬间变 10 份同名简历 | 用户困惑，且要复制 10 份子表 |
| 全部保留为「历史生成物」挂在默认简历下 | 无损 | 需要 `resumes` 与 `resume_documents` 不合并（见 §3.1 的待拍板项） |

### 6.3 方案 B 的迁移（加列 + 回填）

```
1. ALTER TABLE resumes ADD COLUMN content_snapshot JSON NULL, ...（4 列 + 1 索引）
2. 回填历史行：用「当前」档案内容构造 snapshot（不是生成时的内容）
3. 标记 snapshot_version = 1
```

**【已知损失，必须写进 changelog】** 历史 `resumes` 行的快照只能取**当前**档案内容，
而档案在那次生成之后可能已被修改。也就是说回填出来的快照不等于当时的真实输入——
**它只是「现在的内容」，不是「历史的真相」**。若要严格还原，只能靠
`content_html` 反解析（不可靠，见 §7 的「旧 HTML 结构不回溯」遗留问题）。
诚实做法：把历史行的 `snapshot_version` 设为 `0`（= 未知），UI 上标注「早期记录，无快照」。

### 6.4 方案 C 的迁移（3 条 DDL）

```
1. SHOW INDEX FROM student_profiles;   -- 先确认唯一索引真实名字，别照抄 "user_id"
2. ALTER TABLE student_profiles DROP INDEX <实际索引名>;
3. ALTER TABLE student_profiles
     ADD COLUMN title VARCHAR(200) NOT NULL DEFAULT '我的简历',
     ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0,
     ADD INDEX ix_student_profiles_user_default (user_id, is_default);
4. UPDATE student_profiles SET is_default = 1;   -- 存量每人恰好一行，全标默认
```

**【硬约束】`is_default` 的「每人至多一条」无法用约束表达。** MySQL 没有部分唯一索引。
可选做法：应用层在「新建 / 设为默认」时用事务 + `SELECT ... FOR UPDATE` 锁该用户的行；
或接受软约束，读时按 `is_default DESC, updated_at DESC` 取第一条，脏数据也不致命。
【待拍板】选哪种严格度。

### 6.5 三个方案共用的迁移纪律（踩过的坑，别重复）

1. **迁移必须幂等**：这些迁移会碰数据，重跑不能翻倍。`WHERE NOT EXISTS` /
   `INSERT IGNORE` 视方案选用。
2. **collation 陷阱**（D1 的教训）：`utf8mb4_0900_ai_ci` 下 `WHERE title = '我的简历'`
   会命中大小写/重音变体；若需要精确匹配（比如按标题找重复），必须
   `WHERE BINARY title = BINARY '我的简历'`。D1 就踩过「朴素写法恒假、0 行更新」。
3. **迁移前备份**：`mysqldump` 整个库（不只这几张表），并记录 dump 文件名。
4. **先在生产影子库演练**：`CREATE DATABASE ... LIKE` + 导入 dump + 跑迁移 + 跑
   一套只读断言（行数、外键完整性、每条默认简历恰好一条）。
5. **downgrade 必须写明会丢什么**：A 有损、B 无损、C 在有 >1 行时直接失败（需删行）。
   一个「能 upgrade 不能 downgrade」的迁移是可接受的设计，但**不能是意外**。
6. **启动防呆检查要跟着更新**：`app/main.py` 的 `check_migration_state()`
   会在版本落后时告警、production 直接拒启。新迁移落地后，**所有开发/测试/生产库
   都必须 `uv run alembic upgrade head`**，否则新的 fail-fast 会立刻拦人。
   这是本轮刚加固的机制，改造时不要绕过它。

---

## 7. 无论选哪个方案都要付的固定成本

选型只决定「大头」，下面这些是三个方案都躲不掉的：

**后端**

- 新增「按用户 + 简历」的归属校验查询（§4.1 的 **【硬约束】**）。
- `ResumeService` 的完整性判定基准从 `user_id` 换成「这一份简历」
  （`resume_service.py`），`ProfileIncompleteError` 的文案也要区分
  「还没建简历」和「简历内容不全」。
- `ProfileService` 要么多一层 `list`，要么接入新仓储。
- import-linter 的 5 条 contract 必须继续全绿（仓储不能反向依赖 services、API 不能碰
  integrations）。新增仓储放 `app/repositories/`，别为了省事塞进 service。

**前端**

- `api/types.ts` 新增列表项类型（`ResumeListItem`）；`ProfileRead` 视方案加 `title`/`is_default`。
- `api/profiles.ts` / `api/resumes.ts` 新增 list/get/create/delete 封装，
  错误处理沿用 `client.ts` 的 `unwrap` + T6 统一模型。
- `stores/resume.ts` 的 `loadFromServer` / `save` / `generate` / `reset` 四个函数全部要动，
  外加 §5.3 的切换流程与 key 改造。
- 路由 + 顶栏/侧栏 + 新建弹窗。

**测试（会直接变红的，逐条列出，避免被当成「顺手改改」）**

| 文件 | 影响 |
|---|---|
| `tests/test_profiles.py` | 20+ 个用例断言 `PUT /profiles/me`、`GET /profiles/me`；方案 A 会全部作废，方案 C 需改成 `/profiles/{id}` |
| `tests/test_resumes_generate.py` | 断言「生成后 `resumes` +1 行」「`title` 原样返回」；选 G1（覆盖）必改 |
| `tests/test_errors.py` | 4 处直接打 `/profiles/me`、`/resumes/generate`，验证 422/405/404 的 T6 模型 |
| `tests/test_profile_contract.py` | 把 `toUpsertPayload()` 的真实形状喂 `ProfileUpsertRequest`；若请求体不变则**不用改**，这正是方案 C 的隐性收益 |
| `tests/test_migrations.py` | 校验表结构；新迁移要补一条「默认简历恰好一条」的断言 |

**文档**

`docs/PROJECT_STATUS.md`、`docs/DEVELOPMENT.md`（迁移纪律）、
`docs/PHASE2_BACKLOG.md`（本条勾选）、以及本 ADR 的状态字段（草案 → 已接受）。

---

## 8. 分阶段落地的可能切法（不含工期，只排依赖）

若人类倾向「先能用，再变好」，下面这条路径在三个方案上都能用：

- **阶段 0（与选型无关，可先做）**：给 `/resumes` 补 `list` / `get` /
  `PATCH title` / `DELETE`。这一步不碰 `student_profiles` 的唯一约束，
  相当于先把 R2 做出来。做完就能真实回答「用户到底要 R1 还是 R2」。
- **阶段 1（选型后）**：数据模型改造 + 迁移（A/B/C 之一）。
- **阶段 2**：前端切换交互 + 本地草稿 key 改造 + 并发 guard。
- **阶段 3**：清理遗留（旧表/旧列/`grade`/孤儿草稿/兼容别名的 sunset）。

> 阶段 0 的价值：它用最小代价换来一次真实需求验证。若做完发现用户只是
> 想要「多导出几版留档」，那方案 A 的 4 张新表就是白付的。

---

## 9. 待人类拍板的完整问题清单

| # | 问题 | 影响范围 |
|---|---|---|
| **Q1** | 需求是 **R1（多份可独立编辑）** 还是 **R2（一份档案 + 多快照/归档）**？还是两者都要？ | 决定整份 ADR 的走向；若只要 R2，选 B 并大幅缩减范围 |
| **Q2** | 数据模型选 **A / B / C**（D 已排除）？ | 表结构、迁移、全部前后端 |
| **Q3** | 若选 A：`resumes` 与 `resume_documents` **合并**还是**分离**？ | 表数量、前端持有的 id 数 |
| **Q4** | 若选 A：保存时「基本信息 + 内容」发**两个请求**，还是后端用一个 `PUT` 写两张表？ | 前端错误处理与 `dirty` 语义 |
| **Q5** | API 前缀用 **`/resumes`（N1）/ `/profiles`（N2）/ 双前缀（N3）**？ | 路由与前端调用 |
| **Q6** | 生成语义：**G1 覆盖** / **G2 每次新建** / **G3 覆盖 + 保留 K 份**？ | `resumes` 行数语义、`test_resumes_generate` |
| **Q7** | 是否加**乐观锁**（`version` / `If-Unmodified-Since`）防并发覆盖？现状没有，多份会放大 | 表加列、前端错误分支 |
| **Q8** | 删除**最后一份**简历：**拒绝**还是**自动补一份空白**？ | 接口错误码、前端空态 |
| **Q9** | 切换入口：**U1 顶栏下拉 / U2 列表页 / U3 左栏**（U1+U2 组合也可）？ | 前端 UI 与断点 |
| **Q10** | 新建简历：**空白 / 复制 / 可选**？默认标题叫什么？ | 接口参数、列表可读性 |
| **Q11** | 切换时若有未保存改动：**拦截提示 / 自动保存再切 / 直接丢弃**？ | 数据丢失风险（用户可感知） |
| **Q12** | 若选 C：`is_default` 的「每人至多一条」用**事务加锁强保证**还是**软约束 + 读取兜底**？ | 并发正确性 vs 实现复杂度 |
| **Q13** | 存量多行 `resumes`：**只留最新（删数据）/ 每行独立成一份 / 全部留作历史生成物**？ | 不可逆操作，需单独授权 |
| **Q14** | 是否先做「阶段 0」（只加生成的列表/重命名/删除）来验证需求？ | 要不要先付一笔小成本换取需求确认 |
| **Q15** | 旧 `/profiles/me` 兼容别名保留多久（一个发布周期？直接删？） | 前端改造可与后端解耦的程度 |

---

## 10. 本 ADR 未做的事

- **没有修改任何业务代码**（`.py` / `.vue` / `.ts` 一行未动）。
- **没有修改数据库结构**，没有新增迁移脚本。
- 没有执行 §6.1 的任何查询（那会连生产/开发库），所以**本 ADR 不知道真实数据量**——
  Q13 的答案依赖它，需要人类授权后单独跑。
- 没有做任何性能评估（列表查询、JSON 快照大小、`LONGTEXT` 的读写成本）。
- 没有画 UI 线框图；§5.1 的三个交互只给了文字形态与断点影响。
- 没有 commit。

---

## 11. 已知的遗留问题（与本次改造的交叉影响）

这些是 Phase 2 之前就存在的问题，改造时会被放大，先记录不解决：

1. **`content_html` 里的旧结构不回溯**：历史简历的 HTML 是旧版模板产出的，
   「多份简历」列表如果要展示缩略图，会看到两种版式并存。
2. **纸面编辑仍未写回后端**（D3 只合并了保存入口，内容仍只存 Pinia + localStorage）。
   多份之后，「纸面上改的字属于哪一份」会变成一个新问题。
3. **`tests/test_auth.py` 的 `drop_all` 老坑**：跑全量测试前须先对测试库
   `alembic downgrade base`，否则 3730 冲突。新迁移会让这个坑更烦。
4. **限流是进程内计数**（`app/core/rate_limit.py`），多 worker 下不准；
   新增的列表/创建接口若不加限流，批量建简历没有防护。
