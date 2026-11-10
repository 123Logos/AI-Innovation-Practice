# ADR-019：B2 的两格——字段级「待补充」标记的边界，与隐私检查在没有导出的落地形态

- 日期：2026-11-10
- 状态：**已生效并已由批次 B2 实现**（同 PR 交付）。决策人：本 ADR 由我（编码 agent）拍板，依据老板 2026-11-10 第 8 轮「**你可以按照 roadmap 的开发顺序一直开发，不用暂停等我，也不用问我问题，除非非常紧急**」+ 第 9 轮「**全程不需要再和我确认，只最终结果交付**」。我把这两句读成「**写 ADR 但不等批准**」，不是取消记录：§4.6 的 ADR 前置、BYOK 逐条同意、`ADR-016` §7 别名种子逐条批仍然一条不松，撞到就跳过那一格并登记（本批没撞到）。
- 关联：`ADR-010`（生成/润色两条路互不调用）、`ADR-011` 议题 2.A（纯计算端点的先例：`/jobs/extract-skills`）、`ADR-014`（全面解冻）、`ADR-018`（「出现但标注」——本 ADR 的第三格与它同一条判断线）、`docs/PRD.md` §7 FR-06、`docs/ROADMAP.md` §2 B2 行、`docs/API.md` §6

## 1. 背景与取证（先量再拍）

FR-06 有两句话落进 B2：「**缺少信息时输出待补充标记**」与「**导出前显示隐私检查：联系方式、作品链接、个人信息和授权范围**」。开工前取到的事实：

| 取证 | 结果 | 它决定了什么 |
|---|---|---|
| `app/` 里 `export` / `pdf` / `privacy` / 「隐私」 | **零命中**（只有 `app/integrations/evidence_scorer.py` 一个无关词） | 「导出前」这个时刻今天不存在，检查没有挂载点 |
| 简历导出依赖（reportlab / weasyprint / 无头浏览器） | **一个都没装** | 真做导出＝新依赖＝§4.6 事件 + 老板批准，本批不做 |
| `student_profiles` 的全部列 | `id / user_id / title / is_default / deleted_at / paper_size / theme / layout_config / full_name / email / grade / contact / target_role / created_at / updated_at` | **没有作品链接列**（属 B4）、**没有手机号第二列**（`contact` 就是它）、**没有任何授权/可见性列** |
| 全仓 `consent` / `authoriz` / `visibility` / `is_public` | 零命中（只有 HTTP `Authorization` 头与 CORS 头） | 「授权范围」这一类今天无数据可查；教师端授权属 B5 |
| 既有契约 `tests/test_resumes_generate.py::test_generate_omits_email_span_when_profile_has_none` | 钉的是「没填邮箱 → `contact-email` 不出现，且**不留孤立的 ` · ` 分隔点**」 | 给邮箱打「待补充」会撞这条已验证行为 |
| `ADR-018` §6（老板 2026-11-09 第 6 轮定案「维持『出现但标注』，不改」） | 硬边界不删，但每次咬人必须**可见**；可见的义务按边界分层 | 本 ADR 第 3 格的判据来源 |

## 2. 决策

### 2.1 「待补充」标记只打给**已开槽的内容字段**

在 `TemplateResumeGenerator` 里，三格空值渲染成 `待补充：{字段名}`：经历描述、个人职责、教育专业。判空用 `strip()`，因为库里存过纯空白的段落，渲染出来就是一条真空行，它与「没填」在纸面上是同一件事。

**不打标记的三类，各有各的理由，不是漏网**：

1. **可选项（邮箱）**：标了会被 HR 看见，而这条信息用户本来就不打算给。「没填邮箱」这件事改由隐私检查说给他——那里才是能动手补的地方。既有契约 `..._omits_email_span_...` 因此**原样保留、不放宽**，并补一条反面对照 `test_generate_does_not_mark_optional_email_as_pending`。
2. **整节缺失（没有技能 / 没有教育经历）**：走「空模块自动跳过」，`tests/test_resumes_generate.py` 的两条用例钉着（用户主动删光技能后不该看见空标题）。这一格由隐私检查如实报告。
3. **生成硬门槛**：`generate()` 对「姓名/手机号/技能/经历」为空直接 400，根本渲染不出简历，所以没有可标的东西。

### 2.2 隐私检查 = 一个新的**纯计算端点** `POST /resumes/privacy-check`

- 输入只有可选的 `profile_id`，与 `/generate` 共用同一个 `_resolve_profile`（不是复制一份判断），因此「检查的是哪一份」与「生成用的是哪一份」不可能分叉。
- 输出四类，顺序即界面顺序：`contact / portfolio / personal / authorization`。每类 `{key,label,status,note,items[]}`，每格 `{key,label,present,included,note}`。
- **零新表、零新列、零新依赖、零 BYOK 注入面**（不调 AI）。`_resolve_profile` 的签名从 `(user_id, payload)` 改成 `(user_id, profile_id)` 是这次唯一的既有代码改动。

### 2.3 三种状态，其中 `not_available` 是这一格的核心

`status = present | missing | not_available`。后两者的区别是**事实与责任**的区别：

- `missing` = 数据模型里有这一栏，用户没填 → 用户可以自己修；
- `not_available` = 数据模型里**根本没有这一栏** → 今天不可能有答案，责任在产品不在用户。

今天落在 `not_available` 的是作品链接（B4 才有列）与授权范围（B5 才有记录）。`not_available` 时 `note` **必给**——不给，用户看见的是一个没有来由的空壳。这条是 `ADR-018` §6「硬边界仍要存在但要可见」在数据层的同构：**产品做不到的那一格也要可见**。

### 2.4 `present` 与 `included` 分成两格，不许合并

「这一栏有没有」和「这一栏会不会被带出去」是两个问题。合在一起的代价是具体的：目标岗位偏好确实存在但不写进简历，用一格表达必然要在「说它有」和「说它不上简历」之间选一个，两个都不完整。

## 3. 后果

- 实现物：`app/integrations/resume_generator.py` 两个私有函数 + 三处调用、`app/schemas/resume.py` 四个响应模型、`app/services/resume_service.py` 的 `privacy_check()` 与 `_build_privacy_check()`、`app/api/v1/resumes.py` 一条路由。
- 测试：`tests/test_resume_privacy_check.py` 14 条 + `tests/test_resumes_generate.py` 追加 4 条 = **+18**（全量 487 → **505 passed**）。跨端点一致性用 `test_privacy_check_and_generate_point_at_the_same_resume` 钉住（同一份空经历简历，两边必须同时判「没填」，且 `/generate` 当场 400）。
- `docs/API.md` §6 新增该端点条目，并明写它与「设计意图表」里那个未实现的 `/resumes/{id}/export` 的关系。
- **B2 没有被做完**：`导出前显示` 这半句要等导出真落地才有挂载点；作品链接/授权范围两格要等 B4/B5。这一格登记进 §5「留给他的」。

## 4. 替代方案（都量过代价）

- **把检查塞进 `/generate` 的响应**：省一个端点，代价是给一个已验证的写端点加响应字段——那正是老板在 `ADR-018` §6 否过的形状（「维持『出现但标注』，不改」连我多加的一条实现要求都否了）。而且导出根本不经过 `/generate`。
- **只在前端做一份静态提示**：零后端改动，但那句「会被带出去什么」必须由前端自己读档案字段来判断，等于把 `ADR-013`/`ADR-017` 好不容易收回后端的判据再散出去一次。
- **为「授权范围」建一张 consent 表**：能让清单看起来四类齐全，代价是无中生有——今天既没有教师端也没有推荐流程（B5/B7 未开工），建了只能存空行，还要额外一份 §4.6 的 ADR。这是「为了让指标好看而扩范围」的教科书形状。
- **把作品链接/授权范围标成 `missing`**：省一个状态值，代价是**对用户撒谎**（明明是产品没这一栏，说成是他没填）。这是我否决得最干脆的一条。

## 5. 留给他的（不催，登记）

1. **可选项不打标记**这个边界是我定的（§2.1 第 1 类）。若你认为纸面上任何空缺都必须标出来，改 `resume_generator.py` 邮箱那一支即可，同时要把 `test_generate_omits_email_span_when_profile_has_none` 按 `ADR-018` 的方式**改写方向**而不是删掉。
2. **`not_available` 这个状态值**（§2.3）是我为「产品做不到」新增的表达位。若你希望界面只说「暂无数据」，前端可以只按 `status` 做两态收敛，后端契约不必改。
3. 隐私检查的**界面入口**在哪：今天没有导出，所以也就没有「导出前」那一屏。等导出落地时它应该挂在导出按钮的确认弹层里——那个弹层基础件正是批次 F1b 已经交付的 `stores/dialog.ts` + `components/ConfirmDialog.vue`，届时不必再造第三个弹层形态。
