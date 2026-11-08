# ADR-013：人岗匹配 v1 与技能证据评分重构

- 状态：**已接受**（2026-11-07）。🚩 决策 1–5 由人类负责人的概括指令接受：「其余未提到的部分采用你推荐的方案」；
  🚩 **决策 6 同日单独批准**，原话：「**我同意BYOK白名单加入新的URL**」（他同时说「其他没有别的问题了」）。
  这条之所以要单独留痕：它动的是 `frontend/src/api/client.ts:207-222` 那条他自己写的约定，
  不能拿概括指令当它的签字。§6 的白名单实现因此可以开工，但必须连它要求的断言一起做。

- 日期：2026-11-07
- 决策人：本项目人类负责人；Agent 提供方案与实测取证
- 关联：ADR-009（BYOK 形态）、ADR-010（润色与生成解耦）、ADR-011（岗位库与技能提取，本文承接其 §2.E）、ADR-012（前端 lint，无关但同期）

---

## 0. 授权记录（逐字）

AGENTS.md §3.2 与 `docs/MVP_SCOPE.md:42` 把「人岗匹配、自动推荐」列为冻结项，且 `MVP_SCOPE.md:43` 在 Phase 4 部分解除时**明确重申人岗匹配保持冻结**。解冻只能由人类负责人书面批准，措辞必须出自他口。他的原话，两条，均为 2026-11-07：

> 「3.先做人岗匹配，10名用户硬性需求优先级下降一档，开发进度优先」

> 「6.人岗匹配：
> 　1.你去搜索猎头和就业网站（Boss直聘、58同城等）用测试账号新建用于内部测试的多个岗位
> 　2.学生侧技能 self_rating 1–5已在简历部分废除，通过结合项目经验与关键词匹配度技术，配合接入AI分析打分，这里需要重构评分系统
> 　3.目标偏好有用户使用并在简历或者哪里填写偏好岗位后就能计算
> 　4.用户角色系统在后面的需求（教师、学校管理简历等）需要进行实现
> 　7.其余未提到的部分采用你推荐的方案
> 　请继续进行开发」

**这两条构成解冻批准本身**，本文不另造一句「兹批准」代替它。同时必须说清我做了哪些他没有说的推断——每一处都标 `【Agent 推断】`，他改哪条就只改那一节，不影响其余：

| 推断 | 依据 | 风险 |
|---|---|---|
| 解冻只覆盖「用户主动查看某一岗位与自己匹配度」，**不**覆盖广场排序与自动推荐 | 他把「人岗匹配」和「自动推荐」当作两件事下达（`MVP_SCOPE.md:42` 也是两条），且未提排序 | 若他要排序，本文 §2 端点形态不变，但要多算 N 份，成本与限流全部重来 |
| 「配合接入 AI 分析打分」= 授权第 3 个 BYOK 端点 | 评分要语义层就必然要模型 | **这条动的是他自己划的安全线**，见 §6，必须单独确认 |
| 分数不落库、不缓存 | 他未提持久化；陈旧分数比没有分数更有害 | 若要「匹配历史」，需要新表 → 新 ADR |
| 「重构评分系统」= 把 `self_rating` 从写入路径与库中一并移除，而不只是不用 | 实测见 §1.2：UI 早已不采集，前端恒发 3，它是零信息常量 | 是破坏性 API 变更（`SkillInput` 少一个必填字段），需迁移与文档同步 |

---

## 1. 背景（全部为实测，不是推测）

### 1.1 岗位侧现在是空的
dev 库实测：`published` 岗位 **0** 条，`draft` 3 条，回收站 1 条，`job_skills` 共 4 行、只有 1 个不同技能名；学生侧 `skills` 25 行、17 个不同名、7 份档案有技能。也就是说匹配算法写出来也没有东西可匹配，下一轮真实用户打开岗位广场仍是空白页。老板指令 6.1 要求取材真实招聘网站建内部测试岗位，本文 §9 把它列为前置任务而不是匹配的一部分。

取材时有一条必须先划的红线：真实 JD 里高频出现「2027 届本科及以上在读」「无挂科」「可加班」「限男性」「35 岁以下」这类表述。其中**毕业年份是年龄的代理变量**，学历/户籍/性别是 PRD 明令禁止的排序依据（`docs/PRD.md:200`）。内部测试岗位只写技能、项目、到岗时长类要求，**一律不写**上面这些，哪怕真实 JD 都这么写。

### 1.2 `self_rating` 是零信息常量
- 定义：`SkillInput.self_rating: int = Field(ge=1, le=5)`（必填）；列在 `skills` 表 `SmallInteger NOT NULL`，DDL `alembic/versions/20261023_0001_initial_phase1.py:60-78`（65 行建列、68 行 CHECK 1..5）。**代码与列都已被 §10 第 4 步删除**（`alembic/versions/20261107_0013_drop_skill_self_rating.py`），故本条不给 Python 侧行号。
- UI 不再采集：`frontend/src/stores/resume.ts:12` 写明「技能熟练度（`self_rating`）与年级已从 UI 移除」，`:1092` 写明「后端仍必填，UI 不再采集，统一写 3」，常量在 `:264` `DEFAULT_SKILL_RATING = 3`。
- 渲染也不用：`app/integrations/resume_generator.py:104` 「只渲染技能名：熟练度已从产品中移除，`self_rating` 仅作为入参保留校验」。
- 库里的分布（实测）：`{2: 1, 3: 23, 4: 1}` —— 25 行里 23 行是那个常量 3，另外 2 行是熟练度下线前留下的。
- 结论：它既不能当「证据强度」，也不能当任何东西。老板指令 6.2 说它「已在简历部分废除」，与代码一致；本文 §4 决定它的去留。

### 1.3 匹配键现在有三套，且学生侧不清洗
| 位置 | 规则 | 实测 |
|---|---|---|
| MySQL 唯一索引 | `utf8mb4_0900_ai_ci`，**NO PAD** | `SELECT ('Python ' = 'Python') → 0`，即带空格的两个名字可以共存 |
| 后端 `skill_name_key`（诊断时在 `app/schemas/job.py`，现居 `app/core/skill_names.py`，见下方两处偏差说明） | NFKC + casefold，**不 strip** | `'Python '` vs `'Python'` → 不同 |
| 前端角标 `normalizeSkillName`（`frontend/src/stores/job.ts:213-215`） | trim + NFKC + toLowerCase | 同上 → 相同 |

除首尾空白外三套行为一致（`Web␣␣开发` 内部空格、`ß`/`strasse`、`Ｃ#` 都判不同，实测过）。岗位侧在 API 边界已 strip（`app/schemas/job.py:99-110`），**学生侧没有**：`SkillInput.name` 无校验器，档案技能去重用的是裸相等（`app/schemas/profile.py`，修复前的状态，无行号可指）。今天库里 0 条带首尾空白的技能名，但门开着：一旦学生存进 `"Python "`，服务端算「未覆盖」而前端角标显示「已具备」，同一屏上自相矛盾。

**2026-11-07 已消除（§10 第 3 步的 a/b 两半）**，并且实测出一个比文档描述更严重的后果：
裸相等让 `Python` + `python` 通过校验、走到 INSERT，再由 `uq_skills_profile_name`
（建在大小写不敏感的 `utf8mb4_0900_ai_ci` 上）抛 `IntegrityError 1062` —— **用户看到的是 500，
不是「技能名称重复」**。这条不是推演：新测试先跑在未修代码上，抓出来的就是这个数据库异常。
顺带堵掉 `"   "`（`min_length=1` 挡不住纯空白名，它会往库里存一条空白技能）。

落地方式与本节初稿不同，两处偏差如实记：

1. **没有新增 `match_key()`**。`skill_name_key` 就是那个唯一口径，再加一个名字只是多一层转发；
   两端空白仍归各自的**输入边界**负责（岗位侧 `JobSkillInput.normalize_name`、
   现在档案侧 `SkillInput.normalize_name`、提取侧 `_verify_item`），键本身仍是两步
   （NFKC + casefold），ADR-011 §2.D.2 的口径一字未改。
   > **2026-11-08 更正：这条结论被推翻了，但推翻它的是事实而不是口味。**
   > 当时两个键回答的是**同一个问题**（去重），多一个名字确实只是多一层转发。
   > 现在它们回答的确实是两个问题了，所以新增 `skill_forms_match()`（`app/core/skill_names.py`）：
   >
   > | 键 | 回答的问题 | 必须怎么判 |
   > |---|---|---|
   > | `skill_name_key`（不变） | 这两行是不是重复，要不要报 422 / 要不要合并 | `Python` 与 `Python3` **算两行**——用户想同时列 `C` 与 `C++` 时不许被拦 |
   > | `skill_forms_match`（新） | 岗位要求这一格，学生的技能算不算顶住 | `Python` 与 `Python3` **算同一个**——否则写法差异直接吃掉 0.45 那一项 |
   >
   > 触发这条更正的是老板 2026-11-08 的实测：他技能表里写 `Python3`、岗位要求 `Python`，
   > 那一格被判「还缺」并附一句「补一条真的用 Python 做过的项目经历」——而他自己的经历正文里
   > 就写着 `Python 3.13`。
   >
   > 判据四条，**刻意不含任何领域词表**（本项目面向的是所有专业的用户，
   > 一张计算机别名表会让财务/机械/设计/护理的用户系统性吃亏，而我们连发现的机会都没有——
   > 库里 5 条种子岗位实测全是技术岗，就是这个偏置的样本）：
   > ① 折叠后全等；② 词干不同 → 不同（**禁止前缀/子串推断**，于是 `C`/`C++`/`C#`、
   > `Java`/`JavaScript`、`SQL`/`MySQL`、`Go`/`Golang`、`R`/`React` 全部分得开）；
   > ③ 词干相同、恰好一侧带版本号 → 同一个（白名单是**对面真写过的那个不带号的写法**，
   > 不是我手写的词表，所以 `SPSS 26`↔`SPSS`、`AutoCAD 2022`↔`AutoCAD` 天然同规则）；
   > ④ 两侧都带版本号 → **主版本号相同**才算同一个（`Python3` 认 `Python 3.13`，
   > `Python2` 不认 `Python3`，`Vue 2` 不认 `Vue 3`）。
   >
   > **暂缓的一半（老板 2026-11-08 裁定 ③）**：缩写别名（`K8s`↔`Kubernetes`、`PS`↔`Photoshop`、
   > `Golang`↔`Go`）需要一张 L1 词表，非计算机领域的冲突反例与别名**先不建**，
   > 靠真实用户撞出来再收。L1 落地时 `meta` 必须带 `alias_version`，理由见 §11 的分数漂移那条。
   >
   > 同一个判据必须**同时**用在两处，只修一处是半修：覆盖判定（本节）与
   > 证据定位（§6 的 `locate_snippets`）。实测过反例：岗位要求 `Python3`、正文写 `Python 3.13`，
   > 整串子搜得 **0 处**——覆盖修好了仍会落到「已具备但零片段」，也就是 §6.5 那条第三种情形。
2. **键从 `app/schemas/job.py` 移到 `app/core/skill_names.py`**。原因不是我嫌乱，是契约是**传递**的：
   档案侧一旦 `import app.schemas.job`，就出现
   `integrations.resume_generator → schemas.profile → schemas.job → repositories.job_repository`
   这条可达路径，`lint-imports` 当场报 `Contracts: 4 kept, 1 broken`。
   这个键零依赖，`core` 才是它真实的位置。改后 `Contracts: 5 kept, 0 broken`。

**残留的一半（第 3 步 c）**：前端角标仍用自己的 `trim + NFKC + toLowerCase`。在 ASCII 与 CJK 上
它与服务端一致，但 JS 没有 casefold——`ß`/`strasse`、`İ` 这类字符两边会给出不同答案。
要彻底消除得让角标消费服务端结果，而那需要一个不花钱的确定性端点（不能复用匹配端点，
否则每次浏览岗位详情页都烧一次用户的 AI 额度）。这条与匹配端点同轮落地，见 §10 第 6 步。


### 1.4 没有「目标偏好」这个东西
全仓 grep `target_role|preferred|意向|偏好` 在 `app/` 与 `alembic/` 0 命中（只剩两条注释里的「风格偏好」字样）。`student_profiles` 现有列：`title / is_default / paper_size / theme / layout_config / full_name / email / grade / contact`。所以 PRD FR-05 的 0.10 权重项**当前无法计算**，老板指令 6.3 要求补一个用户填写的偏好岗位。

### 1.5 没有角色系统
`users` 表无 `role` 列，全仓无 `is_admin`（ADR-011 1.A 决定不加，老板指令 6.4 确认角色系统留给后续需求）。因此 `docs/PRD.md:119` 那句「权重由管理员配置并记录版本」**现在实现不了**，v1 只能把权重写成代码常量 + 版本号，沿用 `prompt_version='v1'` 的先例。

### 1.6 我自己在这条上连错两次（写下来，防止实现时照抄任何一个版本）

真实机制是**两道**，不是一道：

- 服务层显式切片：`SUMMARY_SKILL_LIMIT = 6`（`app/services/job_service.py:82-83`），
  构造列表项时 `skills[:SUMMARY_SKILL_LIMIT]`（`:534`）；
- 列表模型确实会拦：`JobSummary.skills` 的 `max_length=6`（`app/schemas/job.py:253`）
  实测 **7 条技能 → ValidationError**；而详情模型 `JobRead.skills` 没有上限，
  实测 **15 条 → 通过，`len == 15`**。

所以：同一个岗位在列表里只有前 6 条技能，在详情里才是全量。任何「按列表载荷算匹配」的实现
都会把分母算小——载荷形状决定的必然结果，不是理论风险。差别在于**忘了切片**会撞上有界校验、
当场报错，而不是静默把 15 条发出去，这一点 `frontend/src/views/JobBoardView.vue:7` 的注释说得对。

我错的两处：初稿写「JobRead 只回 6 条」（张冠李戴，那是 `JobSummary`）；改的时候又矫枉过正，
写了「`max_length` 不是校验、只是配合切片」——实测证明它就是校验，会拒。两次都是没有先跑一遍
就下断言，与 §0 的授权记录无关，是实现前的取证义务没做到位。



---

## 2. 范围

**做**：一个登录用户，对**一个**可读岗位，主动请求一次匹配，拿到「总分 + 五项分项 + 已满足及证据 + 部分满足 + 缺口与建议行动及预计验证方式」，前端展示；不落库任何分数。

**不做**（全部明确排除，写在这里是为了下次不能顺手加）：
- 广场按匹配度排序、批量匹配、自动推荐、消息推送；
- 匹配结果持久化、匹配历史、趋势曲线；
- PRD `:152` 的「学生对结果提出纠错」——那是新表 + 新状态机，另出 ADR；
- 技能字典表 / 同义词归一（ADR-011 §2.E 说角标的价值就是为这个决策攒证据，攒够了再议）；
- 角色系统、「管理员配置权重」；
- 用 `grade`、`experiences.start_date` 推算年龄/届别，或任何形式的敏感属性及其代理进入特征空间。

---

## 3. 端点契约

🚩 **决策 1**：`POST /api/v1/jobs/{job_id}/match`。

- 用 **POST** 不用 GET：它会向 DeepSeek 发起一次真实调用、消耗用户额度、受速率限制。语义上是「执行一次计算」，不是读状态。与 `POST /jobs/extract-skills` 同族。
- 权限：沿用 ADR-011 的可见性谓词——`published` 任何登录用户可读即匹配；他人 `draft` 一律 **404 不是 403**（403 会确认「该 id 存在」）。回收站里的岗位 404。
- 请求体：空（匹配对象 = 当前用户**默认档案** vs 路径里的岗位）。🚩 **决策 2**：v1 只支持默认档案，多档案各自的匹配留到下一轮——档案切换与匹配缓存放一起会立刻把「分数不落库」这条冲掉。
- BYOK：请求头 `X-DeepSeek-Key`，与润色/提取同一份 Key、同一套「不落库、不写日志、不出现在异常文本」的规则。
- 限流：**`10/minute` 按用户键**，新增常量 `MATCH_RATE_LIMIT`。
  🚩 这一条是 §13 架构复核改的，原文写的是「`10/minute/user`，与 extract-skills 同量级」——**那句在本仓库当时不成立**：
  `app/core/rate_limit.py:72` 的 `Limiter` 用 `key_func=get_remote_address`，现有三条规则（`AUTH` / `POLISH` / `EXTRACT`）
  实际全部**按 IP**计数，`rate_limit.py:35` 自己也写了「多 worker 部署时每个 worker 各算一份」。
  按 IP 对本端点不是措辞不严谨，是错的：局域网实测那批人共用一个出口时，10 次/分钟会被前一两个账号点光，
  后面的人拿到 429 —— 而这恰恰是唯一一个「别人替你花不了钱、但能替你花 Key」的端点。
  实测 `Limiter.limit()` 的签名支持装饰器级 `key_func=` 覆盖（`slowapi` 的 `limit(limit_value, key_func=None, per_method, methods, error_message, exempt_when, cost, override_defaults)`），
  所以**只给这一条端点换键**，另三条一个字不动；已知局限照抄 `rate_limit.py:35`：进程内计数，多 worker 各算一份。
- 状态码边界（同样来自 §13 复核，实现时最容易写错的一条）：**本端点不因 AI 失败返回 4xx/5xx**。
  润色与提取把 AI 失败抛给 `app/main.py:259/266/277` 的三个处理器（400 `AI_KEY_MISSING` / 401 `AI_KEY_INVALID` / 502 `AI_SERVICE_UNAVAILABLE`）；
  匹配按 §6.5 要**退化**，所以这三个异常在 service 层就地转成「关键词模式的成功响应」，处理器一个都不新增、也不复用其状态码。
  会走到处理器的只有：未登录 401、岗位不可见 404、限流 429、参数校验 422。
  退化必须可归因，见 §6.5 的 `data.meta.degrade_reason`。

响应 `data` 载荷（信封仍是 `{data, meta:{request_id}}`，业务 meta 在 `data.meta`，与 ADR-011 2.A 同构）：

```
data.profile_id         本次被打分的是哪一份档案（§13 复核新增：请求体为空 ⇒ 档案是隐式选择，必须回显）
data.profile_title      档案标题，UI 直接写「本次按《xxx》这份简历计算」
data.job_id             路径里那个岗位，回显而不是让前端自己拼
data.score              0..1，总分（保留 3 位小数）；**五项全免加权时为 `null`**（§5.1 的 F1:A，2026-11-08）
data.weights_version    "v1"
data.terms              五项分项，每项 {name, weight, value, computed: bool, note}
data.covered            [{skill, evidence: [{experience_id, snippet, strength}]}]
data.partial            [{skill, reason}]          命中但证据弱的
data.gaps               [{skill, action, verify}]   未命中；action=建议行动，verify=预计验证方式
data.meta               见 §8
```

`profile_id` 这一格不是贴心功能，是防一类具体的误解：编辑页与岗位页各自独立，用户在编辑器里开着 B 档案、
去岗位页点匹配，拿回来的是**默认档案 A** 的分数。不告诉他是谁的分数，他第一反应会是「分数算错了」。

PRD `:181` 要求「至少展示三条可验证证据或明确缺口」：`covered[].evidence[].snippet` 必须是经历原文的**连续子串**（服务端核验，核不上就丢），这一条同时是验收标准也是反幻觉闸门。

---

## 4. `self_rating` 的处置

🚩 **决策 3**：**彻底删除**，而不是留着不用。

- 迁移：`skills` 表 drop `self_rating` 列；`downgrade` 重新加回为 `SmallInteger NOT NULL server_default '3'`（能回退，且回退后与今天的数据分布一致）。
- 代码：`SkillInput` / `SkillRead` / `SkillDraft`（`app/schemas/resume.py:32`、`app/schemas/profile.py:170`、`app/services/profile_service.py:254`、`app/services/resume_service.py:196`）同步去掉字段；仓储两处构造点删除。
- 前端：`DEFAULT_SKILL_RATING`、草稿抹平逻辑、`api/types.ts:80`、以及 `resume.ts:984` 的字段白名单一并清理。
- 文档：`docs/API.md` 技能字段表与示例载荷。

理由：留着它，等于让每一个新来的人（包括未来的我自己）以为「技能上有熟练度」，而它恒等于 3。**一个恒定值的必填字段比没有字段更坏**：它要求客户端必须编一个数。

替代方案（不采用）：
- **保留列但停止必填**：改动小，但库里继续长出一列死数据，且 API 仍然骗人。
- **把列改成可空、将来用作 AI 评分的落库处**：§2 已决定分数不落库，这是为不存在的需求预留 schema。

### 4.1 已执行（2026-11-07，迁移 `20261107_0013`）

- 库：`skills` 列实测剩下 `['id', 'student_profile_id', 'name', 'created_at', 'updated_at']`；
  `ck_skills_self_rating` 随列一并消失（**实测**，不是照抄 MySQL 文档——迁移注释里那句
  「DROP COLUMN 会连带删除引用该列的 CHECK」现在有观察结果撑着）；25 行技能数据一条没少。
- 代码：`SkillInput` / `SkillRead` / `SkillDraft` / 仓储两处构造 / 服务层两处转发全部去掉该字段；
  前端 `types.ts`、`resume.ts`（含 `DEFAULT_SKILL_RATING` 常量本身、`updateSkill` 的 field 分支、
  本地草稿恢复与脏检查的忽略项）同步清理；`/resumes/polish` 的 `SkillInput` 同源，无需另改。
- 测试：`tests/test_migrations.py` 加了一条**显式**断言（列不在 + 列集合精确等于剩下五个），
  而不是靠「NOT NULL 写不进去」间接暴露；`test_put_profile_rejects_invalid_rating` **删除**，
  因为它的被测对象不存在了——留着一个恒真断言就是假绿。
- 兼容性：`SkillInput` 不再声明该字段，Pydantic 默认丢弃未知键，所以旧客户端多发的那个
  `self_rating: 3` 不会报错也不会入库。这一句写进 API.md，免得将来有人以为「还能传」。


---

## 5. 目标偏好字段

🚩 **决策 4**：`student_profiles.target_role`，`String(200)` **可空**，不建 CHECK、不建枚举、不加索引。

- 为什么挂在档案上而不是用户上：现有模型是「一人多档案，每份档案一个 `title`」（ADR-006），「投后端」「投算法」两版正好对应两份档案，偏好跟着档案走才自洽；挂 `users` 会强迫一个人心智上只有一个目标。
- 为什么可空且不设默认：**没填的人不该被系统性扣分**，见 §5.1 的权重归一。
- 不建枚举的原因：枚举会把「我想做 AI 产品」这类自由表达强行折进我们的词表，而匹配项只需要它和岗位 `title` 做字符串级比较。
- 填写入口：档案编辑页加一格「目标岗位（选填）」，与 `full_name`/`email` 同级。老板指令 6.3 说「在简历或者哪里填写」，我选档案而不是简历，因为简历载荷 `content_html` 是渲染产物、不是输入源；输入源在档案。🚩 **决策 5**：这条入口位置请确认。

### 5.1 未填时不惩罚：权重重新归一

```
score = Σ(w_i · v_i | term i computable) / Σ(w_i | term i computable)
```

`data.terms[].computed` 与 `data.meta.weight_sum_used` 如实暴露「这次是几分母算的」，**两者自 2026-11-08 起是对外契约**（老板裁定：改名/删字段要另出 ADR，不再算实现细节）。替代方案「缺项记 0 分」不采用：那会让「刚注册、什么都没填」的用户稳定拿到 0.1 以下，等于用分数惩罚沉默，产品上是错的，PRD `:200` 的禁止歧视虽不直接覆盖它，但同一判断力应该看得见这种后果。

**2026-11-08 补：这条理由当时被我们自己的行为打脸了一次（老板裁定 F1:A）。**
归一化在「一项都得不出」时退化出 `used == 0`，实现里写的是 `return 0.0, 0.0`，
于是新注册、什么都没填的人拿到的正是上面那句话点名的现象——0 分。取证：

```
score = 0.0  weight_sum_used = 0.0
coverage=None/no_required_skills  evidence=None/no_matched_skills  relevance=None/not_measurable_v1
preference=None/no_target_role    remaining_time=None/no_deadline
```

而 v1 实际出现过的有效分母是 8 个离散值（枚举 24 组合跑出来的，不是推的）：
`0.0 / 0.10 / 0.20 / 0.45 / 0.55 / 0.65 / 0.75 / 0.85`。

裁定：**`score` 改为可空**（`null` = 这次没有可加权的项），`meta.note="nothing_computable"`，
界面显示「—」加一句用户修得动的话；`0.0` 保留原意（「算出来了，零覆盖」）。
`None` 与 `0.0` 的分工必须由测试钉住，否则「压成一个数」仍然全绿
（`tests/test_match_scoring.py::TestNormalizeScore` 两条一对，端到端各一条）。

**2026-11-08 真浏览器验收把裁定句里「一句用户修得动的话」这一半证伪了，界面按实测改。**
`score=null` ⟺ 五项全不得分 ⟹ `coverage` 不得分 ⟹ **岗位一条要求技能都没填**
（枚举 3×3×3×3=81 个组合，`tests/test_match_scoring.py::test_score_is_null_only_when_the_job_has_no_required_skills`）。
所以这一格的用户**没有任何东西可修**——分数算不出来是因为岗位空着，不是因为简历空着；
而 `no_required_skills` 那一行说明本来就在卡上。我照裁定句另写的一句独立文案，
实测与它同屏重复，档案为空时还撞上 `.match-source` 的「你还没有保存的简历…」成三行同义反复
（重复的那两行在真浏览器里读到；三行那一格没在浏览器里造，是把文案放回原样后由
`JobDetailView.spec.ts` 的两条计数断言跑红的）。
文案已撤，说明只留 `.empty` 那一行。**裁定句里「修得动」的措辞是错的，保留原文不改写，**
更正记在这里：`null` 那一屏只需要说清「没有可比对的东西」，不需要给用户派活。

**反方向不成立，而它比 null 那一格更值得裁定**（并入 §7.4 的未决问题一起看）：
岗位没填要求技能、但用户填了目标岗位或岗位有截止时间时，分母只剩 `0.10`/`0.20`，
那一项自己就被重归一成总分。实测（同一账号、同一档案，`target_role="Python 后端开发工程师"`）：

| 岗位（均**未填**要求技能） | 命中的 preference 档 | `score` | 这一屏的样子 |
|---|---|---|---|
| 标题逐字等于目标岗位 | `contains` 1.0 | **1.0** | 「100% 匹配度」+「这个岗位还没有填写技能要求，所以没有可比对的条目。」 |
| 标题含「后端」两字 | `token_overlap` 0.6 | 0.6 | 同上，60% |
| 标题与偏好无关 | `unrelated` 0.2 | 0.2 | 同上，20% |

前一行是 §5.1 拒绝的「用分数奖励/惩罚某个字段」的镜像：手工录入的实习岗常不填技能要求，
于是「100%」会出现在一个**从来没跟技能比对过任何东西**的岗位上，而同屏另一句话承认没有可比对条目。
「有效分母不同」老板已接受，但 0.10 分母能刷满格是这句话最极端的角落，
所以在这里显式提出来：**是否让 `no_required_skills` 一律不给分（`score=null`），等裁定。**
本轮没动实现——改它动的是 §5.1 的公式，不是文案。

### 5.2 已执行（2026-11-07，迁移 `20261107_0014`）

改动面：`student_profiles.target_role`（`String(200)` 可空，无 CHECK、无索引）+ 档案读写链路 + 编辑页一格标签「目标岗位」、placeholder 注明选填并给了一个示例写法，位置在「简历邮箱」之后，与 `full_name`/`email` 同级。没建枚举、没建词表，按上面「不建枚举的原因」执行。

三条本轮学到的、值得留给后续的实现约束：

1. **`ProfileRead` 是手工构造的，不是从 ORM 行 `model_validate` 出来的**（`app/services/profile_service.py:391` 逐字段传参）。所以新增读字段的第一版写了 `target_role: str | None = None`，漏传时不会报错，只会让接口**静默**返回 `null`——测试先写就发现了（`test_put_profile_round_trips_target_role` 直接失败），随后把默认值去掉，让漏传在构造点就 `TypeError`。可空字段的「读路径默认值」和「缺省不改动」是两回事，前者是 bug 的温床。
2. **`PUT /profiles/me` 是整体覆盖语义**（PUT-as-replace，本轮没改它），所以载荷里不带 `target_role` 等于把它清空。这不是缺陷，但必须被一个测试钉住，否则后续任何「只提交部分字段」的前端改造都会静默吃掉用户已填的偏好：见 `test_put_profile_without_target_role_clears_it`。同页的 `paper_size`/`theme` 走的是另一套「缺省不改动」三态，两者并存是既有设计（ADR-007），不要顺手统一。
3. **可选新字段禁止进本地草稿的形状判据**（`frontend/src/stores/resume.ts::isProfileDraft`）。我第一版就把 `typeof candidate.target_role === 'string'` 加了进去，实测后果是：`DRAFT_VERSION` 仍是 1，上一版前端存的草稿少这一格 → 判据直接判整份草稿无效 → 用户不只是丢掉这一格，而是连同**所有**未保存的修改一起被判废，界面上连「恢复草稿」横幅都不出现。`a7a1fe2` 加 `layout_config` 时没这么写，就是同一个道理，我踩的是本项目已经避过的坑。已回退，并留了回归用例 `缺 target_role 键的老草稿必须仍能恢复`（反向探针：把那行判据加回去，用例立刻红 —— `expected null not to be null`）。可选字段的兼容由 `normalizeDraft()` 补默认值负责。

前端侧验证只到 `vue-tsc` + `vitest` + 后端契约测试（`tests/test_profile_contract.py` 的 `FRONTEND_PAYLOAD_KEYS` 已加 `target_role`），本轮**没有**跑浏览器实测这一格输入框；375px 与真机行为留到 §10 第 7 步的匹配卡一起补。

---

## 6. AI 参与打分的边界（🚩 决策 6：这条动的是你自己划的线，必须单独确认）

`frontend/src/api/client.ts:207-222` 的注释是项目里最硬的一条安全约定，原文：

> 判据是一份**白名单**而不是模式匹配，且只列真正消耗用户 AI 额度的端点：目前两个——`/resumes/polish`（ADR-010）与 `POST /jobs/extract-skills`（ADR-011 2.A）……**每扩一个 URL 都必须能回答「这个请求会不会被转发给 DeepSeek」，答不上来就不许加。**……匹配刻意做到**整段相等**……宁可写死两条精确路径。

指令 6.2 的「配合接入 AI 分析打分」如果要在服务端做，白名单就要加第三条，而这条路径是**参数化**的（`/jobs/{job_id}/match`），没法「写死整段相等」。我的方案：

```
/^\/jobs\/[^/]+\/match$/
```

并写明为什么这个模式不构成扩张：`[^/]+` 只允许**一个**路径段、不含 `/`，因此不可能命中 `/jobs/trash`、`/jobs/extract-skills` 或任何其它端点；匹配成功后请求**确实**会转发 DeepSeek，满足那条约定要求的自证。测试要把「`/jobs/x/y/match`、`/jobs/match`、`/jobs/1/match/extra` 三个都不注入」写成断言。

### 6.1 上面那句自证不够严（§13 复核更正，实现以此节为准）

「匹配成功后请求确实会转发 DeepSeek」这句在字面上是**假的**，我写的时候没算过。实测 `client.ts:224-230` 现在的判据是
`BYOK_KEY_PATHS.includes(path)` —— **整段相等**，模式机制今天不存在，加正则等于新引入一种判据。而 `/^\/jobs\/[^/]+\/match$/`
会命中 `/jobs/extract-skills/match`、`/jobs/trash/match` 这类**根本没有路由**的路径（`[^/]+` 吃得下 `extract-skills`），
这些请求返回 404，一个字节都没发往 DeepSeek。所以「命中即转发」只能对**已注册的路由**说。

可辩护、且能测的版本是**路由表论证**，三条断言分工明确，全部加进 `frontend/src/api/__tests__/byok.spec.ts` 现有的注入矩阵：

1. **正向**：`/jobs/{id}/match` 注入 Key（`{id}` 取一个真实 uuid 与一个非数字串，防止判据意外写成 uuid 形状）。
2. **不扩张**：`/jobs/x/y/match`、`/jobs/match`、`/jobs/1/match/extra`、`/jobs/1/match/`（尾斜杠走 `client.ts:229` 的既有归一化）、`/jobs/1/match?x=1` 五种都不注入 —— 前四种是模式本身不该吃到的形状，后两种是「归一化之后仍只认那一条」。
3. **路由表闭合**：这条**不属于 vitest**（前端不知道后端有哪些路由）。它落在后端测试里：从 `app.openapi()["paths"]` 取 `/jobs` 下
   **所有已注册路由**，断言「凡是能被这条正则命中的 `POST` 路由，其处理器一定走 DeepSeek」。判定方式用白名单式的显式集合，
   而不是猜函数体：今天的答案是一个已知列表（`/jobs/{job_id}/match` 一条），将来新增匹配形状的路由时这条测试必须红。
   这条是把上面两句空话换成可执行判据的关键：它守的是未来有人新加一条 `/jobs/{a}/{b}/match` 形状的路由而不转发 AI 的情形，
   而那正是这条约定唯一真正的失效方式。

另外补一句实现纪律：**正则只允许出现在「是否需要注入 Key」这一个判断里**，不许被复用成「这是不是匹配端点」的业务判断；业务判断只看 `job_id` 是否存在。判据的作用域写窄，将来才守得住。

**AI 的职责被限死为「打分」，不碰「有没有」**（这是本端点最重要的结构决定）：

1. 确定性层先算出：哪些岗位技能在学生的技能名里命中（§7 的键）、每个命中技能在哪几条经历文本里出现（关键词）。
2. **只有第 1 层已经命中**的条目才交给模型，输入是 `(技能名, 命中片段列表)`，要求返回每条片段的 `strength ∈ [0,1]` + 引用片段编号。
   **片段不是整段经历，是命中位置周围的窗口**（§13 复核补，理由见下面「输入预算」）：以技能名在文本里每次出现的位置为中心，
   取前后各 `MATCH_SNIPPET_RADIUS = 80` 字，一条 `(技能, 经历, 字段)` 最多一个窗口。返回给用户的 `covered[].evidence[].snippet` 与交给模型的片段是同一份，
   所以「模型看到的」与「我们核验的」与「用户读到的」三者逐字相同，§3 那条连续子串闸门才有意义。
3. 服务端核验：模型引用了我们没给过的片段编号 → 丢弃并计数；模型给出技能名不在我们给它命中的集合里 → 丢弃并计数；`strength` 越界 → 夹到 [0,1] 并计数。
4. 模型**从不**被问「这个学生匹不匹配这个岗位」，也从不收到完整简历或完整 JD——只收到已经定位好的片段。这样它既无法发明覆盖，也拿不到可以反推身份的整体上下文。
5. 无 Key / Key 无效 / 超时 / 429：`证据强度` 项退化为**确定性代理值**（命中经历条数 / min(条数,3)，见 §7.2），并在 `data.meta.evidence_mode` 与 UI 文案里**显式说明当前是关键词模式**，绝不静默降级（AGENTS.md §3.2 那条「失败时必须由用户点出来」的口径沿用）。
   退化必须**可归因**：`data.meta.degrade_reason ∈ {key_missing, key_invalid, upstream, bad_output, timeout}`，AI 正常参与时为 `null`。
   这五个值是 `app/main.py:259/266/277` 三个处理器今天各自对应的失败（400/401/502）加上「输出不可用」的两类，
   在 service 层一一对应转换：`ResumeApiKeyMissingError → key_missing`、`ResumeCopyRejectedError → key_invalid`、
   `ResumeCopyUnavailableError`（及其子类 `SkillExtract*` 翻译过来的超时/上游错）`→ upstream|timeout`、JSON 不可解析/空内容 `→ bad_output`。
   **实现要求**：五种情形各一个测试，断言 200 + `evidence_mode="keyword_fallback"` + 正确的 `degrade_reason`，
   并且断言**没有任何一种**走到 `app/main.py` 的 AI 处理器（否则 §3 那条状态码边界就是空话）。
   UI 文案按 `degrade_reason` 分句，不许把「你没配 Key」和「DeepSeek 正在超时」写成同一句话——两者的用户动作完全不同。
   > **2026-11-08 补第三种，它不是退化**：`evidence_mode="keyword_fallback"` 且 `degrade_reason=null`
   > 只有一个来源——命中的技能在经历里**一个片段都没切出来**，`_score_with_ai` 在检查 Key **之前**
   > 就短路返回了，所以根本没向模型提问（配了 Key 也一样，老板实测到的正是这一格）。
   > 此时「按关键词算法给分」是**假话**：关键词算法什么都没算，那一格记的是 0 分并参与加权
   > （`terms[].note="matched_without_evidence"`）；而用户能修的动作也不是配 Key，是把经历写出来。
   > 界面因此分三句：AI 参与 → 不说；AI 失败 → 算法句 + 原因句；零候选 → 「没向 AI 提问、也没花额度」
   > + 「把那段经历写进简历再算一次」。岗位没填技能要求（`no_required_skills=True`）时一句都不说。

   > **实现时的一处偏离（7d，已执行）**：§10 第 6 步原稿写着「`main.py` 处理器作用域从『两个端点』改
   > 『三个端点』的注释与测试」。这句在本节定稿之后**就不成立了**——匹配故意不进那三个处理器，
   > 所以处理器的作用域今天仍然是**两个端点**，注释改成「第三个 AI 端点显式不经过这里」，
   > 并由 `test_ai_failures_degrade_to_200_not_an_error_envelope` 六种退化各一条钉住。
   > 另外 §6.5 的五种值里 `upstream` 与 `timeout` 必须分得开，所以适配器多了一个
   > `EvidenceScoreTimeoutError`（`APIConnectionError` 那一支）；不新增这一类，服务层就只能瞎猜，
   > 而猜错的那句话是「等一会儿再试」，用户真正该查的是自己的网络。

**输入预算**（§13 复核新增，之前这一条是漏的）：交给模型的字符数不能由用户的自由文本决定。实测：
单档案技能 ≤8（`app/schemas/profile.py:271`）、经历 ≤3（`:272`），但 `description`/`responsibility` 在
`app/schemas/resume.py:67-68` 是**裸 `str`、没有任何长度约束**，库里是 `Text`（`student_profile_repository.py:129-130`）。
dev 库现有 8 条经历的 `description` 长度是 p50 44 / p90 102 / **max 615** 字——也就是说今天最真实的一条已经能到几百字，
而 3 条经历 × 2 个字段 × 8 个技能若整段送，最坏情况没有上界。窗口规则（上面第 2 点）把上界变成
「命中次数 × 160 字」，与自由文本长度**解耦**，因此不需要给 `ExperienceInput` 加 `max_length`（那会是对用户数据的截断式改动，属于另一个决定）。
`data.meta.fragments_truncated` 记录被窗口切掉的片段数。
> 附带记录一个**不在本切片范围**的既有事实：润色端点是整段送 `description`/`responsibility`（`app/integrations/copy_writer.py:171-172`，
> 只有输出侧的字数指引，没有输入侧上限）。这是 ADR-010 的既有暴露面，本轮不动它，也不假装它不存在。

调用参数照 ADR-009/011：`deepseek-flash`、`stream=False`、`thinking.disabled`、`max_retries=0`、30 秒预算、`max_tokens` 由实现时实测输出长度定（不预设 1500）。Key 仍然只在请求头，服务端不落库、不写日志、不进异常文本。

**隐私新增面**：本端点会把学生的技能名 + 命中所在的经历片段、以及公开岗位的 JD 片段发给 DeepSeek。前者是本人 BYOK、本人在点，与润色同源；后者是发布者自愿公开的岗位描述文本。不发 `full_name`、`email`、`contact`、`grade`、`school`。实现时这条「不发清单」要写成测试，而不是注释。

---

## 7. 五项分项的定义

权重沿用 `docs/PRD.md:119`，常量与版本写在 `app/services/match_scoring.py`：
`MATCH_WEIGHTS = {coverage: 0.45, evidence: 0.20, relevance: 0.15, preference: 0.10, remaining_time: 0.10}`，`MATCH_WEIGHTS_VERSION = "v1"`。
（`freshness` → `remaining_time` 是 §7.5 改名的连带项，键名会直接进 `data.terms[].name`，所以是对外契约的一部分，定在本文而不是留给实现时随手起名。）

### 7.1 技能覆盖 0.45 —— 纯确定，可全测
```
R = 岗位要求技能（仓储全量，去重后）；M = 学生技能
coverage = |{ s ∈ R : ∃ m ∈ M, forms_match(s, m) }| / |R|      |R| == 0 → value=0, computed=False, meta.no_required_skills=True
```
分母只用 R：学生多出来的技能不加分，避免「技能堆得多」把匹配度刷高。`computed=False` 时该项退出加权（§5.1）。

`forms_match` 是 `skill_forms_match`（§1.3 的 2026-11-08 更正）而不是裸的键全等：
`Python3` 顶得住 `Python`，但 `C++`、`JavaScript`、`MySQL` 顶不住 `C`、`Java`、`SQL`。
去重键 `skill_name_key` 不受影响，两行照样存两行。

### 7.2 证据强度 0.20 —— 两层，AI 只在外层
```
命中集 H = { s ∈ M∩R : s 出现在至少一条经历的 title/description/responsibility 中 }
AI 可用：value = mean(strength(s))，strength 来自模型且已核验引用
AI 不可用：value = mean( min(hits(s), 3) / 3 )，meta.evidence_mode = "keyword_fallback"
H 为空：value = 0，computed = True（这是真话：有技能名但零证据）
M∩R 为空：value = 0，computed = False（没东西可评，不假装「评为 0」）
```
`hits(s)` 是命中该技能的**经历条数**，不是出现次数——同一句话里写三遍 Python 不该刷分。

`covered` 与 `partial` 的分界（§3 的三档展示在这里落地，原稿没定，实现时必须定）：

```
PARTIAL_STRENGTH_THRESHOLD = 0.3
某技能的平均强度 < 0.3 → 进 partial；否则进 covered
```

**这条阈值被集成测试改过一次：0.4 → 0.3**，理由值得留档，因为它是一次典型的「代理指标越权」：

- 0.4 是我按关键词档位（`0 / ⅓ / ⅔ / 1`）算出来的中点，看起来干净。
- 但 `min(hits,3)/3` 测的是**这个技能出现了几条经历**，而本产品的档案最多只允许 3 条经历、
  一段项目是常态。0.4 的实际后果由 `test_job_match.py::test_evidence_snippets_are_substrings_of_the_stored_text`
  暴露：一份「一段真实项目 + Python/FastAPI」的简历，`covered` 是**空的**——
  用户看不到任何一条「已满足 + 证据」，尽管他确实写过出处。
- 那不是证据弱，那是关键词模式在替它没资格做的判断下结论。「提到名字但没做过」这件事
  只有 AI 模式分得出来（Prompt 里 0.3 那一档就是「只是提到了技能名」）。
- 0.3 让边界回到两种模式各自真正在测的东西：关键词模式 = 有没有一条真实出处；
  AI 模式 = 模型认为这句话撑不撑得起。零出处的命中技能不走这个阈值，见下面那条规则。

还有一条同样是测试逼出来的补充规则（原稿三个清单的语义有洞）：

```
技能名命中、但在经历里找不到任何片段 → 必须进 partial（reason=「技能名命中，但经历里找不到出处」）
```

它原来会**三个清单里都不出现**：`strengths_by_skill` 是从候选对分组的，零候选的命中技能
不在分组里，于是既不是 covered 也不是 gap。§3 要求「至少三条可验证证据或明确缺口」，
这种两头都不算的消失等于对用户隐身——用户看不到「你会 Docker 但没写过」这条最有用的话。

两种含义都成立但不同，所以 `partial` 的文案里不许假装解释了是哪一种，`evidence_mode` 必须一起显示。

### 7.3 经验相关度 0.15 —— 代理指标，明说它是代理
不引中文分词依赖（新依赖要另走 ADR）。定义：岗位 `title` 的关键片段与经历 `title` 的匹配，
```
relevance = max over experiences of |tokens(job.title) ∩ tokens(exp.title)| / |tokens(job.title)|
tokens(x) = 对 match_key(x) 按非字母数字切分后的片段集合（中文整串当一个 token）
```
它测的是「他做过的事的名字，像不像这个岗位的名字」，**不是**语义相关度。UI 上这一项的说明文字要写「岗位名称与经历名称的字面重合」，不写「经验相关度」这种听起来像理解了对方的词。AGENTS.md 第 7.9 条禁止用大词包装未验证的东西，这条同样适用于一个分数标签。

#### 7.3.1 实现时的否决（2026-11-07，7a）：v1 这一项不参与加权

上面这个指标**没有被实现**，因为先测了它，测出来是空的：

```
job titles: ['大模型算法实习生', 'Python 后端开发工程师', '前端开发实习生', '数据分析实习生', 'AI 产品经理实习生']
exp titles: ['1', '占位符', '项目名称121', '123', 'AI职业规划', '校园二手交易平台', '知识图谱问答系统', '…（窄屏测试标题）']
pairs: 40  with any token overlap: 0
```

dev 库 5 个已发布岗位 × 8 条经历，**0/40 重合**。原因不是数据脏，是概念错了：
经历的 `title` 是**项目名**（「校园二手交易平台」），岗位的 `title` 是**角色名**（「大模型算法实习生」），
这两个字符串本来就不该相似。把「中文整串当一个 token」放进去算交集，等价于问「这两个名字一模一样吗」。

于是 v1 的处理是：

- 这一项**恒 `computed=False`**，`note="not_measurable_v1"`，`weight_sum_used` 如实少 0.15（§5.1 的归一在这里生效）；
- **不删掉这一项**：保留在 `data.terms[]` 里，是为了让「PRD 五项中有一项我们现在测不了」这件事在响应里可见。
  删掉它，等下一次有人对着 PRD 数分项，会发现少了一项而不知道原因。
- 一个恒为 0 的 `computed=True` 项**绝不接受**：那会把每个人的总分按 0.15 拖下去，
  与我们删 `self_rating` 的理由（§1.2「零信息常量」）完全同一，只是这次它长在公式里而不是表单里。

两处文字也要更正，否则下一个读这份 ADR 的人会照抄错的定义：

1. 公式里的 `match_key(x)` **不存在**，也不该存在——§1.3 已经裁定过「没有新增 `match_key()`，
   `skill_name_key` 就是唯一口径」。这里写成 `match_key` 是我自己前后不一致。
2. `tokens()` 的实现口径改为**拉丁词/数字串各一个 token + 中日韩按二元组切**。
   二元组不是为了让 §7.3 复活（它仍然不参与），而是 §7.4 的目标偏好比对**真的需要**一个能看见中文内部重合的函数：
   整串口径下「大模型算法」对「大模型算法实习生」只能靠 §7.4 第一条的**包含**关系得分，
   而包含关系看不见词序变化（「算法大模型」→ 落不进任何一档）。
   `tests/test_match_scoring.py::TestTokens` 把「整串口径为 0、二元组口径 > 0.5」钉成断言。

### 7.4 目标偏好 0.10
```
未填 target_role → computed = False
否则：规范化后互相包含 → 1.0；token 重合 ≥ 0.5 → 0.6；其它 → 0.2
```
0.2 而不是 0：填了偏好但不相干，本身是有效信息（说明这个人知道自己要什么，而这个岗位不是），归零会让「填偏好」变成纯亏。

**2026-11-08 实测补记（这一条尚未裁定，写下来是为了让「未改」是一个决定而不是遗漏。）**
上面那句理由在**定分母**下成立，在 §5.1 的重归一下只成立一半：免加权拿的是其余项的加权均值，
所以「填了但不相干」比「留白」更低。同一个人、同一份档案、同一岗位，只有偏好一格之差（真实跑 `score_match`）：

```
岗位无 deadline：留白 0.449 → 填不相干 0.415（−3.4 个点）→ 填相干 0.522（+7.3 个点）
岗位有 deadline：留白 0.482 → 填不相干 0.449（−3.3 个点）→ 填相干 0.543（+6.1 个点）
```

三个选项：**(a)** 保留 0.2（本轮按此项实现，未改代码）；**(b)** 「不相干」也记 `computed=False`，
于是填与不填同分，代价是丢掉「你的目标不是这个岗位」这条真实信息；**(c)** 保留 0.2 但在界面解释分母——
与 2026-11-07「权重/分母不进 UI」的裁定冲突，且不是一句用户修得动的话（唯一修法是改口填相干的，那是造假）。
幅度只有 3 个点、分数不做排序（§12 未改项），所以本轮按 (a) 走并把数值留在这里，等老板拍。

**同一处的第二个未决问题**（2026-11-08 真浏览器实测，取证与表格在 §5.1 末）：0.2 这一档在
`required=()` 的岗位上会被重归一成**总分**，实测能把「什么都没比对」的岗位显示成 100%。
它与 (a)/(b)/(c) 是同一个判断的两半——都在问「一项弱信号能不能代表整体」，所以一起拍最省事。

### 7.5 剩余投递时间 0.10（原名「时效」，§13 复核改名）
```
deadline 为 NULL → computed = False      （§13 更正，见下）
已过期           → value = 0.0，computed = True
距今 ≤ 7 天      → 0.4    ≤ 30 天 → 0.7    > 30 天 → 1.0
```
判据用应用时钟，与 `is_expired`**同一个函数**，不允许端点里再写一遍 `deadline < today` 的比较。

落地形态（实现时定的，比原稿具体）：判据本体在 `app/core/deadline.py::is_expired(deadline, today)`，
`JobService.is_expired(job, today)` 改为**委托**给它（签名不变，调用点一处没动）。
放在 core 而不是留在 service 里，是因为纯打分层 `match_scoring` 不能 import 带仓储依赖的 service 判据；
同一个位置的先例是 `app/core/skill_names.py`（为完全相同的理由搬进来）。
（原稿这里还引过一个错行号：`app/schemas/job.py:255`。`is_expired` 现在实测在 `:235` 与 `:275`，
注释在 `:233`，`255` 那行是 `deadline` 字段本身——是 §1.1 那批行号重新推导时漏掉的一格，已改。）

两处更正，都是改我自己：

1. **原名「时效」是错的标签**。这一项测的是「还剩多少天能投」，不是 JD 新旧。按 §7.3 立的那条判断力（不许用听起来理解了对方的词），
   「时效」这个词会让人以为我们在评价岗位本身，所以改名。UI 说明文字写「距离该岗位投递截止还有多久」。
   注意这是一次**解释而不是一次引用**：`docs/PRD.md:119` 的原文是「时效/可用性」，它自己就没说清算什么，我把它落到 `jobs.deadline` 上。
   另一条路也物理存在，写清楚免得将来有人说我没看见：`jobs.created_at` 在库里（`app/repositories/job_repository.py:118`），
   「岗位有多新」是可以算的。但对本仓库的岗位它测的是**我们入库的时刻**而不是招聘方发布的时刻——
   实测种子脚本建的 5 个岗位（`company_name` 以「某」开头，全部 `published`）`created_at` 落在
   `2026-11-07 10:04:50.563 ~ 10:04:51.718`，跨 1.2 秒：这一批就是同一次建号动作，用它算出来的「新鲜度」彼此几乎相同，
   与岗位本身新旧无关。拿它当「岗位新鲜度」是把我们自己的动作当成别人的事实，所以不采用。
   老板若真要「JD 发布日期」，那需要一个新列（招聘方提供或抓取）→ 新 ADR，不改这里的语义。
2. **`NULL → 0.5` 参与加权与本 ADR 自己的原则冲突，已改为免加权。** §5.1 通篇说的是「算不出来的项不该进入分母」，
   §7.4 对未填偏好也确实这么办；而 deadline 缺失记 0.5 还**留在分母里**，等于把每个人的总分往中间挤——
   实测 dev 库 `status='published'` 共 6 行、其中 `deadline is null` 2 行；但那 2 行里有一行属于「已发布 + 已进回收站」的历史异常岗位
   （§9 末段那条，用户看不到），所以**真实可点的 5 个种子里有 1 个走这一支（20%）**。
   原稿那句「未知不装作来得及」的方向是对的，
   但正确的做法是**这项不参与**，而不是用一个中位数假装知道。
   连带后果：`meta.weight_sum_used` 会更经常地小于 1.0。**它留在 API 里给我们对账，不进用户界面**
   （2026-11-07 人类负责人裁定，原话：「**不用向客户说明技术上的设计**」，针对的正是我上一轮问的
   「总分实际按 0.85 归一这件事要不要对用户说明」）。
   这条裁定我接受，并把它的作用域写清楚，免得它被扩大解释成「失败原因也不用说」：
   - **不进 UI**：权重构成、哪几项免加权、`weight_sum_used`、`candidate_pairs` 这类计数、`not_measurable_v1` 这种 note。
     对用户只需要一个分数和三档清单，剩下的会变成一个他无法采取行动的问号。
   - **仍然进 UI**：`evidence_mode`（现在是关键词模式，不是 AI 打分）与 `degrade_reason`（你没配 Key / Key 失效 /
     上游超时）。这两类不是技术细节，是**用户自己能修的事**，藏起来等于让他对着一个降级结果当真实结果用。
   - **仍然进 UI**：「匹配度是参考、不是录用概率」这句定性（§11、PRD `:152`/`:218` 的要求），
     它是关于分数含义的说明，不属于「技术设计」。
   对账风险由 §8 承担：数字全在响应里，用户界面不显示不等于后端不记录。

---

## 8. 可观测 meta

沿用 ADR-011 §2.D.2 的「数字要能被用户对账」的做法：

| 字段 | 含义 |
|---|---|
| `model` / `prompt_version` | 实际使用的模型与提示版本；AI 未参与时为 `null` |
| `evidence_mode` | `ai_scored` \| `keyword_fallback` |
| `degrade_reason` | `null` \| `key_missing` \| `key_invalid` \| `upstream` \| `timeout` \| `bad_output`（§6.5 第 5 点，退化必须可归因） |
| `candidate_pairs` | 交给模型的 (技能, 片段) 候选对数量 |
| `verified_pairs` | 通过引用核验、真正参与打分的对数 |
| `dropped_unreferenced` | 模型引用的片段编号不在我们发出去的编号里（含「编号是真的但挂在另一个技能下」）的条目数 |
| `dropped_unknown_skill` | 模型返回的技能名不在本次命中集合里的条目数 |
| `dropped_unusable_strength` | 技能名与编号都对，但 `strength` 不可用（缺字段、非数字、布尔）的条目数（实现时新增，理由见下） |
| `dropped_missing_output` | 我们发出去了、却没有拿到可用评分的**候选对**数 |
| `clamped_strength` | `strength` 越界被夹过的次数 |
| `fragments_truncated` | 被 §6 第 2 点的窗口切过端的片段数（输入预算的可见性） |
| `truncated` / `finish_reason` | 与提取端点同义 |
| `weight_sum_used` | 本次实际参与加权的权重之和（§5.1 归一后的分母，**对外契约**） |
| `matcher_version` | 本次用的是哪一版「写法等价」判据（`app/core/skill_names.py::SKILL_MATCHER_VERSION`）。与 §10 第 3c 条那个端点的同名字段同源，一致性断言 (c) 靠它 |
| `note` | 目前只有 `nothing_computable`：`score` 为 `null` 时的标记（§5.1 F1:A）。**前端不按它分句**——实测 `null` 只与 `no_required_skills` 同时出现，那一屏的说明由后者负责（§5.1 末的更正）。留在响应里供对账与后续排障。有分数时恒为 `null` |
| `no_required_skills` | 岗位一条要求技能都没填（`|R| == 0`）。它同时是三件事的键：coverage 免算的原因、界面「没有可比对的条目」那行文案、以及 `score=null` 的唯一来路 |

计数有**两个分区**，各自精确闭合，单位不同，不许混进一个式子：

```
候选侧：candidate_pairs      = verified_pairs + dropped_missing_output
条目侧：len(model_items)     = verified_pairs + dropped_unreferenced + dropped_unknown_skill + dropped_unusable_strength
```

核验按固定次序判，一条返回条目只会落进条目侧的一个桶：
① 技能名不在本次交给模型的命中集合里 → `dropped_unknown_skill`（判完即止，不再看它的编号）；
② 编号不在我们发出去的编号里，或编号是真的却挂在另一个技能下 → `dropped_unreferenced`；
③ 没有可用的 `strength` → `dropped_unusable_strength`；
④ 前三关都过 → `verified_pairs`（越界但可夹的在这里计入，同时 `clamped_strength` +1）。
候选侧不看 junk：模型多编十条也不影响「发出去的候选对有没有拿到评分」，所以两个等式互相独立。

这里连着错了两次，都记下来：

1. **原稿**写「差值应等于两个 `dropped_*` 之和……这条**故意不做闭合断言**」。错在少了一个损失通道
   （模型只回一半条目时没桶装），那等于提前宣布「有一条损失我不数」。
2. **§13 复核给出的更正式版**写「四个桶互斥且穷尽，相加 = `candidate_pairs`」——**同样错**，
   而且是我在实现这一层的测试时跑红的：它把「候选对」（我们发出去的东西）与「返回条目」（模型吐回来的东西）
   两种单位塞进同一个等式。模型只要多编一条编号，等式两边同时各多 1，测试立刻不闭合。
   现在这两个分区分别断言，`tests/test_match_scoring.py::TestApplyModelScores` 两个方向都钉住。
   `dropped_unusable_strength` 也是在这一步冒出来的：它原来被 `continue` 静默吞掉，
   既不进候选侧也不进条目侧，正是第 1 条那个老毛病的复发。

先例引用也要更正：ADR-011 那条 `received = dropped + deduped + 返回 + 切掉` 里无字段的最后一格是**已知缺陷**，
不是可以照抄的判断，别拿它当先例给自己开路。而这一轮我自己照着它「再补一格」补出来的式子，同样是错的——
**把计数写成可闭合的两个分区**才是答案，不是把桶加多。

---

## 9. 前置任务：内部测试岗位（老板指令 6.1）

不属于匹配的代码，但没有它一切无法验收。约定：
- 取材方式：公开搜索结果与可访问的高校就业网/招聘聚合页（Boss直聘/猎聘详情页是 JS 渲染，直接抓只能拿到壳）。**文字全部由我们自己重写**，不逐字复制任何站点，不写具体真实公司名（用「某 AIGC 创业公司」这类形式）。
- 建号：只用于内部测试的账号，命名带可辨识前缀并沿用现有约定（今天的 `preflight_*` / `pf_*` / `nx_*` 已被要求排除在真实用户统计外）。岗位以这些账号创建、`published`、每人 3–5 条，覆盖 §1.1 的 5 类岗位（大模型算法实习、Python 后端实习、数据分析实习、AI 产品实习、前端实习）。
- 走真实路径 `POST /jobs` + `PUT /jobs/{id}/publish`，不用裸 SQL 插库——否则等于用假数据骗自己，且绕过 `JobSkillInput` 的全部校验。
- 每份岗位的技能条目控制在 8–15 条、必备/加分语义写进 `type`；`origin` **不是我们填的字段**——服务端用 `is_traceable(evidence_text, description)` 派生（`app/services/job_service.py:473`），`evidence_text` 必须是 JD 原文（归一化后）的连续子串才拿到 `jd_verifiable`，否则自动 `unverified`。所以种子数据想有可回溯徽标，就得真的把技能写进 JD 正文里，而不是在请求里声明。
- 红线：不含性别/年龄/户籍/健康/婚育，不含「2027 届」这类年龄代理，不含「无挂科」「限男性」。
- 测试账号与种子岗位在下一轮真实用户测试前必须下架/停用，并再次声明不计入真实用户统计。

**已执行（2026-11-07，脚本 `scripts/seed_internal_jobs.py`，PR #6）**：dev 库现有 `published`
岗位 5 条（`seedboard_a` 3 条 / `seedboard_b` 2 条），技能条目合计 61 条，其中服务端派生出
`jd_verifiable` 50 条、`unverified` 11 条——11 正好等于脚本里 `evidence_text=None` 的「加分项」条数，
说明徽标不是脚本声明的。`deadline` 铺了 +5d / +20d / +45d / +60d / NULL 四档加一个未知档，
让 §7.5 的每一支都有真实数据可点。脚本连跑两次后 `published` 仍为 5（第一版在这里会翻倍，
因为它只查草稿是否重名；已改成同时查 published 并按 `created_by_user_id` 归属过滤）。

顺带发现的**历史数据**：探针账号 `jobboardcheck01` 有一条 `status='published'` 且
`deleted_at` 非空的岗位（11-07 下架那两条探针岗位时留下的）。可见性谓词挡住了它，
用户看不到，但「已发布 + 已删除」这个组合是 ADR-011 1.D 缺口②（回收站无永久删除）的
一个具体后果，处理它属于岗位轮，不属于匹配轮。


---

## 10. 落地顺序（每条一个分支一个 PR，照 DEVELOPMENT.md §10.0）

1. **本文 + `MVP_SCOPE.md:42-43` 与 AGENTS.md §3.2 的解冻注记**（纯文档，先合）。
2. ~~种子岗位（§9，纯数据 + 一份可重放的建号脚本，不动 schema）~~ → **已执行**，PR #6。
3. **匹配键统一**（§1.3）：
   - **a/b 已执行**：档案侧 `SkillInput.name` 补 strip + 去重改用 `skill_name_key`，键本体移入
     `app/core/skill_names.py`（详见 §1.3 的两处偏差说明）。
   - **c 已执行**（2026-11-08 老板裁定：并进阶段 1，即本 PR；命名与判据都按他给的定义）。
     端点 `GET /jobs/{job_id}/requirement-match`（模型 `app/schemas/match.py::RequirementMatchResponse`），
     逐条回 `name` / `status: covered|partial|missing` / `matched_form`。三条「同源」是实现约束而不是愿望：
     取数同一个 `_gather`（一个 session、一份快照、同一个 `get_readable` 谓词）、
     分区同一个 `score_match`（本端点**不调 AI**，也不外露 `evidence_mode`——按定义没参与的东西没有「退化」可言）、
     写法命中同一个 `matched_with_forms`（`matched_required_skills` 改成它的投影，判等循环只剩一处）。
     验收判据按老板给的定义落成集成测试（同一 fixture 调两个真实端点，不比源码）：
     (a) `coverage` 的名字**顺序与条数**等于 `GET /jobs/{job_id}` 的 `skills`（U）；
     (b) `status ∈ {covered, partial}` 的集合 == 匹配的 `covered ∪ partial`；
     (c) 两边 `meta.matcher_version` 相等——**并且额外等于那个常量**，
         否则两边同时是 `null` 也能过，那是空检。
     `Python3` / `Python` 这条回归用例额外断言 `matched_form == "Python3"`。
     实现期间被测试当场抓出的一个界面缺陷：`v-if="skillStatus(name)"` 里 `missing` 是**非空字符串**、
     在 Vue 里是真值，于是每条技能都长了角标；改成 `isRequirementHit()`（只认 covered/partial）之后
     那条用例仍留在测试里防复发。判据 (b) 也解释了两档为什么可以合并成一个布尔：角标回答的只有「命中没有」，
     证据够不够是匹配卡那一层的事。
     **并集恒等不是巧合**：`score_match` 装配三档时把「命中却拿不到强度」的技能一律补进 `partial`
     （`app/services/match_scoring.py:715` 那一段），所以模型漏答、拒答、给出不可用强度都只能把一条
     命中从 `covered` 挪到 `partial`，挪不出并集。这件事原先只是我读代码推的，现在是一条测试：
     `test_badge_union_still_equals_match_union_in_ai_mode` 把每条 AI 强度都判成 0.1（全低于
     `PARTIAL_STRENGTH_THRESHOLD`），于是卡上 `covered` 必须全空、角标那侧仍有 `covered`——
     两边**逐档不等而并集相等**，否则这条断言就是在「两边恰好同模式」的巧合上过关。
     反向探针跑过：把端点里的 `partial` 一档判成 `missing`，两条并集用例立刻红，已还原。

     真浏览器实测（2026-11-08，Vite dev + uvicorn + 新建探针账号，档案技能写 `Python3`、
     经历原文含 `Python 3.13`，未配 Key 即关键词模式）三条都过：
     ① 角标只出现在 `Python` 那一行，`MySQL`（`status=missing`）没有；悬停 `title` 读出
     「按你简历里写的「Python3」认的」；② 每次挂载**只发一条**
     `GET /jobs/{id}/requirement-match`，且请求头里没有 `X-DeepSeek-Key`（在页面主世界里
     包 `XMLHttpRequest.prototype.setRequestHeader` 实测出来的，不是读代码推的）；
     ③ 匹配卡的「已具备」与角标在同一屏、同一集合。
     同一次实测抓出第二个界面缺陷：`score=null` 那一屏把「岗位没填技能要求」**说了两遍**
     （新加的 `matchUncomputableLine` 与本来就在的 `.empty`——这两行是浏览器里读出来的）；
     档案为空那一格还会再叠上 `.match-source` 的「你还没有保存的简历…」，一共三行同义反复
     （这一格没在浏览器里造，是把文案放回原样后由 `JobDetailView.spec.ts` 的计数断言跑红的）。
     根因是我按裁定句配了文案，却没验这一格的可达性——见 §5.1 末的枚举。
     文案已撤，回归锁改成**数出现次数**而不是判存在（`occurrences()`）：
     把那句文案放回去，两条用例立刻红（已实测，失败信息原文是
     `expected '…这个岗位没有填写技能要求，暂时没有可比对的要求条目。 这个岗位还没有填写技能要求…' not to contain '暂时没有可比对的要求条目'`）。
     实测还顺手量出 §5.1 末那条未决问题在真实账号上的样子：无技能要求的岗位显示 **100%**。
     **出口标准：加入**（老板给的【加入/不加入】选项，此处记我的选法与理由）——
     「同屏一致性 100%」写进 §10 第 8 步的出口条件，具体由三样东西保证：
     判据 (a)(b)(c) 的集成测试（CI 每次跑）、`JobDetailView.spec.ts` 的角标用例、
     以及每次改动匹配链路后必须重做一次的**真浏览器同屏核对**（本轮的做法：同一屏读出角标集合
     与匹配卡「已具备」集合，两者相同才算过）。
     选「加入」而不是「只留测试」的原因是这一条测的是**两个渲染路径**，
     单测各测一半看不出撞车（本轮那个三行同义反复就是这么漏掉的）。

4. ~~`self_rating` 删除（§4，含迁移与降级测试 + 前后端 + API.md）~~ → **已执行**，见 §4.1。
5. ~~`target_role` 新列（§5，含迁移与档案接口/表单）~~ → **已执行**，见 §5.2。
6. ~~架构复核（§13）与其更正值落文档~~ → **已执行**（本 PR，先于实现合入）。
7. **评分与匹配服务 + `POST /jobs/{job_id}/match`**（§6–§8）。这一条内部有四个必须一起做的部分，缺一个就是假交付：
   - **7a 纯打分层** `app/services/match_scoring.py`：权重常量与版本、五项分项、窗口化片段与 `fragments_truncated`、
     §8 的**两个分区**闭合核验（**不碰 DB、不碰 FastAPI**，契约已强制，单测全覆盖）。
     → **已实现**（`c4a4531`，`tests/test_match_scoring.py`）。实现时否证了两处设计：§7.3 的指标恒 0、
     §8 原来的闭合式单位混了，均见 §13.1。
   - **7b 适配层** `app/integrations/evidence_scorer.py`：`EvidenceScorer` 协议 + `DeepSeekEvidenceScorer`，
     与 `skill_extractor.py` 同一族异常（`SkillExtractorError` 的三个子类那样），测试用 fake，且 fake 要能**分别**造出 §6.5 的五种退化。
     → **已实现**（`c900ff2`，`tests/test_evidence_scorer.py`）。多了一个 `EvidenceScoreTimeoutError`
     才能让 `upstream` 与 `timeout` 分得开，见 §6.5 那条实现偏离说明。
   - **7c 编排层** `app/services/match_service.py` + **一个** `get_match_service(request)`：同一个 `session_scope()` 里装
     `JobRepository` + `StudentProfileRepository` + scorer。🚩 复核定的结构约束：**不许**复用 `get_job_service()` + `get_profile_service()`
     拼两个 session（`app/services/dependencies.py:64-83` 的先例是一 service 一 session；两个 session 等于同一次请求占两条连接，
     且两侧读到的是两个快照）。
     → **已实现**。这条约束的测试第一版用 `inspect.getsource` 数字符串，被自己的 docstring 骗过（docstring 里就写着那两个函数名），
     已改成行为断言：`service._jobs._session is service._profiles._session`。
   - **7d 路由与闸门**：`POST` 路由 + `@limiter.limit(MATCH_RATE_LIMIT, key_func=<按用户>)`（§3）、404-not-403、
     `main.py` 处理器作用域从「两个端点」改「三个端点」的注释与测试、`client.ts` 白名单第 3 条 + §6.1 的三条断言（含路由表闭合）。
     → **已实现**（`451aae4`）：路由、按用户限流（含「同一 IP 两个用户互不挤占」的行为用例，它同时证明了鉴权依赖先于限流装饰器执行）、
     404-not-403、`main.py` 注释（按 §6.5 的实现偏离写成「仍两个端点 + 匹配显式不经过」）、后端的路由表闭合测试、
     `client.ts` 白名单第 3 条（`POST ^/jobs/[^/]+/match$`，绑方法——全仓库第一条模式判据，所以配套写了**不扩张**用例：
     `/jobs/a/b/match`、`/jobs/match`、`/jobs/1/match/extra`、`/resumes/polish/match`、GET `/jobs/1/match` 都不许注入）
     与前端的注入矩阵（`frontend/src/api/__tests__/byok.spec.ts`：正例 / 不扩张 / 尾斜杠与查询串归一化 / `matchJob()` 走白名单而不是手挂头）都已完成。
     那条白名单的自我辩护从「命中即转发」换成了路由表论证，理由见 §6.1。
8. 前端匹配卡（三档展示 + `evidence_mode` 与 `degrade_reason` 分句可见 + **必须显式点击才请求**，
   不许在岗位详情页 `onMounted` 里自动调用：那是拿用户的 Key 换页面浏览量）。
   🚩 2026-11-07 人类负责人裁定「不用向客户说明技术上的设计」之后，这一条**去掉**了「`weight_sum_used` 显示」：
   权重构成与计数只留在 API 响应里，界面只给分数、三档清单、以及用户能自己修的那两句（作用域见 §7.5）。
   375px 与 1280px 的实测仍然必须做（AGENTS.md §3.1），这一条不受上面裁定影响。
   → **已实现**（`frontend/src/views/JobDetailView.vue` 的匹配卡 + `stores/job.ts` 的
   `runMatch` / `matchDegradeLines` / `matchFailureText`，用例在 `JobDetailView.spec.ts` 与 `job.spec.ts`）：
   - 三档清单、分数、用的是哪一份档案、`evidence_mode` + `degrade_reason` 两句说明都上屏；
     权重、`weight_sum_used`、各计数与 `not_measurable_v1` 这类 note **不上屏**，
     并由一条「渲染文本里不许出现 `0.65` / `0.45` / `权重` / `relevance`」的反向断言钉住（裁定被扩大解释时它会红）。
   - **不自动调用**：`mount` 后断言 `POST /jobs/{id}/match` 请求数为 0；假适配器的兜底分支刻意返回一次
     **看得见的失败**而不是 throw——将来谁在 `onMounted` 里加自动调用，测试会指出是哪一次请求，
     而不是抛一个未捕获拒绝让断言落空。
   - 五种退化的界面断言用「五段渲染文本**两两不同**」而不是「各自包含某个词」，
     后者会因为实现把五句拼成一条常量大串而假绿。
   - 实测（方法见 `docs/DEVELOPMENT.md` §10.2）：1280×900 与 375×780 下 `scrollWidth` 1265 / 375，
     均无横向溢出；卡片内元素无一超出视口右边界；点击前 0 次匹配请求、点击后 1 次。
   - **出口标准（2026-11-08 加入，老板在 3c 裁定里给的【加入/不加入】选项）**：
     岗位详情页**同一屏**上，「你已具备」角标的技能集合必须与匹配卡 `covered ∪ partial` 的集合
     逐字相同，出现率 100%。三样东西一起守：判据 (a)(b)(c) 的集成测试（CI 每次跑）、
     `JobDetailView.spec.ts` 的角标用例、以及每次改匹配链路后重做一次的**真浏览器同屏核对**。
     选「加入」而不是「只写测试」：这一条测的是两个渲染路径，单测各测一半看不出同屏撞车
     （3c 那一版把「岗位没填技能要求」在同一屏说了两遍，就是单测全绿、浏览器一打开才看见）。
   - 🚩 **实现时否证了「卡片放最后」这个默认排法**：第一版按「岗位是什么 → 要什么 → 我差什么」的读序
     把卡放在技能表之后，实测按钮距顶 1776px（1280）/ 2218px（375），也就是要翻完整份 JD 加整张技能表
     才够得着——一个必须点击才发真实计费请求的功能埋在那儿等于没有。改到 JD **之前**，
     两个视口分别 497px / 694px，都在首屏内。这条不是审美调整，是量出来的。
   - ~~**本轮没有验证的**：`ai_scored` 那一支的真实 DeepSeek 往返~~ → **已由人类负责人在 2026-11-08 浏览器实测补齐**：
     他自己的标签页配了 Key、点了一次「算一次匹配」，退化框整个不出现（`evidence_mode=ai_scored` 的界面判据），
     分数 26% 也与 AI 强度自洽（同一条 MySQL 经历在关键词模式下该给 `min(hits,3)/3 = 1.0`，算出来约 33%）。
     同一屏还暴露了两个真实缺陷，分别是下面第 11 步的两种情形：`Python3` 不被认成 `Python`（写法判据）、
     以及命中但零片段时那句半截退化说明（§6.5 的第三种）。
9. 第 3c 条（角标消费后端确定性端点）与第 7 条同 PR 或紧随其后，理由见 §10 末。
10. `docs/API.md` 新章节与 §1.1 错误码可达性更新。
    → **已实现**（本次）：新增 §4.10 描述真实契约、§4.11 承接原「未接通」清单；§1.1 补一句
    「`POST /jobs/{job_id}/match` 不在三个 `AI_*` 码的可达集合里」；§4.0 的 OpenAPI 实测从 10 个操作改成 11 个并补一行 429；
    权限矩阵补第 10 行；§4.11 与 §5 里「人岗匹配整条仍在冻结清单」那句按实现更正。
    新增路由让 `app/api/v1/jobs.py` 等五个文件之后的行号全部漂移，所以两份文档里指向它们的行号引用**逐条重新推导**过
    （用 master→HEAD 的逐行对齐映射机械平移，再对手写的那两条 `is_expired` 引用按语义改写），
    最后跑一遍机器检查：`docs/` 与 `AGENTS.md` 里指向代码的 `路径:行号` 引用全部在文件范围内且首末行非空。
    > **2026-11-08 更正：第 10 步那句「全部…指向非空行」当时并不成立，而且我复查过一次才发现。**
    > 把判据从「区间里至少有一行非空」换成「**首行与末行都必须非空**」，再加一条 AST 判据（区间末尾不得溢到
    > **下一个同级函数**），当场查出 12 处首/末行落在空行、7 处末尾溢出到邻近函数，集中在
    > `job_service.py` / `job_repository.py` / `skill_extractor.py`。根因是机械平移的性质：difflib 只保证
    > 「原来那一行还在不在」，不保证「这一行还是被引的那件事」。逐条按代码语义重新落点之后，
    > **检查已做成一条脚本并进 CI**（`scripts/check_docs_citations.py`，纯文本扫描；没做成 pytest 是因为 `tests/conftest.py` 的 autouse 夹具会为一次文本检查去连 MySQL），不再靠我在交付说明里声明。
    > 另有 4 处是**历史引用**，代码状态本身已不存在、行号无处可指，改成只留文件与符号并注明时点：
    > ADR-012 的 `aiError()`（已删）、本 ADR §1.2 的 `self_rating`（§10 第 4 步删）、
    > §1.3 表里的 `skill_name_key`（已搬到 `app/core/skill_names.py`）、§1.3 正文的学生侧裸相等（第 3 步修）。
    > 还有一处口径错误顺手改掉：§1.3 正文说岗位侧 strip 在 `app/schemas/job.py:127-130`，
    > 那个位置现在是 `dedup_key`，`normalize_name` 在 `:99-110`。
11. **写法判据与零候选文案**（2026-11-08 老板三条裁定：并进 PR #11 / 技能补全只抽取不推断 / 非计算机别名暂缓）。
    → **已实现**：`app/core/skill_names.py::skill_forms_match`（四条规则，理由与被推翻的旧结论见 §1.3）+
    `matched_required_skills` 与 `locate_snippets` **两处同修**（只修一处会落到「已具备但零片段」，
    这是实测出来的）+ 前端 `matchDegradeLines` 三分句（§6.5 的第三种）。
    顺带修掉两个旧的假阳性：定位现在带词边界，`Java` 不再命中 `JavaScript`、`C` 不再命中 `C++`
    ——旧实现是纯子串搜，这两条一直在给**假证据**。
    测试按老板「不要花大量时间在不必要的测试上」收紧：一张参数化判据表（19 行，每行双向测 + 覆盖判定同测）
    取代逐个用例，端到端只加 1 条；反向探针跑过——把判据退回裸键全等，那条端到端立刻
    `assert [] == ['Python']` 红，不是空检。

第 3 条必须在第 7 条之前：否则匹配与角标各用各的键，§1.3 那屏自相矛盾会成为产品事实。

---

## 11. 后果

- 简历生成路径的技能条目失去「熟练度」概念（本来就没有，只是这次把假字段拿掉）。
- 匹配度是一个**代理指标**，不是录用概率。UI 必须这么写。分数只用于「差在哪」的解释，不用于排序，也不用于任何对外承诺（PRD `:218` 的「只做辅助排序」在当前范围里进一步收紧为「只做解释」）。
- BYOK 白名单从 2 条 URL 变 3 条，且第 3 条是模式匹配而非整段相等——安全注释必须同步重写，不能只加一行代码。
  🚩 复核更正：`client.ts` 现在只有 `includes(path)` 这一种判据，所以这不是「往列表里加一项」，而是**引入第二种判据**。
  重写注释时要同时改掉那句「宁可写死两条精确路径」——它今天仍然正确，但从第 3 条起不再充分，留着会误导下一个改这里的人。
- 限流第一次出现**两种键**（IP 与用户）共存。`app/core/rate_limit.py` 的模块注释现在通篇按「键 = IP」写，
  加第二条端点时必须把「为什么这一条例外」写进同一个文件，否则下一次「统一成一种键」的重构会把它悄悄改回按 IP，
  而这个退化在测试里是不可见的（测试只测端点自己那条规则能不能触发）。
- 破坏性变更到今天为止是四类：`SkillInput` 少字段（§4.1）、`POST /jobs/{id}/match` 新端点（§3）、
  **`data.score` 由 `float` 变 `float | null`**（§5.1 F1:A，2026-11-08）、
  `GET /jobs/{job_id}/requirement-match` 新端点（§10 第 3c 步）。无外部客户端，前端同 PR 改完即可。
  `meta` 侧是**新增字段**（`matcher_version` / `note`）而不是改语义，所以对已写好的消费方是兼容的。
- **判据版本号（2026-11-08）**：写法判据一改，同一个人同一岗位的判定结果就会变，而响应里原本没有任何东西
  能说明「这次用的是哪版」。所以 `SKILL_MATCHER_VERSION` 从两条端点的 `meta.matcher_version` 同时对外，
  一致性断言 (c) 拿它做锁。L1 别名词表（阶段 2）落地时另加 `meta.alias_version`——两者是不同的东西：
  一个是判据规则的版本，一个是词表内容的版本，词表会随真实用户反馈频繁增删而规则不变。
- 成本与延迟：一次匹配 = 最多一次 DeepSeek 调用（候选对已经过第 1 层裁剪），30 秒预算内；用户看不到「正在打分」之外的等待理由，所以按钮要有 loading 态与失败重试，沿用 C2 的提取交互。
- **分数会随写法判据漂移（2026-11-08）**：`skill_forms_match` 让 `Python3` 顶住 `Python` 之后，
  同一个人对同一个岗位的分数**只会往上走、不会往下走**（新增的是认写法，不是松判据）。
  因此 2026-11-08 之前记录下来的匹配分数**不可与之后的直接比较**——本轮没有落库任何分数，
  所以这个断层只影响我们自己翻旧的实测记录，不影响用户屏幕上的任何东西。
  L1 别名词表落地时必须带 `meta.alias_version`，否则下一次漂移又会没有坐标可对账。

## 12. 替代方案

- **不算总分，只列覆盖/缺口清单**：更诚实，也更简单，且已能满足 PRD `:152` 的解释卡。不采用，因为老板指令 6.2/6.3 明确要「能计算」，说明他要的是分数。
- **匹配落库 + 定时重算**：需要新表 + 后台任务（Redis/ARQ/Celery 全在冻结清单）。不采用。
- **AI 直接判整体匹配度**（把 JD 和简历全文丢给模型出 0..1）：省掉五项设计，但分数不可解释、不可测、且违反 PRD `:152` 的可展开理由要求；同时把完整个人内容交给第三方模型，是 §6 第 4 点专门避免的。不采用。
- **中文分词库做语义相关度**：新增运行时依赖，收益是 §7.3 那个代理指标更漂亮一点。不采用，先把代理指标的诚实标签立住。
- **保留 `self_rating` 并把它变成 AI 打分结果**：让一个 UI 不再采集的字段悄悄换语义，是对数据历史的伪造。不采用。

---

## 13. 架构复核（2026-11-07，实现之前）

方式：调用 `architect-review` 技能，把 §3/§6/§7/§8 的设计逐条**对着代码核**，不接受设计稿自己的说法。
下面是复核发现的问题、取证方式与处置。结论一句话：**结构方向不需要改**（纯打分层 + 「AI 只判强弱不判有无」的分层是对的），
但设计稿里有两处**如果照原样实现就会做错**的表述，和两处我原来写得不成立的话。

| # | 发现 | 取证（实测，不是推断） | 处置 |
|---|---|---|---|
| 1 | 限流口径与仓库机制不符，按 IP 会让局域网测试互相抢额度 | `app/core/rate_limit.py:72` `key_func=get_remote_address`；`:37` 自记多 worker 局限；`Limiter.limit` 签名实测支持 `key_func=` 覆盖 | §3 改为按用户键 + 新增 `MATCH_RATE_LIMIT`，另三条规则不动 |
| 2 | 「AI 失败退化成 200」与三个全局 AI 处理器冲突，实现时最自然的写法会把它变成红字报错 | `app/main.py:259/266/277` 分别 400/401/502；异常类 `ResumeApiKeyMissingError` / `ResumeCopyRejectedError` / `ResumeCopyUnavailableError`（`app/services/resume_service.py:43/47/51`） | §3 新增「状态码边界」；§6.5 新增 `degrade_reason` 五值 + 五情形各一测试 |
| 3 | 交给模型的输入没有上界，而它由用户自由文本决定 | 技能 ≤8 / 经历 ≤3（`app/schemas/profile.py:271-272`），但 `description`/`responsibility` 无长度约束（`app/schemas/resume.py:67-68`，列型 `Text`）；dev 库 8 条经历 `description` p50 44 / p90 102 / **max 615** | §6 第 2 点改窗口化（±80 字）+ `fragments_truncated`；不给 `ExperienceInput` 加 `max_length`（那是截断用户数据，另一个决定） |
| 4 | §7.5 与 §5.1 的「未知怎么办」自相矛盾，常数 0.5 进分母会把总分往中间挤 | `status='published'` 6 行、`deadline is null` 2 行（其中 1 行是回收站异常行）→ 真实可点 5 个里 1 个走这一支 | §7.5 改为 `computed=False`，键名 `freshness` → `remaining_time`，UI 改名「剩余投递时间」 |
| 5 | §6 的白名单自证不成立：正则也命中**不存在**的路径，那些请求不转发 DeepSeek | `client.ts:224` 现在是 `BYOK_KEY_PATHS.includes(path)` 整段相等，模式机制今天不存在；`:229` 只做查询串与尾斜杠归一化 | §6.1 换成路由表论证 + 三条断言（正向 / 不扩张 / 已注册路由闭合），并进 `byok.spec.ts` 现有注入矩阵 |
| 6 | §8 故意不做计数闭合，等于把一条漏计写进设计 | 少一个「模型没回这条」的通道，等式必然不闭合 | 新增 `dropped_missing_output` + 四桶互斥次序 + **要求写成断言**；同时更正我引用的 ADR-011 「先例」其实是已知缺陷 |
| 7 | 「请求体为空 ⇒ 用哪份档案」是隐式选择，用户会以为分数算错 | `student_profiles.is_default`（`app/repositories/student_profile_repository.py:76`）；岗位页与编辑页无共享档案上下文 | §3 响应新增 `profile_id` / `profile_title` / `job_id` 回显 |
| 8 | 编排层若复用两个既有 DI 会开两个 session、两个快照 | `app/services/dependencies.py:64-83`：每个 `get_*_service` 各自 `session_scope()` | §10 第 7c 条：一个新 DI、一个 session、两个仓储 |

**复核没有改的东西**（写下来是为了让「改了什么」有边界）：五项权重数值与 PRD 出处、`score` 只做解释不做排序、
分数不落库、v1 只支持默认档案、404-not-403、`deepseek-flash` + BYOK + 非流式 + `max_retries=0`、模型不被问「匹不匹配」。
这些经复核仍然成立，没有一条是被我为了显得认真而动的。

**复核发现但不属于本切片的**：润色端点整段发送 `description`/`responsibility`、输入侧无上界
（`app/integrations/copy_writer.py:171-172`）。这是 ADR-010 的既有暴露面，本轮只记录、不修改——
把它顺手改掉会让「一条切片一条分支」变成一屏无法审的 diff。

### 13.1 上面这张表没有覆盖到的三条（实现 7a 时才发现）

复核只能读设计，读不出「这个指标在真实数据上有没有值」。写纯打分层之前先测了它们，结果推翻三条：

| # | 被否证的说法 | 实测 | 处置 |
|---|---|---|---|
| 9 | §7.3 的经验相关度是个「诚实标签下的弱代理」 | 5 个已发布岗位 × 8 条经历，token 重合 **0/40** | v1 该项恒 `computed=False`，见 §7.3.1 |
| 10 | §13 复核第 6 条给出的「四个桶相加 = `candidate_pairs`」 | 模型多编一条编号，等式两边同时各多 1，测试跑红 | 改成候选侧/条目侧两个分区，各断言一次，见 §8 |
| 11 | 「越界的 `strength` 夹到 [0,1]」这一句隐含「能拿到数值」 | `True` 在 Python 里既是 `int` 又能当 `1` 用，还顺手命中编号 `1` | 两处显式挡 `bool`，并新增 `dropped_unusable_strength` |

第 10、11 条的共性值得单独留一句：**复核也是靠读文字做的，所以它只能发现「说法不自相矛盾」级别的问题**。
计数是否真能闭合、代理指标在真实数据上是否恒为 0，这两类只有把代码跑起来才知道——
所以本 ADR 要求实现前先测数据（§7.3.1 的 0/40 就是这么来的），而不是把复核当成终点。

### 13.2 第 7 步实现期间的合入事实（结论已定位，细节与取证在 `DEVELOPMENT.md` §10.0）

- PR #9 / #10 在两条 `test` 检查都 `success`、`required_approving_review_count: 0` 的前提下持续
  `mergeable_state: blocked`，页面文案是 **「Merging is blocked — Cannot update this protected ref.」**，
  管理员侧只剩「Bypass rules and merge」可点。
- 凶手是我建的 ruleset 里那条 **`update` 规则**：它挡的是对 master ref 的一切更新，而 PR 合并本身就是一次
  ref 更新——**闸门把自己的合入路径也锁死了**。删掉该规则后 `mergeable_state` 立刻 `clean`（同一 ruleset 上
  两次 PATCH + 一次删除的实测，过程记在 DEVELOPMENT.md §10.0）。
- 对本文的影响：§10 第 7 步要开 3–4 个 PR，如果这条没定位，每一步都会撞上「只能绕过」，
  而「绕过」用第三次之后就不再是例外，是习惯。所以这条属于流程阻塞项，不是记录性细节。
- 我这边仍有一道外部约束：协作策略不允许我合自己写的 PR（`gh pr merge` 与 merge API 自合并都会被拦），
  所以 PR #9 之后所有合入都需要人类负责人点一次。这一点不改动任何设计结论。
