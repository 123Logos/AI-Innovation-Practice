# ADR-015：档案多值数据模型与简历版本指针（批次计划 B4 + B8 的契约）

- 日期：2026-11-09
- 状态：**已生效（三条裁定已定，第 5 节三格待拍）**
- 决策人：人类负责人。2026-11-09 原话（编号照抄他的）：「**C/1** 证书/作品：进简历，`LAYOUT_VERSION` 1→2，但 422 只拦「旧布局生成新简历」，不拦已存在的简历 / **C/2** 多值：子表，不用 JSON 列；孤儿问题用「全量替换 + 软删除」，不物理先删后插 / **C/3** 版本指针：`resumes.current_version_id` 显式外键，不靠 `created_at` 猜」。
- 关联：ADR-006（多简历）、ADR-007 §6.8(1)（排版降级口径）、ADR-013（匹配，本 ADR 不动它）、ADR-014（全面解冻）、`docs/MVP_SCOPE.md` §6（新表需 ADR）

## 1. 背景

FR-02 还差三格：**期望城市 / 证书 / 作品链接**。老板 2026-11-08 复核批次表时把 B4 的规模判成「取决于多值」，并要求**数据模型与 B8（简历版本对比）一起定**，理由是不能「先加单值列后续再迁移」。开工前查清的现状（全部是代码事实，不是推演）：

| 现状 | 出处 | 为什么它决定了本 ADR |
|---|---|---|
| 全仓**没有任何**可放证书 / 作品链接 / 附件的列 | `alembic/versions/` 全链 + `app/`、`frontend/src/` grep `certificate\|portfolio\|url\|attachment` 零命中 | 这不是「补一列」，是开新实体 |
| 技能 / 经历 / 教育每次保存是**物理先删后插**，且新行换全新 UUID | `app/repositories/student_profile_repository.py:486-488` | 任何挂在 `skills.id` 下的子表**每存一次就成孤儿**——这正是 C/2 要解决的问题 |
| 可渲染区块被白名单钉死 3 段 | `app/core/document_style.py:155`（`SectionId`）、`:175-179`（`DEFAULT_SECTIONS`）、`:182`（`SECTION_IDS`）；写侧 `max_length=len(SECTION_IDS)`（`app/schemas/profile.py:125`） | 「进简历」必然要动这张白名单，动它就动版本 |
| `LAYOUT_VERSION = 1`，写侧版本不符即 422 | `app/core/document_style.py:158`、`app/schemas/profile.py:127-132` | 升版会立刻让老客户端存不进配置 |
| 读侧版本不符**静默回落默认顺序**（只 warning） | `app/core/document_style.py:234-237` | 老配置 + 新版本 = 用户精心排的顺序**悄悄没了**，这是 C/1 那半句要拦的东西 |
| `resumes` 只有 `content_html` 快照，**零版本列**（无 revision / parent_id / is_current / deleted_at），「最新」全靠 `created_at` 猜 | `alembic/versions/20261023_0001_initial_phase1.py:105-122`、`app/repositories/resume_repository.py:33-51` | C/3 的对象 |
| 前端另有一份默认区块表 | `frontend/src/stores/resume.ts::DEFAULT_LAYOUT_SECTIONS`，由 `tests/test_profile_contract.py::test_default_layout_matches_frontend` 比对 | 升版要**两处同改**，否则契约测试红 |
| 条数上限：技能 8 / 经历 3 / 教育 5 | `app/schemas/profile.py::ProfileUpsertRequest` | 新子表必须自己带上限，一页 A4 装不下无限个证书 |

## 2. 决策

### 2.1 C/2 多值放**子表**，写路径改成「全量替换 + 软删除」

新表两张（列名即契约，不留占位）：

```
profile_certificates(id PK, student_profile_id FK→student_profiles ON DELETE RESTRICT,
                     name VARCHAR(100) NOT NULL,
                     issuer VARCHAR(100) NULL, issued_on DATE NULL,
                     deleted_at DATETIME(6) NULL, created_at, updated_at)
portfolio_links(id PK, student_profile_id FK→… ON DELETE RESTRICT,
                label VARCHAR(100) NOT NULL, url VARCHAR(500) NOT NULL,
                deleted_at DATETIME(6) NULL, created_at, updated_at)
```

外键方向沿用仓库既有口径：**一律 `RESTRICT`，删父行靠应用层按 `_CHILD_TABLES` 显式清子行**（`20261023_0001` 里每条 FK 都是 RESTRICT，`student_profile_repository.py:628-643` 是那个显式清理）。**不引入 `CASCADE`**——那是第二条删除路径，而本项目刚刚才因为「同一个概念两处实现」付出过代价。

上限：证书 5 / 作品链接 5（与 `educations` 同档；理由只有一条——一页 A4）。**不用 JSON 列**：JSON 里塞数组等于把「同名证书去重」「按条软删除」「将来被匹配读」这三件事降级成字符串游戏，而本仓库已经付过一次这种学费（`layout_config` 只能存顺序不能存内容，就是当年为了避免这种情况）。

**全量替换 + 软删除**的确切含义（写路径只留这一套口径）：`PUT` 时把当前**活跃行**（`deleted_at IS NULL`）全部盖上 `deleted_at = now`，再插入本次提交的行；**不再执行 `DELETE`**。同一套改法**适用于现有三张子表**（skills / experiences / educations）——老板 2026-11-09 拍的第 ① 格：「要」，理由与我提的一致：只让新表软删、老表继续物理删，同一个档案就有两套写路径口径。

**这条裁定有一句必须同时生效的推论，否则它是破坏性的**：软删除一旦落在子表上，**所有读侧都要加 `deleted_at IS NULL`**，而这件事**只准写在仓储层那三个方法里**（`app/repositories/student_profile_repository.py::list_skills` / `::list_experiences` / `::list_educations`，新三张子表的读侧同理），调用方**一律不许自己再比一次**。今天这三个方法的调用点在 `app/services/profile_service.py`、`app/services/resume_service.py`、`app/services/match_service.py::_gather`，漏掉任何一处就是：档案页干净、生成的简历里多一条已删的技能、匹配的分母也被那条已删技能污染——而这三屏读数互不相同，正是本项目最贵的那种 bug。同理，**条数上限现在只数活跃行**（`skills:8 / experiences:3 / educations:5`，`app/schemas/profile.py::ProfileUpsertRequest`），否则用户存几次之后就永久加不进东西。`duplicate_profile` 也只复制活跃行。


唯一键必须跟着改：`uq_skills_profile_name(student_profile_id, name)`（`20261023_0001:76`）这类约束在软删除下会**误伤**——用户删掉「Python」再加回「Python」，历史行还在，直接撞 MySQL 1062 变 500。机制：每张表加一个生成列 `active_marker AS (IF(deleted_at IS NULL, 1, NULL)) STORED`，唯一键改成 `(student_profile_id, name, active_marker)`。MySQL 的唯一索引**允许多个 NULL**，所以历史行自由重复，而活跃行仍逐字唯一。新表同理（证书按 `(profile_id, name, active_marker)`，链接按 `(profile_id, url, active_marker)`）。

### 2.2 C/1 证书与作品**进简历**：`LAYOUT_VERSION` 1 → 2，422 的位置精确到「生成」

`SECTION_IDS` 从 3 段变 5 段：`educations / skills / experiences / certificates / portfolio`，默认标题「教育经历 / 技能 / 项目经历 / 证书 / 作品链接」，默认顺序即此顺序；`DEFAULT_SECTIONS`、前端 `DEFAULT_LAYOUT_SECTIONS`、`resolve_layout()` 的兜底同步改，`tests/test_profile_contract.py::test_default_layout_matches_frontend` 继续负责比对两份默认表。

三侧的行为**刻意不对称**，这就是他那句「422 只拦『旧布局生成新简历』，不拦已存在的简历」的展开：

| 面 | 遇到 `version != 2` 的存储配置 | 为什么 |
|---|---|---|
| **读**（`GET /profiles/...` 的 `layout_config`） | 照旧**静默回落默认顺序** + warning（`document_style.py:234-237` 的既有原则） | 打开档案不是写操作，不该因为客户端旧就看不见东西 |
| **写**（`PUT` 带 `version`） | **422**（`schemas/profile.py:127-132` 既有行为，只是认的值变成 2） | 写侧严格一直是这个项目的口径 |
| **生成简历**（`POST /resumes/generate`） | **新增：422**，文案必须是修得动的一句（「你的排版设置是旧版本，请在排版面板重新保存一次再生成」） | 静默回落会把用户排好的顺序换成默认顺序，而他**看不出来**——这比报错更糟。升版的那一次一定会发生，所以要发生在一句话能说清的地方 |
| **已存在的简历** | **永不受影响**：`resumes.content_html` 是不可变快照，不重渲染、不改写、不迁移 | 他明确点名的「不拦已存在的简历」 |

一句话概括这条不对称：**版本失配只在「要产出新数据」时拦人，产出之后永不回头。**

### 2.3 C/3 版本指针是**显式外键**，禁止按 `created_at` 猜

`resumes` 增加：`version_no INT NOT NULL DEFAULT 1`、`current_version_id VARCHAR(36) NULL`（FK → `resumes.id`，`ON DELETE SET NULL`）。**这一条是本仓库唯一一个非 `RESTRICT` 的外键，偏离 §2.1 的口径是刻意的**：链头行本身可能被删（用户永久删除一份旧简历），RESTRICT 会变成「有指针就删不掉行」的死锁；SET NULL 让其余行的指针落空，而**指针落空时读侧必须显式报错，不许回落到「挑一条看起来最新的」**——回落就是把这条 ADR 作废。语义写死：一个档案的所有简历行，`current_version_id` 都指向同一条，且**该行自己的 `current_version_id` 必须等于自己的 `id`**（自指即链头）。

「每个档案至多一条链头」用同一个 MySQL 机制守：生成列 `is_head AS (IF(id = current_version_id, 1, NULL)) STORED` + `UNIQUE(student_profile_id, is_head)`。不这么做就会开出「两行都自称当前版」的双口径——本项目最不想要的那种病。

写入方（`create_resume`）必须在**同一事务**里插新行 + 把旧链头的 `current_version_id` 指向新行 + 新行自指；`GET` 侧「当前简历」只准读这个指针。**「取 `created_at` 最大的一条」这个写法从此不许出现在任何地方**——顺带把这条现状写进契约里，因为它比看起来重要：`ResumeRepository` **今天只有 `create_resume` 一个方法**（`app/repositories/resume_repository.py:33`，全类 `:30-51`），全仓 `app/` 与 `tests/` 里 `select(Resume)` **零命中**。也就是说 `resumes` 是一张**只写不读**的表：每次生成都落库，但没有任何端点把它读回来。所以 C/3 不只是「加一列指针」，它是 B8 第一次给这张表建读侧，**指针必须在那条读路径出现之前先定死**，否则第一个读实现就会顺手写成 `ORDER BY created_at DESC LIMIT 1`，从此变成隐性契约。

## 3. 后果

- 一次迁移动 5 张表（2 张新表 + 3 张老子表加 `deleted_at`/生成列/唯一键 + `resumes` 加 3 列），必须附 upgrade 与 downgrade，并跑「空库 → head → base → head」那条既有测试。
- 老客户端**能读能看，不能存不能生成**（读回落默认 / 写与生成 422），这是刻意的：宁可给一句修得动的报错，也不静默改人家的排版顺序。
- 软删除让子表行数随保存次数增长，**「永久删除档案」的路径必须物理清干净**：现有实现按 `_CHILD_TABLES` 列表清子行（`app/repositories/student_profile_repository.py::_CHILD_TABLES` = `("resumes", "educations", "experiences", "skills")`，`::purge_expired` 是执行处），**新表三张必须一起加进这个元组**（`profile_certificates` / `portfolio_links` / `profile_city_preferences`，见 §5 第 ③ 格），漏一张就是「档案删了、证书还活着」。好在该删除是物理 `DELETE ... WHERE student_profile_id IN`，不区分 `deleted_at`，所以软删除积累的历史行会一并清掉，不需要额外条件。
- 一页 A4 的约束现在要兜 5 个区块，证书/作品很容易把版面撑爆；B4 实现时必须实测 375px 与 1280px 两档，并把「超长时截断还是缩字号」当一条**可见文案**决定，不是 CSS 巧合。
- 匹配（ADR-013）**暂时读不到**新表：`MatchFacts` 只装 `student_skills / experiences / target_role / deadline`（`app/services/match_service.py::_gather`），教育都从来不进。证书/作品要不要进匹配是**另一个 ADR**，不在本 ADR 里顺手定。

## 4. 替代方案（都否了，理由留档）

- **JSON 列存多值**：省一张表和一次迁移，代价是「按条软删除」「按条唯一」「将来按条被读」三件事全变成应用层字符串比较；且 `layout_config` 的先例已经证明，一旦内容进 JSON，读侧与写侧的校验就要各写一份。
- **继续物理删 + 只给新表加子表**：孤儿问题从 `skills.id` 挪到新表的 `student_profile_id`，只解决一半；而且同一档案出现两套写路径，正好是老板警告的「把阶段 1 刚收口的口径差重新打开」。
- **不给 `resumes` 加指针，改用「`updated_at` 最大即当前」**：这就是今天的实际行为（没有任何列说明哪条是当前版），B8 的「版本对比」要的是稳定身份，靠时间排序既不稳也不可用。
- **`is_current BOOLEAN` 而不用链头自指**：`UNIQUE(profile_id, is_current)` 在 MySQL 里能保证至多一条，但要多维护一个「旧的置 false」的步骤，而指针方案顺带给出了版本序列（B8 的对比要的就是这个）。

## 5. 2026-11-09 同日拍完的三格（原为「待拍」，此处按裁定改为定稿，不留悬项）

老板第二轮回复原文：「2.1 要 / 2.2 两节 / 2.3 多值」。

| 格 | 裁定 | 落到契约的结果 |
|---|---|---|
| ① 现有三张子表一起转「全量替换 + 软删除」 | **要** | 见 §2.1 那段推论：`active_marker` 唯一键要一起改到 `uq_skills_profile_name`（`20261023_0001:76`）与 experiences/educations 的对应约束上；**读侧过滤只写在 `list_skills/list_experiences/list_educations` 三个仓储方法里**（`student_profile_repository.py:302/310/318`），6 处调用点一律不自比；条数上限只数活跃行；`duplicate_profile` 只复制活跃行 |
| ② 证书与作品算几节 | **两节** | `SECTION_IDS` = `educations / skills / experiences / certificates / portfolio` 共 5 段（§2.2 的默认顺序与标题照此，不需再改），两节各自可排序、可少于上限、独立截断 |
| ③ 期望城市单值还是多值 | **多值** | 开**第三张子表**：`profile_city_preferences(id, student_profile_id FK RESTRICT, city VARCHAR(50) NOT NULL, deleted_at, created_at, updated_at)` + `active_marker` 唯一键 `(student_profile_id, city, active_marker)`，上限 5。**它不是简历区块**——`SECTION_IDS` 保持 5 段，期望城市不进 `content_html`：它是投递元数据（将来给 B7 的来源/城市匹配用），把它塞进简历会让「一页 A4」多一个我控制不了的变量。今天 `student_profiles` 连 `city` 列都没有（只有 `grade/contact/email/target_role`），所以这条是**新列都没有 → 直接开表**，不存在「先加列再迁」的第二步 |

连带一处措辞要跟着改准：§3 里「新表必须加进 `_CHILD_TABLES`」现在是**三张**（`profile_certificates` / `portfolio_links` / `profile_city_preferences`），不是两张。

## 6. 实现补充：版本号与链头约束

- `is_head` 使用 VIRTUAL 而非 STORED：MySQL 对 STORED 生成列的基列不允许 `ON DELETE SET NULL`；保留自指表达式、唯一索引与 SET NULL 语义，迁移测试验证约束。
- `student_profiles.resume_version_seq` 保存已分配的最高版本号。生成事务先锁档案行，再递增序号、插入快照和更新指针；避免并发生成分到同号，也避免删除链头后重用已分配序号。序号不是当前版指针，读当前版仍只认 `current_version_id`。
- 迁移按现存 `version_no` 初始化计数，不按创建时间推断链头；旧快照正文不改写。降级移除计数列，历史数据降级风险仍按 §3 处理。

