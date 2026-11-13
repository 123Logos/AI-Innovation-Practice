# ADR-026：简历版本对比与按岗位定制（批次 B8 剩余部分）

- 状态：已实现（2026-11-13）
- 关联：`docs/PRD.md` FR-06、`ADR-015` §2.3 / §6（版本指针与链头）、`ADR-007`（自定义排版）、
  `ADR-017` §5（动作层 `is_expired`）、`ADR-024`（动作层拦截的先例）、
  `docs/ROADMAP.md` §1 B8、§7 第 18 轮「该交叉里唯一未落的是版本对比 UI 与按岗位定制」

## 1. 背景：指针有了，但「版本」这件事对用户还是不可见

B8 的前半（2026-11-11）只交付了**指针与读路径**：

- `resumes.version_no` / `current_version_id` / `is_head` 生成列 + 档案计数器
  `student_profiles.resume_version_seq`（迁移 `0016` / `0017`）；
- `GET /resumes/current` 只读链头，指针落空显式 400。

也就是说：每次「生成」都真的落了一行新版本，**但没有任何端点能把它们列出来，
更没有东西能回答「这一版和上一版差在哪」**。FR-06 要的「版本对比」在数据上已经成立
（每行是一份完整的 `content_html` 快照），缺的是读取与解释。

同时 FR-06 的另一半「按岗位定制」从来没有过产品口径。本 ADR 把它收窄成一条
**不可能造假**的规则（§2.5），因为「定制」是这套系统里最容易滑向「AI 编内容」的地方。

### 1.1 一个必须正面回答的问题：版本对比到底比什么

版本是**整份 HTML 快照**（`ADR-015` §2.3 的选择），不是结构化差分。于是有三种做法：

| 做法 | 问题 |
|---|---|
| 对两段 HTML 做字符级 diff | 用户看到的是 `class="skill-item"` 与实体转义的噪音；改一个技能名会让后面所有字节错位 |
| 让 AI 读两份 HTML 说差异 | 不可复现、要 Key、可能编出「他把 XX 改成了 YY」这种不存在的变化 |
| **按渲染结构拆成「区块 → 条目 → 字段」再比** | 解析器要认得模板结构；但它给出的每一条差异都能在纸面上指到具体一行 |

选第三种。理由是这套 HTML **不是任意 HTML**：它由 `TemplateResumeGenerator` 生成，
类名集合是封闭的（`paper-section` / `skill-item` / `experience-title` / …），
`ResumePreview.vue` 与它逐类名对应。按结构解析是「读自己的产出」，不是猜别人的格式。

**不认识的快照不许假装能比**：若某个区块里既找不到已知条目类名，整次对比返回
`comparable=false` + 一句说明，而不是返回一份「什么都没变」的空 diff——
后者会让用户以为两版一样，那是最坏的一种错误（`ADR-015` §2.3 对「猜」的同一态度）。

## 2. 决策

### 2.1 数据模型：加两列，不加表

`resumes` 增：

| 列 | 类型 | 说明 |
|---|---|---|
| `origin` | `VARCHAR(16) NOT NULL DEFAULT 'generated'` + `ck_resumes_origin` | `generated` / `customized`。**不靠标题前缀或文本来猜**「这是不是定制版」 |
| `target_job_id` | `CHAR(36) NULL`，FK → `jobs` `ON DELETE SET NULL` | 定制版面向哪条岗位 |

不加 `resume_versions` 之类的从表：版本**本来就已经**是 `resumes` 的行
（`ADR-015` §2.3 的核心决定），再建一张从表就会出现「一行简历、一行版本」两份事实。

**岗位标题不做列，也不做 join**：定制版的 `title` 落成
`‹简历名› · 面向「‹岗位名›」`。这是快照语义的正确形态——岗位后来改了名，
手上这份简历的名字不该跟着变。`target_job_id` 只用于「回到那条岗位」的导航。

### 2.2 链头不变式：定制版本**进入**历史，但不移动链头

把 `ADR-015` §2.3 的语义写成一条不变式（本批把它显式化，此前只在代码注释里）：

> 对某个档案的**所有** `resumes` 行，`current_version_id` 都指向同一个 `id`
> （即链头）；链头行自指。`is_head = IF(id = current_version_id, 1, NULL)`。

两种写入的差别只有一行：

| 动作 | 新行 `current_version_id` | 是否移动链头 | 旧行是否被改 |
|---|---|---|---|
| 生成（`create_resume`，既有） | 先 `NULL` → 最后改成自己的 id | **是** | 全部指向新行 |
| 定制（`create_customized_resume`，本批） | 直接写成**当前链头的 id** | **否** | 一行都不动 |

于是定制版天然满足「每个档案至多一条链头」（它的 `is_head` 是 `NULL`），
也天然进入 `list_versions` 的历史序列。**没有 `is_head` 的翻转，就没有
「定制之后 `GET /resumes/current` 返回了定制版」这种惊喜**——这正是本批要保证的事。

链头必须存在：定制要「基于某一版」，没有链头时不猜、不建，直接
`400 RESUME_NOT_GENERATED`（「先生成一份简历，再按岗位定制」）。

### 2.3 `version_no` 仍由档案计数器分配（定制也占号）

定制版拿一个**新的** `version_no`（`resume_version_seq + 1`），哪怕它不是链头。
理由与 `ADR-015` §6 同一条：版本号是「第几次产出」的流水号，不是「链头第几代」。
让两个不同内容的行共用一个号，会让「v3 vs v3」这种对比请求失去意义；
而不占号又会让历史序列出现「v2 之后凭空多出一行但编号还是 v2」的窟窿。

副作用要写清楚：**链头号可能不连续**（生成 v1、定制 v2、再生成 v3，链头序列是 1→3）。
界面上按 `version_no` 倒序列出全部版本，链头用徽标标出，不承诺连续。

### 2.4 差异的返回结构：区块 → 条目 → 字段

```jsonc
{
  "from_version": {"resume_id": "…", "version_no": 2, "title": "…", "origin": "generated", "is_head": false, "created_at": "…"},
  "to_version":   {"resume_id": "…", "version_no": 3, "title": "…", "origin": "customized", "is_head": true,  "created_at": "…"},
  "comparable": true,
  "note": null,
  "summary": {"added": 1, "removed": 0, "changed": 2, "sections_changed": 2,
              "section_order_changed": false, "item_order_changed": true},
  "sections": [
    {"key": "skills", "label": "技能", "change": "changed",
     "title_before": "技能", "title_after": "技能", "order_changed": true,
     "items": []},
    {"key": "experiences", "label": "项目经历", "change": "changed",
     "title_before": "项目经历", "title_after": "项目经历", "order_changed": false,
     "items": [
       {"kind": "changed", "label": "实习：订单系统", "before": "…", "after": "…",
        "fields": [{"field": "个人职责", "before": "…", "after": "…"}]}
     ]}
  ]
}
```

四条硬规则：

1. **区块用结构识别，不用标题识别**：`key` 靠区块内出现的条目类名决定
   （`education-school`→`educations`、`skill-item`→`skills`、…）。用户把「技能」改名成
   「技术栈」时，结果是一条 `title_before/title_after` 的差异，而**不是**
   「删掉一个区块 + 新增一个区块」。按标题认区块的实现会把改名读成整段删除。
2. **条目按锚点文本配对**：项目/教育/证书/作品用各自的标题类；技能用技能名本身。
   同名重复按「出现次序」配对（第 1 个「实习」对第 1 个「实习」）。
3. **字段级只说认得的那几格**：项目（描述 / 职责 / 时间）、教育（专业 / 学历 / 入学 / 毕业）、
   证书（颁发机构 / 颁发日期）、作品（链接）。段落里的「待补充：描述」是文本，
   照常参与比较——它本来就是纸面上可见的内容。
4. **顺序变化单独报**：`order_changed`（条目顺序）与 `section_order_changed`（区块顺序）
   必须各自有位置。按岗位定制**只会**造成顺序变化（§2.5），如果没有这两个标记，
   一份真正变了的简历会被算成「什么都没变」。

### 2.5 按岗位定制 = 只重排，一个字都不改

`POST /resumes/customize` `{profile_id?, job_id}`。

生成规则（全部是**排序**，没有任何改写）：

1. 技能：命中岗位技能（`skill_name_key` 同一判定键，含别名层）的排前面；
   两组内部各自保持档案里的原顺序（**稳定排序**，用户可以预期）；
2. 项目经历：按「该段经历描述里命中的岗位技能条数」降序，同分保持原序；
3. 其余（教育 / 证书 / 作品 / 联系方式 / 配色 / 排版）一字不动；
4. 标题：`‹简历名› · 面向「‹岗位名›」`（截到 200 字，与列宽一致）。

**为什么不做「删掉不相关的经历」或「改写描述以贴合 JD」**：

- 删条目是替用户做取舍，而他可能正需要那段经历（且它已经在**别的**版本里存在，
  删了只会让两个版本的差异变成一团）；
- 改写描述等于让系统在没有 AI 审核链路的情况下新增事实——`ADR-009` / `ADR-018`
  已经把「AI 只改文案、不改事实」写死了，定制这条路不该成为绕过它的后门。

于是定制版的差异**应当只出现在顺序上**。这不是限制，而是可验证性：
用户打开 diff，如果看到「新增了一条技能」，那说明出了 bug，而不是「AI 帮他加了点东西」。

响应同时给一份**不写进简历**的对照信息，让「定制」这件事有实际用处：

```jsonc
{"resume": { … ResumeRead … },
 "based_on_version_no": 3,
 "focus": {"job_id": "…", "job_title": "Python 后端实习生",
           "matched": ["Python", "MySQL"], "missing": ["Docker", "Kubernetes"]}}
```

`missing` 是**提示**不是结论：它只说「JD 里点了这三样，你的档案里没有」，
不评价重不重要，也不写进纸面。

### 2.6 端点契约

| 端点 | 说明 | 错误 |
|---|---|---|
| `GET /resumes/versions?profile_id=` | 该档案的全部版本，按 `version_no` 倒序 | 档案不存在/越权/回收站 → 404；一份档案都没有 → 400 |
| `GET /resumes/versions/diff?from_version_id=&to_version_id=` | 两版差异；两版必须属于**同一个**档案且属于当前用户 | 任一版本不存在/不属于自己/与另一版不同档案 → 404（不区分原因，避免探测） |
| `POST /resumes/customize` | 按岗位定制，返回新版本 + 对照信息 | 岗位不存在/未发布/已删 → 404；岗位已过期 → 409 `JOB_EXPIRED`；还没生成过简历 → 400；档案不完整/排版旧版 → 与 `/generate` 逐字相同 |

`diff` 是 `GET` 且参数是版本 id：它是**纯读**，两个 id 都在 URL 里，
不产生任何写入，可以安全地被浏览器缓存/刷新。`customize` 是 `POST`：它落一行新版本。

岗位过期拦截走 `app/core/deadline.is_expired`（`ADR-017` §5 的动作层约束）：
「为一条已经截止的岗位定制简历」是能省则省的无效劳动，且它会产生一行**新数据**，
属于动作层，不属于读侧。

### 2.7 与 `ADR-015` 的两处偏离，都必须记账

1. **不再是「全仓唯一非 RESTRICT 外键」**：新增 `fk_resumes_target_job`
   （`ON DELETE SET NULL`）。理由与 `current_version_id` 同构——岗位会被用户删，
   RESTRICT 会变成「有定制版就删不掉岗位」的死锁；而定制版本身必须活下来。
   代价：仓里从此有 2 个 SET NULL 的简历外键，`tests/test_migrations.py` 的
   `set_null_fk` 名单同步加一条（那条断言是 `==` 精确集合，漏改就红）。
2. **`is_head` 的语义补一句**：「自指即链头」在定制行上不成立，
   正确表述是 §2.2 的不变式（所有行指向同一个 id，链头自指）。
   `ADR-015` §2.3 的原文不删，本 ADR 作为补充口径。

## 3. 后果

**好的**：

- 「生成了几次、每次差在哪」第一次对用户可见，且差异指得到纸面上的具体一行；
- 定制版可解释：diff 里只应有顺序变化，出现别的就说明代码有问题；
- 链头语义没有被定制动作污染，`GET /resumes/current` 的行为一行不改。

**代价 / 明确不做**：

- 不提供「把某一版设为当前版」（回滚）。它需要一次「翻转所有行指针」的写操作，
  与定制版的「不移动链头」正好相反，产品上要先说清「回滚后新版本从哪一版长出来」；
- 不提供删除单个版本（回收站删的是**档案**，不是版本）；
- diff 不处理跨模板版本的快照（老模板没有 `paper-section` 结构时 `comparable=false`）。

## 4. 验收判据（每条都先红过）

| 判据 | 用例 |
|---|---|
| 定制版落库但不移动链头（`GET /current` 仍返回原链头） | `tests/test_resume_customize.py::test_customize_reorders_without_moving_the_head` |
| 定制版进入历史列表，且 `origin` / `target_job_id` 如实 | `::test_versions_report_origin_head_and_target_job` |
| 定制只改顺序：diff 里没有 added/removed 的条目 | `::test_customized_version_differs_only_in_order` |
| 技能命中项真的排到最前（稳定排序，其余保持原序） | `tests/test_resume_customize.py::test_customize_puts_matched_skills_first`（读生成物里的技能序列，`React/Python/SQL` → `Python/React/SQL`） |
| 改名区块不会被读成「删一个加一个」 | `tests/test_resume_diff_rules.py::test_a_renamed_section_is_a_title_change_not_remove_plus_add` |
| 纯排序变化不产生 added/removed 条目 | `tests/test_resume_diff_rules.py::test_reordering_is_reported_as_order_change_without_item_changes` |
| 两版必须属于同一档案 | `tests/test_resume_customize.py::test_diff_across_profiles_is_rejected` |
| 过期岗位不能定制（动作层 `is_expired`） | `::test_customize_rejects_an_expired_job` |
| 没生成过任何简历时定制 → 404（不猜、不建） | `::test_customize_without_a_generated_resume_is_rejected` |
| 结构不认识时 `comparable=false`，不返回「无差异」 | `::test_unrecognized_snapshot_is_not_comparable` |
