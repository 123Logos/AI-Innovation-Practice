# ADR-016：技能别名层与 `skill_name_key` 的关系（B1 的契约）

- 日期：2026-11-09
- 状态：**已生效并已由批次 B1 实现**（2026-11-10；验收对账见 §5，第一批候选别名见 §7——**尚未入库，等老板逐条批**）
- 决策人：**本 ADR 由我（编码 agent）拍板**。依据是老板 2026-11-09 第 3 点原话：**「ADR-A / ADR-B 我给你全部决策权与操作权，按你觉得最优的方法实现」**。他此前只点名拍了 ADR-C（→ `ADR-015`）。凡与他既有裁定冲突的地方，一律以他的为准，本 ADR 第 6 节把那几处冲突逐条列出来，没有一处是我替他解围。
- 关联：ADR-013 §7.4（`preference` 三档与「同方向不同词留给阶段 2」）、ADR-011 2.F（JD 技能提取）、ADR-015（档案多值，别名不参与）、`docs/adr/ADR-014`（全面解冻，本 ADR 不再需要批准信）

## 1. 背景与取证（选项的代价是量出来的，不是推演）

判重/身份用的键是 `app/core/skill_names.py:154-173` 的 `skill_name_key = NFKC + casefold`（**不 strip、不动空白、不动标点**）。它的调用点共 5 处写入/合并语义 + 4 处匹配语义：

| 用在哪 | 出处 | 换成别的键会怎样 |
|---|---|---|
| 岗位技能判重 | `app/schemas/job.py:134 → :190`（422 `skill names must be unique`） | 判重口径与库里 `uq_job_skills_job_name`（`20261105_0012:96`）脱节 |
| 档案技能判重 | `app/schemas/profile.py:314`、`app/services/job_service.py:257` | 同上，且 `deduped_count` 的含义变 |
| coverage 的分子分母域 | `app/services/match_scoring.py:404/437/625` | 同一岗位同一批技能的分母口径变 |
| 真正的命中判定 | **不是键**，是 `skill_forms_match`（`skill_names.py:78-110`，被 `app/services/match_scoring.py::matched_with_forms` 用） | — |
| 偏好档位 | `app/services/match_scoring.py` 的 `preference` 折叠那一格（行号已搬家，不再引）：`skill_name_key(target_role)` vs `skill_name_key(job.title)` 互相包含 + `overlap_ratio` | **完全不查技能表、不进 `skill_forms_match`** |
| 回显 | `app/schemas/match.py::RequirementCoverageRow`（`name` = 岗位侧原始写法逐字回显、`matched_form` = 学生侧原始写法） | 回显语义变 |

还有一组**裸字符串等值**的点，任何别名表都修不到、也不许顺手改：`match_scoring.py`（`matched_names` 用 `item.name` 原值组集合）、:477/485/501-517（AI 必须逐字复述技能名才能对上候选对）、:554/558/562（按原值分组）、:752（命中却无强度那条并集不变式）。

## 2. 决策

### 2.1 别名只在**匹配判定层**生效（三个候选里的 (c)）

- **(a) 别名替代 `skill_name_key` 当身份键 —— 否决**。它把「写法不同」升级成「身份相同」，于是 `Python` 与 `Python3` 在同一个档案/岗位里**存不进两条**（判重放宽后撞 `uq_skills_profile_name`/`uq_job_skills_job_name`，MySQL 1062 → 用户看到 500 而不是 422），并且直接推翻 `tests/test_jobs_constraints.py:442/458` 钉住的既有契约。老板要的是「用户能都写进去、匹配时认得出是一回事」，(a) 恰好把他要的这半件毁掉。
- **(b) key 之后做后处理（判重也走别名）—— 否决**。应用层判重比数据库唯一键严，同一别名对就有两套身份口径；而 `ADR-013` §7.4 里「免加权不许用来逃避负面信号」的同一条洁癖，这里对应的是「判重不许比存储严」。
- **(c) 只在匹配层映射 —— 采用**。改动面收敛在 `skill_forms_match`、`locate_snippets`（`match_scoring.py:327/344`）、`matched_with_forms`（`:440`）、以及 `preference` 那一格。存储、判重、DDL、回显、裸等值那六点**全部不动**。

### 2.2 别名对存在 `skill_name_key` 的输出空间里

新表（列名即契约）：

```
skill_aliases(id PK, canonical_key VARCHAR(200) NOT NULL, alias_key VARCHAR(200) NOT NULL,
              scope VARCHAR(10) NOT NULL, source VARCHAR(20) NOT NULL,
              note VARCHAR(200) NULL, created_at, updated_at)
UNIQUE (canonical_key, alias_key, scope)      -- 单向存一行，判定双向查
```

存的是**归一后的 key**，不是原始写法：别名是「key 之后的追加判据」，不是「key 的替代」。原始写法永远只在 `skills.name` / `job_skills.name` 里，`ADR-013` 的回显契约因此不受影响。

- `scope` 两个值：`skill`（技能写法同义）、`role`（岗位/目标方向同义）。**必须有第二个域**，理由见 §2.4——不写这一格，本 ADR 就只是把「同义」限制在技能表里，而老板点名的「`React 工程师` vs `前端开发`」根本不在技能表里。
- **单向存一行、判定双向查**（`canonical↔alias` 两个方向都算命中）。不这么定就要存两行，而漏一行就是「A 匹配 B、B 不匹配 A」这种无法解释的不对称。
- `source` 枚举：`seed`（人工评审入库）/ `ai_reviewed`（阶段 3 的 AI 建议经人工评审后入库）。**运行时永远不查 AI**：匹配链路保持确定性的既有承诺不因别名表松动。
- **硬约束（写入路径校验 + 一条测试，不是文档祈祷）**：`canonical_key` 与 `alias_key` 的版本尾巴必须一致——用现成的 `skill_stem`/`_VERSION_TAIL` 判，尾巴不同（`python 2` / `python 3`）直接拒绝入库。把两个版本号合成一回事，等于给用户一个他会带去面试的假匹配。
  - **实现时的两处偏离，报备在这里**：① 落点是 `SkillAliasRepository.add()` 这**唯一写入口**，不是「迁移校验」——本批不写任何种子迁移（§3 要求逐条人工过），所以库里没有迁移可校验；将来的种子迁移同样必须走这个入口。② 「一致」取了**严格读法**：两侧尾巴**逐字相同**才放行，于是 `python` / `python3`（一侧有版本号、一侧没有）**也被拒**。这比本 ADR 举的例子更严，代价是「Golang ↔ Go」这类没问题、「Python ↔ Python3」这种本来该由词表认的对子也被挡住。**这一格等老板拍**：维持则文档照此，放宽则改 `alias_version_tail_conflict()` 一处 + 那条测试。
- 表可以空。**空表 = 今天的纯词面行为**，这一点是下面版本策略成立的前提。

### 2.3 技能域命中顺序：词面先跑，词表兜底

`skill_forms_match` 现有四条规则（键相等 / 词干+版本尾巴 / 其余两条，`skill_names.py:100-110`）**全判否之后**才查别名表。顺序是刻意的：便宜的、可解释的、用户看得见的先跑，人工词表只做兜底，命中时它替谁都不留痕。

影响面：coverage 的 covered/partial 集合、badge（`requirement-match`）、证据定位窗口（`locate_snippets` 的复核同样认别名）。并集不变式（`match_scoring.py` 那条）**不许因为别名而破坏**——B1 必须像 `test_badge_union_still_equals_match_union_in_ai_mode` 那样再钉一条带别名行的测试。

### 2.4 角色域：`preference` 增加第四档来源，给 0.6 不给 1.0

新判据顺序（写死）：`contains` 1.0 → `token_overlap` 0.6（`ratio ≥ 0.5`）→ **`alias_match` 0.6（`role` 与 `job.title` 的 key 在 `scope='role'` 里成对）** → `preference_below_threshold` 0.0 → `preference_conflict` 0.0。两格 0.0 的语义与原因码沿用老板 2026-11-09 的裁定（`ADR-013` §7.4 更正段），本 ADR 不重开那一格。

为什么是 0.6 不是 1.0：`contains` 那一档的现值意味着「岗位标题逐字就是用户填的方向」，靠人工同义词表救回来的配对**证据更弱**（词表是我们加的，不是用户写的）。给 1.0 会让「我们替用户认的」与「用户自己写准的」同分，而这两种情况的修正成本完全不同。0.6 与 `token_overlap` 同档，理由一致：都是「方向一致但需要中间层推断」。

代价（明码写出来）：一个只有别名能对上的配对，从 `0.0` 变成 `0.6`，`preference` 权重 0.10 ⇒ 总分最多 +0.06。这是 B1 唯一会改变分数的一格（技能域别名改变 coverage 归属，也会改分数，幅度由岗位技能条数决定），所以 §2.5 的版本轴必须在同一 PR 里落地。

### 2.5 三条版本轴，各管各的上升判据（本 ADR 顺带把这件事一次说清）

| 轴 | 管什么 | 什么时候必须 +1 | 别名层的影响 |
|---|---|---|---|
| `MATCH_WEIGHTS_VERSION`（现 `v2`） | **权重表 + 归一公式** | 权重变了，或分数算法含义变了 | **不升**。别名不动权重也不动公式 |
| `SKILL_MATCHER_VERSION` → `meta.matcher_version`（现 `v1`） | **写法判据**（`skill_forms_match` / `skill_stem` / `_VERSION_TAIL`） | 这三者任一逻辑变化 | **B1 落地时 v1→v2**：判据里多了查表这一步，哪怕表是空的。按 `app/core/skill_names.py::SKILL_MATCHER_VERSION` 上面那段自己写的规则，这就是必须升的情形 |
| `meta.alias_version`（**新增**，起始 `v1`） | **别名表内容** | 任何一行增删改 | 词表是「内容」而不是「代码」，所以给它自己的轴；同一份代码 + 不同词表给出不同判定，只有这格能说明是哪一版 |

分工写明白，是为了避免将来「分数变了，不知道是哪一版造成的」这种无法回答的对账问题。**空表的 `alias_version` 仍是 `v1`**，不因为「还没有数据」而不发这一格。

## 3. 后果

- B1 一个 PR 里要动：新表迁移（upgrade + downgrade）、`skill_forms_match` 增加兜底查询、`_gather` 侧一次性把别名集合载入内存（**不许每条技能各查一次**，那是 N+1）、`preference` 第四档、`meta.alias_version`、`matcher_version` v1→v2、`ADR-013` §11 再记一类破坏性变更、前端 `jobs.ts` 类型与 `stores/job.ts` 消费（角标判定继续走后端，前端不自建别名逻辑——`ADR-013` 判据归一的既定方向）。
- 种子内容：**宁缺毋滥**。第一批只收计算机领域高频同义写法（`JS`/`JavaScript`、`K8s`/`Kubernetes`、`golang`/`Go` 一类），每条都要人工过一遍版本尾巴约束。理由：每一行都会真实改变判定结果，而 `matcher_version` 是唯一能对用户解释「为什么昨天 0% 今天 60%」的东西。
- 别名**不影响**：判重、存储、回显、裸等值那六点、`ADR-015` 的证书/作品/期望城市（匹配今天读不到它们，别名层读不到得更彻底）。
- 「同方向不同词」在角色域修好的前提是**词表里真有成对条目**。B1 落地≠ 这一格不再报 0 分，只是有了能收敛的地方。这句要写进 PRD 对照，不能让「有了别名表」被读成「匹配变准了」。

## 4. 替代方案

- **(a)/(b)**：见 §2.1，都是拿身份换表达，代价实测在表里。
- **只在技能域做别名、角色域推给阶段 3**：省一张表的一半，但老板点名的「同方向不同词」正是角色域，等于把这一格的问题原样留着还宣称修完了。
- **`preference` 给别名 1.0**：分数更高、更「像成功」，代价是用户自己写准的标题和靠词表救回来的配对同分——这是代理指标越权的教科书形状。
- **把别名塞进 `layout_config` 那种 JSON 列**：与 `ADR-015` 否决 JSON 多值的理由同构（成对唯一、按条审计、将来要被读）。

## 5. 实现时的验收（B1 的出口标准，先写在这里）

1. 空表时，四条端点读数与 `v1` 判据逐字相同（`alias_version=v1`、`matcher_version=v2` 是唯一允许的差异）。
2. 加一条 `(python3, python 3.13)` 的别名行后：badge 与匹配卡的 `covered ∪ partial` 仍然**逐字相同**（并集不变式）。
3. 一条 `scope='role'` 的配对把某对 role/title 从 `preference_conflict` 0.0 变成 `alias_match` 0.6，且 `score` 恰好 +0.06。
4. 版本尾巴不同的成对提交被拒绝（校验 + 测试各一条，不许只有文档）。

**B1 交付后的对账（2026-11-10）**——四条各由谁钉住：

| 验收 | 测试 |
|---|---|
| §5.1 空表 = v1 逐字相同 | `tests/test_job_match.py::test_empty_alias_table_reproduces_the_v1_readings`（四条端点读数）+ `tests/test_skill_alias_layer.py::test_an_empty_index_is_the_lexical_behaviour_for_every_pair_we_could_ask` |
| §5.2 并集不变式 | `tests/test_job_match.py::test_a_skill_alias_row_lands_in_the_badge_and_keeps_the_union_invariant` + `tests/test_match_scoring.py::test_an_alias_hit_without_any_evidence_stays_in_the_union` |
| §5.3 role 配对 0.0 → 0.6 且 +0.06 | `tests/test_job_match.py::test_a_role_alias_row_moves_preference_from_conflict_to_alias_match` + `tests/test_match_scoring.py::test_an_alias_row_moves_the_weighted_numerator_by_exactly_0_06` |
| §5.4 版本尾巴不同被拒 | `tests/test_skill_alias_layer.py::test_a_pair_with_mismatched_version_tails_is_refused`（另一条正向：`::test_a_pair_with_consistent_version_tails_is_stored`） |
| §3「不许 N+1」 | `tests/test_job_match.py::test_a_match_request_reads_the_alias_table_exactly_once` + `tests/test_skill_alias_layer.py::test_load_index_issues_exactly_one_statement_regardless_of_row_count` |
| §2.5 两条版本轴同源 | `tests/test_job_match.py::test_both_endpoints_report_the_same_alias_version_too` |

## 6. 与他既有裁定的边界（我没重开的格子）

`ADR-013` §7.4 的分叉表、§5.1 的「核心 term 不在就整个不评估」、两格 0.0 的原因码拆分、BYOK 白名单逐条同意、`ADR-014` 的三道闸——**本 ADR 一条都没动，也没引用它们来给自己开路**。如果实现时发现必须动其中任何一条，停下来问他，不自行扩范围。


## 7. 第一批候选别名（**未入库**，等老板逐条批）

§3 写的是「宁缺毋滥 + 每条都要人工过一遍版本尾巴」。所以 B1 交付的是**机制 + 空表**，
下面这张表是**候选清单**，不是我替你勾好的答案：每一行都会真实改变判定结果，
批准后由一条独立的数据迁移落库（那条迁移必须走 `SkillAliasRepository.add()` 同一段判据，
不许自己拼 SQL 绕过版本尾巴校验）。

| canonical | alias | scope | 版本尾巴自查 | 为什么能进 |
|---|---|---|---|---|
| `JavaScript` | `JS` | `skill` | 两侧都无版本号 → 一致 | 缩写与全写指同一门语言，简历与 JD 里都常见 |
| `TypeScript` | `TS` | `skill` | 同上 | 同上，且 `TS` 不会与别的技能撞名 |
| `Kubernetes` | `K8s` | `skill` | 同上 | 行业通用缩写，词面四条判不出 |
| `Go` | `golang` | `skill` | 同上 | 社区域名写法；`go` 这个词在 JD 里也确实是同一门语言 |
| `React` | `ReactJS` | `skill` | 同上 | 官方与民间两种写法 |
| `Machine Learning` | `ML` | `skill` | 同上 | 缩写；注意 `ML` 在某些语境指 Markup Language，**这条要老板判断是否接受歧义** |
| `SQL` | `结构化查询语言` | `skill` | 同上 | 中文全称与缩写 |
| `前端开发工程师` | `React 工程师` | `role` | 同上（role 域一般无版本号） | 这正是老板点名的「同方向不同词」那一格；**代价**：命中后 `preference` 从 0.0 变 0.6，总分最多 +0.06 |

反例（**故意列出来，好让「为什么被拒」看得见**）：

| canonical | alias | 为什么拒 |
|---|---|---|
| `Python` | `Python3` | 严格读法下两侧尾巴不同（`''` vs `'3'`）→ 拒。放宽与不放宽的分歧记在 §2.2 那条报备里，等老板拍 |
| `Python 2` | `Python 3` | 主版本号不同，合成一回事会给用户一个他会带去面试的假匹配 |
| `开发` | `前端开发` | 上下位关系而不是同义关系；别名表只收「同一个东西的两种写法」，不收「更宽泛的方向」 |

落库时的两条硬规矩（写在这里，免得将来只靠记忆）：
① 每个域各一条 `alias_version` 上升事件——任何一行增删改都要 `SKILL_ALIAS_VERSION` +1，并在
`ADR-013` §11 记一类分数漂移；② 同一批里同时改 `skill` 与 `role` 两个域的逻辑不许混着提交。
