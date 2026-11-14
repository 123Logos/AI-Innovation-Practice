# ADR-029: 教师端学情看板（B10a）与批量下载 / 导出审计（B10b）

- 状态：已采纳（Accepted，ADR 先行，代码随本文件同分支落地）
- 日期：2026-11-14
- 关联需求：FR-08（教师端学情看板只读、批量下载与下载审计）、第 16 轮「简历导出端点并入 B10b」
- 关联裁定：第 7 轮「数据层算、动作层不算」、第 15 轮「自然分页 + 溢出预警线」、
  第 18 轮裁定 ①「渐进式强制：按动作拦截」、第 19 轮本批硬约束
- 前置：`ADR-021`（组织 / 角色 / 审计）、`ADR-025` §2.5（`app/core/action_gate.py`）、
  `ADR-015`（简历版本链）、`ADR-028`（`profile_tags` 表）

## 1. 背景：这一批要同时关掉三个账

1. **B10a 的「看板」一直没有口径**：`profile_tags` 已经在 B9 落成一张可聚合的表，
   但「按什么维度聚合、聚合谁」没定；`MatchService` 的 AI 匹配分**不落库、按次算**，
   不可能拿它按班聚合——把「匹配度概况」做成「再跑一次 AI」是本批最容易走错的岔路。
2. **B10b 行里挂了两年的问题**：「下载审计与 B5 审计日志是同一张表还是两套」，
   不定就是第二个口径分叉点（`ROADMAP.md` §1 B10b 行原文）。
3. **导出端点一直不存在**：`ADR-019` §1 记着「`app/` 里 `export` / `pdf` 零命中」，
   所以 B2 的「导出前隐私检查」当年只能做成纯计算端点。本批要给出**真的**导出端点，
   且必须遵守第 15 轮裁定的打印方案，不能另写一套「塞进一页」的实现。

**两条硬约束（第 19 轮）**，本文逐条给出可测的落地形态：

- **A. 动作层组织闸**：B10a / B10b 所有相关入口（查看数据、下载、导出）**第一句代码
  必须过 `app/core/action_gate.py::require_organization`**；散人（无组织归属）被拦截
  并提示绑定组织。不许「先查数据再判有没有组织」——那会先泄露「这个组织有多少人」。
- **B. 导出打印方案**：PDF 导出端点必须 **自然分页 + 溢出预警线 + 非 scoped 的
  `@media print` `page-break-inside: avoid`**；不截断、不缩字号、不 `overflow: hidden`，
  页码由导出侧给。

## 2. 决策

### 2.1 动作闸：一张可枚举的表，一条入口判据

`app/core/action_gate.py::OrganizationAction` 增加三个取值（已有 `PUBLISH_JOB`、
占位的 `BULK_DOWNLOAD` 保留并**启用**）：

| 取值 | 用在哪 | 拦截文案 |
|---|---|---|
| `BULK_DOWNLOAD`（已占位） | `POST /teacher/resumes/batch-download` | `ErrorMessage.ORGANIZATION_ACTION_REQUIRED` |
| `VIEW_DASHBOARD`（新增） | `GET /teacher/dashboard` | 同上 |
| `EXPORT_RESUME`（新增） | `POST /resumes/{id}/export` | 同上 |

**「第一句代码」的机械定义**（不满足就算违约，有源码级测试钉住）：

1. 每个 B10 service 的公开方法，**第一条语句**是
   `organization_id = self._require_organization(context, action, ...)`；
2. `_require_organization()` 的**第一条语句**是
   `require_organization(org_id_or_none, action=...)`（即 `action_gate` 的真调用），
   **在任何仓储读取之前**；异常在这里就地翻成域异常 + 写一条 `result=denied` 审计；
3. 路由处理器的**第一条语句**是对应的 service 调用；权限依赖
   `require_permission(...)` 挂在参数上，先于处理器执行。

三条合起来保证：散人的请求**不读任何业务表**，只写一条拒绝审计。文案只从
`app/core/error_messages.py` 出（G3），`action_gate` 本身不带文案（沿用 `ADR-025` §2.5）。

**权限**：新增 `DASHBOARD_READ = "dashboard:read"` 与 `RESUME_DOWNLOAD = "resume:download"`，
只给 `teacher` / `school_admin` / `platform_admin`，**学生没有**（`ROLE_PERMISSIONS`
是前后端同源那张表）。组织闸与权限闸是两件事：一个是「有没有组织」，一个是「在这个组织里
是什么角色」，两个都要过。

### 2.2 B10a 看板聚合口径（只读、不调 AI、不返回个人信息）

**范围**：`organization_members` 里属于当前 `organization_id` 且 `role='student'` 的用户
→ 他们的 `student_profiles`（未删除）→ 这些档案上的 `profile_tags` / `skills`（活跃行）。
跨组织数据一律不进聚合；`platform_admin` 也**不自动跨组织**（要看别的组织得显式指定，
本批不提供该入口）。

**① 标签分布**（`tag_distribution`）：按 `(tag, status)` 分组，计数口径是
**「有多少名学生拥有这个状态的这条标签」**（`COUNT(DISTINCT student_profile_id)`），
不是标签行数——B9 的 `UNIQUE(student_profile_id, tag)` 保证一名学生一条标签，
但写出 `COUNT(*)` 仍然会在将来加「项目/教师证据」时把人算重。
输出同时给 `student_total`（范围内学生数）与 `tagged_student_total`（有任意标签的学生数），
让「0 条标签」与「根本没有学生」在界面上能区分。

**② 匹配度概况**（`match_overview`）：**不是** `MatchService` 的 AI 分数。
定义为「岗位技能覆盖率」，一次纯计算，不落库、不花钱、可复现：

```
job_required = { skill_name_key(s) for s in job_skills where job.status='published'
                 and job.organization_id = <当前组织> }
student_skills(profile) = { skill_name_key(s) for s in active skills of that profile }
coverage = |job_required ∩ student_skills| / |job_required|    # job_required 为空 → 0，且标 no_requirements
```

每个岗位返回 `required_skill_count`、`student_count`、`avg_coverage`、
`buckets = {below_half, half_to_eighty, eighty_plus}`（学生数）。
用 `app/core/skill_names.py::skill_name_key`（B7 同一份归一规则），**不接别名层**：
别名是匹配打分的产品口径，看板要的是「纸面上写没写这个技能」这种可解释的事实。
平均覆盖率用整数四舍五入（`app/core/assessment_rules.py::score_percent` 的 half-up 口径，
换算成 0–100 的整数），避免出现 `0.6666666667` 这种没法对账的小数。

**③ 不返回 PII**：响应只有计数、技能名、岗位 id / 标题、标签名与状态；
**没有姓名、邮箱、手机号、简历正文**。看板回答「这个班整体怎样」，不回答「某某同学怎样」。

**④ 空数据**：组织里没有学生 / 没有标签 / 没有已发布岗位时，返回结构完整的零值数组
与 `student_total=0`，**不返回 404、不返回 null**——前端不该为「正常为空」写一条错误分支。

### 2.3 B10b 批量下载：压缩包结构

`POST /teacher/resumes/batch-download`，请求体 `{"resume_ids": [...], "format": "pdf"}`，
上限 **50 条**（与 `AnswerBatchUpsert` 同量级；放开只会让「一次请求把整库导出」成为可能）。
响应 `application/zip`，文件名 `resumes_<YYYYMMDD-HHmmss>.zip`。

```
resumes_20261114-160000.zip
├── README.txt          人话：导出时间（东八区）、组织名、发起人用户名、条目数、格式、
│                       以及「本包只含已授权导出到本组织的简历版本」
├── manifest.json       机读：generated_at / organization_id / actor_user_id / format /
│                       items[{resume_id, profile_id, version_no, file, sha256, bytes}] /
│                       skipped[{resume_id, reason}] / item_count
└── resumes/
    ├── 001_<学生名>_v<版本号>.pdf
    └── 002_<学生名>_v<版本号>.pdf
```

- **只能下本组织学生的简历**：`resume_ids` 里不属于本组织 / 不存在 / 在回收站的 id
  一律进 `manifest.json` 的 `skipped`，**不报 400**（批量操作里夹杂一个坏 id 不该让整批失败），
  但如果**一个都不剩** → 400 `NO_DOWNLOADABLE_RESUME`。
- **文件名消毒**：学生名去掉路径分隔符与控制字符、截到 40 字符、折叠空白；
  序号前缀 `001_` 保证同名不覆盖。只写进 ZIP 的**文件名**，绝不用原始串拼路径。
- `manifest.json` 里的 `sha256` 是**每个 PDF 字节**的摘要，`bytes` 是长度：让「这一包到底
  下了什么」可以离线核对，不用回查库。
- 一次请求写**一条**批量审计（见 §2.4），不是每条简历一条。

### 2.4 下载审计日志表结构：**与 B5 同一张表，不建第二套**

回答 `ROADMAP.md` §1 B10b 行挂着的那个问题：**同一张 `audit_logs`**。
理由是「两套审计日志」必然长出两套 `action` 词表、两套 `denied` 语义、两份保留策略；
而下载事件需要的额外字段用 `detail` 的 JSON 就能装下，不需要新表。

新增三个 `AuditAction` 取值（白名单枚举，不许在调用点写字面量）：

| `action` | `target_type` | `result` |
|---|---|---|
| `dashboard.view` | `organization` | `ok` / `denied`（散人） |
| `resume.export_pdf` | `resume` | `ok` / `denied` |
| `resume.batch_download` | `resume_batch` | `ok` / `denied` |

**行形状**（`audit_logs` 既有列，逐格写死，写入方不得自由发挥）：

| 列 | 下载 / 导出事件里装什么 |
|---|---|
| `actor_user_id` | 发起人 `users.id` |
| `organization_id` | `require_organization` 返回的那个 id（**拒绝时为 NULL**，这正是 `denied` 行的形状） |
| `action` / `target_type` / `target_id` | 上表；单份导出 `target_id=resume_id`，批量为 NULL（多目标装进 `detail`） |
| `result` | `ok` / `denied` |
| `detail` | JSON：`{"resource_ids":[...],"item_count":N,"format":"pdf","bytes":N,"reason":"no_organization"}`；**拒绝行也必须写 `resource_ids`**，否则「谁试图导什么」答不了 |
| `ip` | 与 B5 同源，只取 `request.client.host`，不采信 `X-Forwarded-For` |
| `created_at` | `utc_now()`；**只追加**，本批不提供任何改 / 删入口 |

**被拒绝的尝试也记**（B5 `AuditResult.DENIED` 的既有纪律）：「谁在试图批量导出却没有组织」
是唯一证据。审计与业务**同一个 `session_scope()`**，一起提交；批量下载即使有 `skipped`
也记 `ok`（请求确实完成了，只是部分跳过），`item_count` 记**实际打包数**。

### 2.5 B10b PDF 导出：打印方案与渲染通道

`POST /resumes/{resume_id}/export?format=pdf`（当前只接受 `pdf`，其它值 422），
返回 `application/pdf` + `Content-Disposition: attachment; filename*=UTF-8''...`。

**① 导出的是「同一份纸面」**：从 `resumes.content_html`（B8 落库的生成物）取 HTML，
套进一个独立打印文档，注入 `frontend/src/components/ResumePreview.vue` 里**非 scoped
`@media print` 那一块**所定义的分页规则。不重新排版、不改字、不改结构。

**② 三条不可动的打印规则**（第 15 轮裁定，`ROADMAP.md` §6 第 5 条）：

- **自然分页**：`.paper` 保持 `min-height`、打印时 `min-height: 0`，不设固定 `height`、
  不 `overflow: hidden`、不缩放字号；内容超过一页就自然长到第二页；
- **段落级防腰斩**：`.paper .paper-experience` 等「一条内容」上写
  `break-inside: avoid; page-break-inside: avoid;`，标题写 `break-after: avoid`；
  **非 scoped**（生成物是 `v-html`，没有 `[data-v-*]` 属性，scoped 会静默失效）——
  `paperPaginationGuards.spec.ts` 已经把这条钉在 `ResumePreview.vue` 上，本批新增一条
  「导出侧用的是同一份 CSS」的测试，不许在后端抄第二份会漂移的样式；
- **溢出预警线不上纸**：`.paper-page-guard` 在 `@media print` 里 `display: none`；
  它是屏幕预览的软提醒，不是简历内容。页码由导出侧（浏览器打印参数）给，
  **不写进正文 HTML**——写进去既改了 `content_html` 的语义，又会在第二页重复。

**③ 渲染通道**：`app/integrations/pdf_renderer.py` 定义 `PdfRenderer` 抽象，
实现为无头 Chromium / Edge 的 `--print-to-pdf`（`docs/DEVELOPMENT.md` 里那条既有验收手段）。
这是**系统级工具**不是 Python 包：不新增 `pyproject.toml` 依赖；路径由
`Settings.pdf_browser_path` 显式给，没配就按候选路径找；找不到 →
`PdfRendererUnavailableError` → 503 + 一句可执行的提示（让用户改用浏览器打印），
**绝不返回一个假的 PDF，也绝不静默降级成 HTML**。
测试注入假 renderer（与 `test_interviews.py` 的 `ai_coach` 同构），CI 不依赖浏览器；
真实几何 / 分页仍按 `AGENTS.md` §4.5 用本机 headless Edge 截图与页数实测。
**本批实测（2026-11-14，本机 Edge）**：把 8 段长项目描述塞进同一份
`build_export_html()` 产物，用 `HeadlessBrowserPdfRenderer` 真跑
`--print-to-pdf` → `39955` 字节、`%PDF-1.4`、**2 页**（自然分页生效，
没有 `overflow: hidden` 截断，也没有被压成一页）。CI 里的假 renderer 验的是
「交给浏览器的 HTML 对不对」，这次本机实测验的是「浏览器真的能分页」，两者不互相代替。

**④ 每次导出都过 §2.1 的闸**（`EXPORT_RESUME`），并写一条审计。

### 2.6 端点清单

| 方法与路径 | 作用 | 闸 |
|---|---|---|
| `GET /teacher/dashboard` | 标签分布 + 岗位技能覆盖率概况 | 登录 + `dashboard:read` + `VIEW_DASHBOARD` |
| `GET /teacher/resumes` | 本组织可导出简历清单（看板页勾选用，不含正文） | 登录 + `dashboard:read` + `VIEW_DASHBOARD` |
| `POST /teacher/resumes/batch-download` | 打包下载（ZIP） | 登录 + `resume:download` + `BULK_DOWNLOAD` |
| `POST /resumes/{id}/export?format=pdf` | 单份 PDF 导出 | 登录 + `resume:download` + `EXPORT_RESUME` |

清单端点也过同一个动作闸，但**不写 `ok` 审计**：它只是页面加载时的一次读，
审计记的是「看了一眼看板」与「真的带走了文件」（§2.4）。给每次列表刷新都记一条
只会把有效证据淹掉。清单里含学生姓名（教师已授权、要看的是「谁的哪版简历」），
而聚合区块坚持不含 PII——两者是不同的读，不混进同一个响应。

**前端**：`TeacherDashboardView.vue` 一屏展示概况 / 标签分布 / 覆盖率 / 下载清单，
从工作区切换器进入；勾选后打包、单行导出。`api/teacherTools.ts` 是四个端点的唯一封装，
整个模块**一条 AI 调用都没有**，所以 BYOK 白名单一条都不加。

## 3. 后果

**好处**：看板口径可复现、不花钱、不泄露个人信息；下载审计与 B5 共用一张表，
`action` 词表只有一份；导出与预览共用同一份打印 CSS，不会出现「预览不腰斩、导出腰斩」。

**代价 / 如实登记**：

- PDF 真的生成依赖宿主机上有无头浏览器；本批不引入 Python PDF 库，
  CI 用假 renderer 验契约、本机用 headless Edge 验几何，**两者缺一不可**；
- 匹配度概况是「技能覆盖率」而不是 `ADR-013` 的匹配分，两者数值不可互相解释；
- 批量下载一次最多 50 条，超过要分批；本批没有异步任务与断点续传（`ADR-014` 解冻
  的是禁令，不是必要性）；
- 看板不含任何学生姓名，教师想看具体某人仍需走既有档案读端点（那属于另一个权限面）。

## 4. 验收判据

| 判据 | 用例 |
|---|---|
| 散人看板 / 下载 / 导出都被拦，且提示绑定组织 | `tests/test_teacher_tools.py::test_unbound_user_is_blocked_on_every_entry` |
| 组织内的学生过闸但被权限挡成 403 | `::test_student_with_organization_is_forbidden_by_permission` |
| 权限闸不跑到动作闸前面（403 会盖掉绑定提示） | `::test_every_entry_starts_with_the_action_gate` |
| 清单只列本组织的简历，聚合响应不含个人信息 | `::test_downloadable_list_only_contains_own_organization` |
| 空组织返回结构完整的零值 | `::test_empty_organization_returns_zero_structure` |
| 拦截发生在**读业务表之前**，且写 `denied` 审计 | `::test_denied_attempt_writes_audit_and_reads_no_business_data` |
| 源码级：每个 service 公开方法第一句就是动作闸 | `::test_every_entry_starts_with_the_action_gate` |
| 标签分布按学生去重，空组织返回零值结构 | `::test_tag_distribution_counts_students_not_rows` |
| 匹配度概况 = 技能覆盖率，不调 AI、不算匹配分 | `::test_match_overview_is_skill_coverage_not_ai_score` |
| 跨组织数据不进聚合 / 不进下载包 | `::test_other_organization_resumes_are_skipped` |
| ZIP 结构、manifest、sha256、文件名消毒 | `::test_zip_layout_and_manifest` |
| 审计行形状：`ok` / `denied`、detail JSON、批量一条 | `::test_download_audit_rows` |
| PDF 导出用非 scoped 打印 CSS，不截断不缩字 | `::test_export_html_uses_global_print_css` |
| 预警线打印时隐藏、页码不进正文 | `::test_export_hides_overflow_guard` |
| 浏览器不可用 → 503 而不是假 PDF | `::test_renderer_unavailable_is_not_a_fake_pdf` |

## 5. 遗留与下一步

1. B11b 的 BYOK 白名单 `/v1/tools/asr`、`/v1/tools/tts` 只在 `ROADMAP.md` 与 `ADR-022`
   登记（第 19 轮），**本批不碰**；
2. 看板的时间序列（「本周 vs 上周」）需要 `profile_tags` 的历史快照，今天只有当前值；
3. 导出格式只做 PDF，`docx` / 图片不在本批；
4. 批量下载的「异步 + 短时下载 URL」（`docs/API.md` §7 的旧设计意图）没有实现，
   本批是同步流式返回；要异步得先解决任务队列与文件存储的归属。
