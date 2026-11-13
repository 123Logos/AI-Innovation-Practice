# ADR-028：标准化题库测评与 AI 能力画像（批次 B9）

- 状态：已实现骨架（2026-11-13）——题库为种子数据、画像为结构化标签、AI 摘要可降级
- 关联：`docs/PRD.md` FR-04 与 §113（「已验证 / 待验证 / 待提升」三态）、§150（证据链图谱）、
  `ADR-009`（BYOK 与 AI 调用边界）、`ADR-013`（证据打分，**本批不重复造分**）、
  `ADR-022`（B11a 的 AI 可见降级先例）、`ADR-027`（`source='assessment'` 的生产者）、
  `docs/ROADMAP.md` §2 B9

## 1. 背景：0% 起步，先要回答两个结构问题

冻结已全面解除（`ADR-014`），但「标准化题库测评 + AI 用户画像」一行代码都没有。开工前必须定死两件事：

1. **题库怎么结构化**：题目、选项、标准答案、评分口径要不要进库；
2. **「画像」怎么存**：一段 JSON，还是拆成标签行。

### 1.1 「AI 画像」这个词最容易做成假货

如果画像 = 一段 AI 写的自由文本，它同时坏掉三件事：

- **不可查询**：B10a 的学情看板要「按标签聚合这个班」，一段散文聚合不了；
- **不稳定**：同一个学生今天测出来是「踏实」，明天是「有潜力」，用户会认为系统在瞎说；
- **不可引用**：B7 的 `source='assessment'`、B10b 的批量导出都想知道**哪一条**技能被验证过，
  而散文里没有「一条」这个概念。

所以本 ADR 的核心是一条分工：

> **标签是事实，AI 只负责把标签写成一段人话。**

事实进表（可查、可聚合、幂等覆盖），措辞进 `ai_summary`（可重生成、可降级、带来源标记）。

### 1.2 与 `ADR-013` 的关系：本批不打分

`ADR-013` 已有一套人岗匹配打分（`match_scoring.py`，带权重与可解释 note）。
测评**不产出**任何匹配分，也不往公式里塞「测评得分」——那会让同一份档案在「匹配页」与
「画像页」得到两套人格。本批只产出**标签与三态**，匹配公式一行不改。

## 2. 决策

### 2.1 题库：一张表 + 钉住 `bank_version`

`assessment_questions`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `VARCHAR(36)` PK | |
| `skill_key` | `VARCHAR(100)` | 归一后的技能键（`app/core/skill_names.py::skill_name_key`），与 B7 的技能同名口径一致 |
| `skill_name` | `VARCHAR(100)` | 展示用的原始写法（`Python` / `MySQL`） |
| `prompt` | `VARCHAR(500)` | 题干 |
| `options` | `JSON` | `[{"key":"A","label":"…"}, …]`，2–6 项 |
| `answer_key` | `VARCHAR(4)` | 标准答案的 option key，**只存在于服务端** |
| `bank_version` | `VARCHAR(16)` | 题库版本（种子为 `v1`） |
| `is_active` | `BOOLEAN` | 停用不删；历史会话仍要能解释 |
| `created_at` | `DATETIME` | |

四条口径：

1. **选项存 JSON 而不是单开一张 `assessment_options` 表**：选项是「这道题的数据」，不是独立实体，
   没有任何跨题查询需要它；单开表只会多一次 join 与一套 CRUD。
2. **`answer_key` 不进任何读响应**（`GET` 系列只回 `options`），否则测评当场变开卷。
3. **`bank_version` 钉在会话上**：题库会改。会话记下当时的版本，否则「三个月前那份测评的 80 分」
   在题库换过之后无法解释。`bank_version` 是**字符串**而不是自增号——它要能被人读懂（`v1`）。
4. **种子走迁移的数据插入**（12 题，4 项技能 × 3 题），`downgrade` 按 `bank_version` 删除。
   扩库是**数据**操作，不需要再写迁移（§5）。

### 2.2 会话状态机：比 B11a 更结构化

`assessment_sessions`：

| 列 | 说明 |
|---|---|
| `id` / `user_id` / `student_profile_id` | 归属：会话属于**用户 + 某一份档案**（画像挂档案，见 §2.4） |
| `status` | `in_progress` / `submitted` / `abandoned` |
| `bank_version` | 开业时的题库版本 |
| `question_count` | 本题库实际出题数（写死，避免与题库后续变化混淆） |
| `correct_count` / `score` | 提交时算出的确定值；`in_progress` 时为 `NULL` |
| `started_at` / `submitted_at` / `abandoned_at` | |
| `ai_summary` | `TEXT NULL`，AI 或降级模板写的一段话 |
| `ai_summary_source` | `ai` / `fallback` / `NULL`（从未生成） |
| `ai_summary_at` | |

状态迁移只有三条，**全部单向**：

```
in_progress --submit--> submitted     （判分 + 写标签，一次性）
in_progress --abandon--> abandoned    （用户放弃；不写标签）
submitted   / abandoned               （终态，任何写入 409）
```

- **只有 `in_progress` 能改答案**；`submitted` 之后任何写答案/重复提交 → `409`；
- 提交是**幂等**的吗？不是——第二次提交返回 409 而不是「再算一遍」。理由：提交会写画像标签，
  「重复提交悄悄成功」会让用户以为可以靠刷新改分；
- **允许重考**：每次重考是一条新会话，历史保留；画像取**最近一次 `submitted`**（§2.4）。

### 2.3 答案与判分：确定性，不调 AI

`assessment_answers`：`session_id` + `question_id` + `chosen_key` + `is_correct` + `answered_at`，
`UNIQUE(session_id, question_id)`（提交前可覆盖，等价 UPSERT）。

判分在提交那一刻算，**纯函数**（分数 `app/core/assessment_rules.py::score_percent`、
三态 `app/core/assessment_rules.py::tag_status`，两者由 `score_skill` 汇总）：

```
score      = round(正确数 / 题数 * 100)           # 0–100，整数
tag_status = verified    命中率 >= 0.8 且题数 >= 2
             improving   命中率 <  0.5
             pending     其余（含题数不足 2）
```

阈值写在一个模块里，不散在 SQL 与服务里：**「几分算验证过」是产品口径**，散开就会漂
（`ADR-013` 的权重常量同一条理由）。

### 2.4 画像：`profile_tags` 表，不汇总成一段 JSON

`profile_tags`：

| 列 | 说明 |
|---|---|
| `id` | PK |
| `student_profile_id` | **挂档案**（不是用户）：技能属于档案（`skills.student_profile_id`），画像聚合技能，挂 `user_id` 会与「一人多档案」直接矛盾 |
| `tag` | 标签文本 = 技能名（`skill_name` 原写法），`UNIQUE(student_profile_id, tag)` |
| `status` | `verified` / `pending` / `improving` |
| `score` | 0–100 整数 |
| `source` | 今天只有 `assessment`（枚举留位：将来的 `project` / `teacher`） |
| `evidence_count` | 支撑这个标签的题数/证据数 |
| `updated_at` | |

为什么不是「一个 JSON 字段挂在档案上」：

- **要聚合**：B10a 学情看板按标签统计全班，JSON 做不到（MySQL 里 `JSON_CONTAINS` 能查，但不能索引成聚合口径）；
- **要 UPSERT**：重考一次应当**覆盖**同一标签，而不是让画像越长越胖；
- **要可引用**：`ADR-027` 的 `source='assessment'` 与将来的证据链都要指到「一条」标签。

### 2.5 AI 摘要：可选、BYOK、**必须能降级**

`POST /assessments/sessions/{id}/summary`（登录 + `X-DeepSeek-Key`）：

- 输入**只有**标签、分数、技能名与题库版本——**不读简历正文**，也不读邮箱/手机号（画像不该把
  纸面上的联系方式送进第三方）；
- 输出写给 `ai_summary`，`ai_summary_source='ai'`；
- **任何 AI 失败**（无 Key / Key 无效 / 超时 / 上游异常）→ **`200` + `source='fallback'`** +
  一段由标签拼出的确定性文案，并在 `ai_summary_note` 里说清「这是自动生成的概述」。

这条与 B11a（`ADR-022`）看似相反，其实是同一条原则的两面：

| | B11a 模拟面试 | B9 画像 |
|---|---|---|
| AI 挂了你损失什么 | **内容本身**（追问、复盘没了） | 只是措辞 |
| 所以 | 明说「AI 不可用」，不假装有内容 | 降级成模板文案，**事实（标签）照旧可看** |

**降级不许伪装成 AI**：`source` 就是给前端与后续排障用的，前端必须据此显示「自动概述」而不是
「AI 分析」。这与 `ADR-022` 的「AI 可见降级」同一条底线。

### 2.6 与 B7 的接线：提交时**只升级已有技能的来源**

提交成功时，对 `status='verified'` 的标签，若该档案里**已有**同名技能（按 `skill_name_key` 匹配），
把它的 `skills.source` 写成 `assessment`；**不新建技能、不改名、不动 `last_used_on`**。

三条理由：

1. `ADR-027` §2.2 规定 `assessment` 只能服务端写，这里就是那个生产者；
2. 用户点「提交」是他对「用这次测评的结果标记我的技能」的**明确同意**，不是后台偷偷改数据；
3. `source` **不上纸**（`ADR-027` §2.4），所以这条写入不会让任何已生成的简历发生变化。

**为什么不自动新建技能**：测评能证明「你答对了这几题」，不能证明「你要把这条技能写进简历」。
简历内容永远是用户自己确认的。

### 2.7 端点

| 方法与路径 | 作用 | 权限 |
|---|---|---|
| `GET /assessments` | 题库目录（技能 + 题数 + `bank_version`，**不含题干与答案**） | 登录 |
| `POST /assessments/sessions` | 开一场测评（可指定 `profile_id`），返回题目（**不含答案**） | 登录 |
| `GET /assessments/sessions/{id}` | 会话详情：题目 + 我的答案 + 状态 | 本人 |
| `PUT /assessments/sessions/{id}/answers` | 批量覆盖答案（可多次，`in_progress` 才允许） | 本人 |
| `POST /assessments/sessions/{id}/submit` | 判分 + 写标签 + 升级来源 | 本人 |
| `POST /assessments/sessions/{id}/summary` | AI 概述（BYOK，可降级） | 本人 |
| `GET /assessments/profile` | 我的画像：标签列表 + 最近一次会话的摘要 | 登录（自己的档案） |

别人的会话一律 `404`（不是 403）：与岗位、简历同一口径，403 会暴露「这个 id 存在」。

## 3. 后果

**好处**

- 画像可查询、可聚合、可被别的批次按行引用；重考不会让标签膨胀；
- AI 挂着的时候用户仍能看到全部事实，只是措辞变成模板；
- 判分完全确定性，同样的答案永远同样的分——测评结果可以被追问「为什么」；
- `ADR-027` 的 `assessment` 来源有了真实生产者。

**代价**

- 题库只有 12 题种子，覆盖面窄到只能算骨架（§5）；
- 没有防作弊（同一会话不能重复提交，但可以开新会话抄答案）——这一批不解决；
- 没有时间限制、没有难度分层、没有题目随机化（同一 `bank_version` 每次同样的题）；
- 画像标签不参与匹配打分，所以「验证过的技能」今天不会让匹配分变高（这是有意的，见 §1.2）。

## 4. 验收判据

| 判据 | 用例 |
|---|---|
| 题库读不到标准答案 | `tests/test_assessments.py::test_catalog_never_leaks_answer_keys` |
| 开考返回题目，且不含答案 | `::test_start_session_returns_questions_without_answers` |
| 答案可改，重复写覆盖不追加 | `::test_answers_are_overwritten_not_appended` |
| 提交判分与标签三态符合阈值 | `::test_submit_grades_and_writes_tags` |
| 重复提交 → 409，且分数不变 | `::test_second_submit_is_rejected` |
| 提交后改答案 → 409 | `::test_answers_are_frozen_after_submit` |
| 别人的会话 → 404 | `::test_other_users_session_is_not_found` |
| 放弃的会话不写标签 | `::test_abandoned_session_writes_no_tags` |
| 重考覆盖标签而不是追加 | `::test_retake_overwrites_tags` |
| 已验证标签把已有技能来源升为 `assessment` | `::test_verified_tags_upgrade_skill_source` |
| AI 不可用时摘要降级且事实仍在 | `::test_summary_falls_back_without_a_key` |
| 降级摘要不得标成 `ai` | `::test_fallback_summary_is_labelled_as_fallback` |

## 5. 遗留与下一步

1. **题库规模与质量**：12 题种子只够跑通链路。扩库是数据工作（题库表已支持 `bank_version`），
   但**题目质量需要人工**，不许让 AI 生成题目直接进库；
2. **防作弊与随机化**：同 `bank_version` 每次同题、可开无限新会话。要防，得先有「出题池 + 抽样 +
   时间窗」的产品口径；
3. **难度与技能覆盖**：现在每题等权。加权需要先有「这道题能代表多少能力」的判断依据；
4. **画像标签参与匹配**：需要与 `ADR-013` 的公式一起谈，且必须先解决「已验证」与「会做」之间的
   语义差距；
5. **教师确认 / 项目证据进入画像**：`profile_tags.source` 已留位，但写入路径要等
   `ADR-027` §5 的证据链；
6. **前端页面**：本轮只交付 API 与前端类型定义，答题页与画像页排在下一批（`docs/ROADMAP.md` B9 行）。
