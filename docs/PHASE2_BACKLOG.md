# Phase 2 需求池

> 本文件记录 Phase 1（MVP）评估后认为有价值、但超出 Phase 1 范围或暂不紧急的条目，
> 作为下一阶段（Phase 2）的迭代依据。
>
> - 状态：**2026-11-07 逐条对代码复核过一遍**（此前长期停留在「全部未开始」，与代码不符）。
>   勾选框只在「已实现且有测试/复现步骤」后才可勾选——这条规则没有放宽，
>   下面每个 `[x]` 都附了代码位置或用例名，没有证据的一律仍是 `[ ]`。
> - 排序：按优先级分组（高 → 中 → 低），组内不表示先后。
> - 维护规则：新增条目请写清「现状 → 需求 → 验收标准」，避免只有一句口号。
>   复核时**不要删掉当初的观察**，它记录了当时的判断依据；补一行「现状（2026-11-07 复核）」即可。
>
> 最后更新：2026-11-07

---

## 一、待修复 Bug / 体验优化（优先级：高）

这些问题会直接影响可用性，Phase 2 应优先处理。

- [x] **登录用户名大小写处理**（2026-11-07 复核：已实现，且比本条当初写的「二选一」做得更完整）
  - 现状：`username` 未做任何大小写归一，注册按用户输入原样存、登录按 `User.username == username` 原样比。
  - **实际行为取决于数据库排序规则**：当前库是 MySQL 默认 `utf8mb4_0900_ai_ci`（大小写不敏感），
    所以 `ZhangSan` 现在也能登录，唯一索引也挡不住只差大小写的重复注册——**这是「碰巧对」，不是代码保证的**。
    一旦换库/换 collation（`_bin`、`_cs`、SQLite、TiDB 某些配置），或迁移里显式指定排序规则，行为立刻翻转。
  - 需求：在应用层显式归一，不依赖数据库隐式行为。二选一：注册时 `.lower()` 后存储，或登录校验时忽略大小写（如 `Username.lower()`）。
  - 动机：避免用户因误开 CapsLock 而登录失败，这类失败无法自我诊断且会直接流失；也让行为可测、可移植。
  - 注意：改动需处理**存量数据**（已有含大写的用户名如何兼容/是否需要回填），并同步调整唯一性冲突的错误提示。
  - 现状（2026-11-07 复核）：应用层归一在 `app/schemas/auth.py:28`（`strip().lower()`）与
    `app/services/auth_service.py:72`（注册）、`:89`（登录）两处都做了，不是「二选一」而是两边同一口径；
    用例 `tests/test_auth.py::test_register_lowercases_username`（含入库值断言）与
    `::test_login_is_case_insensitive`（`.lower()` / `.upper()` / `.swapcase()` 三种输入都能登录）；
    存量回填见 `alembic/versions/20261103_0005_backfill_lowercase_usernames.py`，
    它用 `BINARY` 比较——因为在 `utf8mb4_0900_ai_ci` 下 `<>` 对只差大小写的值恒假，
    直接写 `WHERE username <> LOWER(username)` 会**一条都匹配不到、回填静默空跑**。
    唯一需要知悉的遗留：该迁移的 `downgrade()` 是 `pass`，文件里论证了数据回填不可逆。

- [x] **Token 静默续期机制**（2026-11-07 复核：已实现；「续期失败要提示」这半句本轮才补齐）
  - 现状：Access Token 15 分钟过期，长表单填写（档案 + 多段经历）中途掉线，已填内容可能丢失。
  - 需求：引入 Refresh Token 机制，或前端实现 401 拦截器触发自动续期。
  - 验收：连续填写超过 15 分钟不被打断；续期失败时给出明确提示而不是静默白屏。
  - 现状（2026-11-07 复核）：`frontend/src/api/client.ts` 三层——请求拦截器按
    `REFRESH_LEEWAY_MS = 10_000` 提前续期（不等 401 才补）、`refreshPromise` 做 single-flight
    （并发 401 共享同一次刷新）、响应拦截器 401 → 续期 → `_retried` 只重放一次；
    后端 `/auth/refresh` 在 `app/api/v1/auth.py:92`，refresh token 有效期 7 天
    （`app/core/security.py:32`），库里只存 SHA-256 哈希
    （`app/repositories/refresh_token_repository.py:36`），轮换与重用检测由
    `tests/test_auth.py::test_refresh_rotates_token_pair` / `::test_refresh_rejects_reused_token` 钉住。
  - **本轮补上的缺口**：验收后半句「给出明确提示」原来只做到「跳回登录页」，
    而 `LoginView.vue` 从不渲染那句原因——用户看到的是「一个莫名要求我重新登录的页面」，
    他会怀疑账号坏了，而不是知道该重新登录。现在被服务端踢回来的那一次，
    登录页会显示一次性提示（`armSessionExpiredNotice()` / `consumeSessionExpiredNotice()`，
    判据在 `router/index.ts` 的回调里「跑到第几行」，不是调用方传的标记：
    已经在登录页时的 401 只能是密码打错，对那个人说「登录状态过期」是撒谎）。
    用例：`frontend/src/views/__tests__/LoginView.spec.ts`（含「主动登出不得显示这句话」）。
    真实浏览器 375px 复核：浅色对比度 5.4:1、暗色 9.92:1，均过 WCAG AA。

- [x] **纸面内容编辑回写机制**（2026-11-07 复核：走的是本条允许的第二条路径；自动化用例本轮补齐）
  - 现状：A4 纸面支持点击直接编辑，但改动**仅存入 Pinia** 并参与当前预览，失焦时不写回后端数据库；刷新页面即丢失。
  - 需求：完善回写流程（失焦/防抖自动保存），或提供明显的「保存当前修改」按钮。
  - 验收：纸面上改一处文字 → 刷新页面后仍在；改动与编辑区表单双向一致。
  - 现状（2026-11-07 复核）：实现选了第二条路径——**不是**失焦自动保存，所以「失焦时不写回后端」
    这句字面上**至今仍然成立**，别误读成没做。纸面 13 处 `contenteditable` 在 `@blur` 回写 store
    （`frontend/src/components/ResumePreview.vue:391/:404/:455/:569` 等），
    `saveLabel` 在有改动时显「保存修改」、无改动时显「已保存」且禁用（`:190-192`），
    点击走 `store.save()` → `PUT /profiles/{id}`。「双向一致」由构造保证：
    编辑区与纸面读写同一个 `store.draft`。
  - **本轮补上的缺口**：本文件开头那条规则是「勾选框只在已实现**且有测试**/复现步骤后才可勾选」，
    而 `ResumePreview.vue`——用户盯得最久的那个组件——此前**一个用例都没有**
    （`components/__tests__/` 里只有 `AppTopBar.spec.ts`）。现在 10 条：
    两态按钮、失焦回写、纸面与编辑区同一句话（按标签定位真实 textarea 的 `value` 来断言，
    不是注释声明）、恰好一次 `PUT` 且请求体带的是刚改的那句、
    保存失败时按钮不得变「已保存」且用户打的字三处都还在、重新挂载后仍在。
  - 仍未覆盖（写清楚，别让这条看起来比实际强）：jsdom 无布局引擎，纸面高度/页数
    （`scrollHeight` → `setPageCount`）在这些用例里恒为 1 页；`innerText` 是垫片代理，
    真实浏览器的选区保持、粘贴换行、`pre-wrap` 下与 `textContent` 的差异不在覆盖面内。

---

## 二、新增功能需求（优先级：中）

- [ ] **导入外部 PDF 简历**
  - 需求：允许用户上传已有的 PDF 简历，系统解析并自动填充表单。
  - 待评估：解析方式（文本层提取 / 版面模型）、字段映射准确率、失败时的降级路径（只填能识别的部分）。

- [x] **简历 DIY 拖拽排版**（2026-11-07 复核：**「拖拽」是被有意否决的方案**，交付的是按钮调序 + 自定义栏目标题 + 纸张 + 主题）
  - 需求：允许用户自由拖拽调整模块顺序、修改栏目标题。
  - 待评估：需引入拖拽库或富文本编辑器；**重点评估对 A4 网格系统的影响**（分页、行高、打印边距不能因此失真）。
  - 现状（2026-11-07 复核）：`frontend/package.json` 里**没有任何拖拽库**，这是决定不是遗漏——
    纸面上有 13 处 `contenteditable`，`mousedown + mousemove` 会被浏览器解释成「选文字」，
    拖拽与就地编辑必然抢手势（理由写在 `StylePanel.vue:264-266`，出处 ADR-007 §8.1）。
    实际交付：`↑` / `↓` 调序（`:287/:297`，用例 `resume.spec.ts:120/:134`）、自定义栏目标题、
    纸张与主题；顺序与标题的解析契约由
    `tests/test_document_style_layout.py::test_valid_config_keeps_order_and_titles_verbatim` 钉住。
  - **本条当初的顾虑已经不适用**：排期时不要把「拖拽库对 A4 网格的影响」当成待评估的技术风险重排一遍。
    仍然真实存在的缺口是**打印一致性靠人**：`ResumePreview.vue:766` 的 `@media print` 取消了缩放并置
    `min-height: 0`，但 `StylePanel.vue:354` 要求用户自己在打印对话框里选对纸张并勾掉页眉页脚，
    这一步没有回归测试，也不可能有——它发生在浏览器里。

- [x] **多份简历 / 档案库**（2026-11-07 复核：已实现，上限 20 份）
  - 现状：用户与简历是一对一关系。
  - 需求：允许一个用户创建并管理多份不同版本的简历（如「投后端」「投算法」两版）。
  - 待评估：数据模型从一对一改为一对多，涉及主表唯一约束、前端切换与命名交互。
  - 现状（2026-11-07 复核）：`alembic/versions/20261103_0007_multi_profiles.py` 删掉 `user_id`
    唯一索引并加 `title` / `is_default` / `deleted_at`；上限 `MAX_PROFILES_PER_USER = 20`
    （`app/services/profile_service.py:42`）；重名标题 422 由
    `tests/test_profiles.py::test_rename_profile_rejects_duplicate_title` 钉住，
    封顶由 `::test_profile_count_is_capped` 钉住，回收站过期清理是
    `::test_expired_trash_is_purged_lazily_on_list`（读侧触发，不是定时任务）。
    前端切换器在 `AppTopBar.vue`（每行常驻重命名/删除 + 排序），回收站 `RecycleBinView.vue`。

---

## 三、技术架构与规范（优先级：低，持续改进）

- [x] **接入真实的 AI 大模型**（2026-11-04 完成，见 [ADR-009](adr/ADR-009-deepseek-byok-nonstream.md)）
  - 现状：文案润色走真实 DeepSeek `deepseek-flash`（BYOK：用户自带 Key，服务端不落库、不写日志、
    非流式、thinking 关闭、30 秒预算、`max_retries=0`）；**排版仍由 `TemplateResumeGenerator` 负责**，
    模型只产出经历文案与技能排序的 JSON，服务层按四条合并规则回贴后套模板——这样 `v-html` 注入路径上
    的每个字段依旧 `escape()`，A4 一页与主题/顺序契约不受模型影响。
  - 前置已满足：ADR-009 写清了 Key 管理（BYOK + CORS 白名单 + 第三方 logger 压级）、Prompt 约束
    （禁止新增技能/经历/改时间）、超时与重试（30s / 不重试）、失败分流（400/401/502 三码）与降级
    （必须用户点出来）。
  - **仍待人类做的一件事**：Token 计费与成本上限没有服务端强制（Key 是用户的，成本由用户自负），
    且真实 Key 下的端到端效果、429 的真实形态尚未手测。

- [x] **测试隔离问题修复**（2026-11-07 复核：被顺带解决，但**不是**本条设想的方案，且留下一条新纪律）
  - 现状：`tests/test_auth.py` 使用 `drop_all` 清理，与 `test_migrations` 共库时会产生 3730 冲突，须先手动 `alembic downgrade base`。
  - 需求：改为每个用例独立事务回滚或独立 schema，做到「直接 `pytest` 就能跑」。
  - 现状（2026-11-07 复核）：`tests/conftest.py` 现在按 `APP_TABLES`（`:121`）的**外键安全顺序做 DELETE**
    （`:138 _clear_app_tables()`，每个用例前后各一次；注释里量过 DELETE 6ms vs TRUNCATE 454ms，
    所以没选 TRUNCATE），会话级只 `upgrade head`、不 downgrade（`:177`）。
    `grep drop_all|create_all tests/` 现在只在 conftest 的**注释**里命中，`test_auth.py` 已无 `drop_all`。
    「直接 pytest 就能跑」达成。
  - 但它**不是**「独立事务回滚 / 独立 schema」，而是共享库 + 顺序 DELETE，因此得到一条新的硬纪律：
    **禁止并行跑两个 pytest 进程**。两个进程会各自在每个用例前后清空全部 9 张业务表，
    互相把对方正在用的行删掉，产出的是一批与代码无关的偶发失败。
    本轮监督者自己就踩了两次（第二次伪装成「43 failed」，差点被当成格式化引入的回归）。

- [ ] **CI/CD 部署上线**（2026-11-07 复核：拆成两半，(a) 已完成、(b) 未开始——别让整条看起来还差一半）
  - 现状：CI 四件套（ruff / mypy / import-linter / pytest）只在本地验证过，未在 GitHub Actions 实际运行；服务未上公网。
  - 需求：配置 GitHub Actions 真实运行，并将前后端与数据库部署至公网服务器。
  - 待评估：数据库迁移的发布流程、密钥与配置注入、限流从进程内计数改为共享存储（多实例下当前实现会失效）。
  - 现状（2026-11-07 复核）：
    - **(a) 已达成**：`.github/workflows/ci.yml` 在 master push 上真实跑绿，覆盖
      `ruff format --check` / `ruff check` / `mypy` / `lint-imports` / `pytest`（带 MySQL 8.0 service，
      含 upgrade+downgrade 迁移检查）/ `npm ci` / `vitest` / `vue-tsc` / `vite build`。
      上面那句「只在本地验证过」已经过期。
    - **(b) 未达成**：公网部署没有任何证据。仓库里只有 `Dockerfile.backend` / `Dockerfile.frontend` /
      `docker-compose.yml`，而 `ADR-008-docker-configs-unverified.md` 自己标注了**未验证**。
      不要从这些文件推断成「已部署」。
  - 口径待定：本条与文末出口标准写的是「CI 在 **PR** 上真实绿过」。实际仓库是单分支 trunk-based，
    `gh pr list --state all` 为空、从未有过 PR。按字面判未达成；若接受「master push 上绿」等价则达成。
    **需要人类负责人定口径**，不要由文档默认。

---

## 附：Phase 2 出口标准（草案）

进入 Phase 2 前，至少满足：

1. 「高」优先级三项全部关闭，并各有用例或手动复现步骤；
2. CI 在 PR 上真实绿过；
3. 有一轮真实用户（≥ 5 人）从注册到导出简历的完整链路通过。

### 2026-11-07 逐条对照

1. **满足**。三项均已实现且都有自动化用例（见上文各条的「现状（2026-11-07 复核）」）。
   其中第 3 项「纸面回写」此前**只有手动步骤**，按本文件开头那条规则本来不该勾选，
   本轮补了 `ResumePreview.spec.ts` 之后才成立。
2. **口径待定**（见 §三 第 8 条）：CI 真实在跑且绿，但仓库从未有过 PR。
3. **满足**：2026-11-07 人类负责人宣布 5 名真实用户测试完成（在另一套数据库上进行，数据已归档，
   用户无额外建议）。这一条是外部事实，仓库内无法自证，按 §1 的口径以人类负责人确认为准。

> 还没走完的那半：**目标是 10 个真实用户**，`docs/MVP_SCOPE.md:98` 写的是
> 「10 个真实用户 | Phase 1 先完成 5 个，Phase 2 补齐 10 个」。现在是 5/10。
