# ADR-005：Phase 2 高优先级三项需求的技术方案选型（草案）

- 状态：**草案（待人类拍板，尚未选定任何方案）**
- 日期：2026-11-03
- 决策人：本项目人类负责人
- 关联需求：`docs/PHASE2_BACKLOG.md` §一「待修复 Bug / 体验优化（优先级：高）」
- 关联文档：`docs/adr/ADR-004-auth-jwt.md`（JWT 无状态鉴权，本 ADR 第 4 节是它的后续）

> **本文档不替人类做决定。**
> 每个决策点只提供「事实 → 选项 → 代价」三样东西。凡涉及「必须连带做另一件事」的地方
> 都以 **依赖** 标注，那是技术约束而非倾向；凡涉及偏好（安全 vs 便利、速度 vs 完备）
> 的地方一律留空，收在第 7 节的待拍板清单里。
>
> 第 2 节是只读核实的结果，其中**纠正了需求池里两处与代码不符的描述**——
> 它们会直接影响选型，请先读第 2 节再看选项。

---

## 1. 背景与范围

Phase 1 已结项（HEAD `9111518`），工作区干净。Phase 2 按需求池优先级启动，
本轮需要为三项「高」优先级需求选定技术路线：

| # | 需求 | 用户可感知的痛点 |
|---|---|---|
| D1 | 登录用户名大小写处理 | 开着 CapsLock 登录失败，且失败原因不可自诊断 |
| D2 | Token 静默续期 | 15 分钟后掉线，长表单填写被打断 |
| D3 | 纸面内容编辑回写 | 纸面上改的内容不进数据库 |

三项都被标成「体验优化」，但它们的技术性质差别很大：
D1 是**语义归一**（可能零表结构变更），D2 是**会话模型重构**（大概率要新表），
D3 是**写路径与并发模型**（表结构可能不变，但接口语义和前端时序要重排）。
三者之间还有耦合，见第 6 节。

---

## 2. 只读核实结论（代码事实，非推测）

### 2.1 D1 用户名大小写：当前「能用」是 collation 给的，不是代码给的

| 位置 | 事实 |
|---|---|
| `app/schemas/auth.py::RegisterRequest.validate_username` | 只做 `strip()`、拒绝空白、长度 ≥ 3，**没有任何 `lower()`** |
| `app/services/auth_service.py::register` | 先 `get_by_username(payload.username)` 查重，再原样 `create_user(username=payload.username)` |
| `app/repositories/user_repository.py::get_by_username` | `select(User).where(User.username == username)`，**没有 `func.lower()`** |
| `alembic/versions/20261023_0001_initial_phase1.py` | 建表时 `_table_args()` **显式钉死** `mysql_charset=utf8mb4, mysql_collate=utf8mb4_0900_ai_ci`；`UniqueConstraint("username")` |

只读验证（不改任何数据，直接问服务器 collation 语义；`resume_mvp_test` 库）：

```sql
SELECT 'ZhangSan' = 'zhangsan' COLLATE utf8mb4_0900_ai_ci,   -- 1
       'resume'   = 'résumé'   COLLATE utf8mb4_0900_ai_ci,   -- 1
       LOWER('ZhangSan');                                    -- 'zhangsan'
SELECT 'ZhangSan' = 'zhangsan' COLLATE utf8mb4_0900_as_cs;   -- 0
```

输出：`(1, 1, 'zhangsan')` / `(0,)`。

结论：

1. `utf8mb4_0900_ai_ci` 是 **case-insensitive + accent-insensitive**。
   于是 `SELECT ... WHERE username = 'ZhangSan'` 在 MySQL 侧就能命中 `zhangsan` 那一行，
   **登录现在其实已经忽略大小写**。
2. 同理，唯一索引也不区分大小写：注册 `ZhangSan` 之后再用 `zhangsan` 注册，
   会在 `create_user` 的 `IntegrityError` 上被捕获，最终返回 400「用户名已被注册」。
3. 因此需求池里「同一用户两个登录名」的描述不准确——
   **当前不存在「两个只差大小写的账号」**。

真正的风险是**这层语义由 collation 隐式提供，代码里没有任何一行表达这个意图**：

- 排序规则一旦改（迁移里换成 `utf8mb4_0900_as_cs` / `_bin`），登录会**突然**变严格，
  而代码、测试、文档都不会有任何变化，也没有任何测试会失败；
- `ai_ci` 的 `ai` 还额外吞掉**重音差异**（`resume` / `résumé` 视为同名），
  这一点没人要求过，却被 collation 一并给了；
- 现有测试里没有任何一条断言「大小写不同也能登录」，所以这个行为是**未被守护的**。

### 2.2 D2 Token 续期：当前**没有任何可用于续期的接口**

| 位置 | 事实 |
|---|---|
| `app/core/security.py` | `create_access_token(subject, expires_minutes, now)`，载荷固定 `sub/type="access"/iat/exp`，HS256 |
| `app/core/security.py::decode_token` | 硬校验 `payload["type"] == "access"`，否则 `TokenError` |
| `app/core/config.py` | `jwt_expire_minutes` 默认 15；`jwt_expire_seconds` 派生 |
| `app/schemas/auth.py::TokenResponse` | `access_token` / `token_type="bearer"` / `expires_in` |
| `app/api/v1/auth.py` | 只有 `POST /register`、`POST /login`、`GET /me`。**没有 `/refresh`** |
| `frontend/src/stores/auth.ts::applySession` | 只把 `access_token` 写进 store + `localStorage`，**`expires_in` 被丢弃** |
| `frontend/src/api/client.ts` | 401 响应拦截器直接 `clearStoredToken()` + 跳登录页，**没有重放机制** |
| `pyproject.toml` | 依赖里**没有 Redis**（ADR-004 已把 Redis 列入 Phase 1 冻结清单） |
| `app/core/rate_limit.py` | 限流是 slowapi 默认的**进程内内存计数**，多 worker 各算一份 |

三条直接约束：

- **依赖 A**：任何「静默续期」都必须先有一个能签发新 access token 的端点。
  现在唯一能签发 token 的是 `/auth/login`，而它要密码。
  所以「纯前端定时器」单独存在是不成立的——定时器到点后没有接口可调。
- **依赖 B**：前端现在**不持久化过期时刻**。刷新页面后只剩一个裸 token 字符串，
  定时器无从得知还剩多久（要么解 JWT 的 `exp`，要么把 `expires_in` 一起存下来）。
- **依赖 C**：401 拦截器现在是「终态处理」（清 token + 跳转）。
  要做「401 后自动续期再重放」，这段逻辑必须改成可中断、可排队、可重试的流程。

### 2.3 D3 纸面回写：现状比需求描述好一点，但方向仍然错

| 位置 | 事实 |
|---|---|
| `frontend/src/components/ResumePreview.vue` | 所有 `contenteditable` 都在 `@blur` 时调 `store.updateXxx()`，只改 Pinia |
| `frontend/src/stores/resume.ts::markContentChanged` | 每个 `updateXxx` 都走它，但 `dirty` 由**内容投影与基线的差异**算出（批次 F6 / `ADR-018` §5），不再无条件置真 |
| `frontend/src/stores/resume.ts::startDraftAutosave` | `watch(draft, {deep:true})` → **400ms 防抖写 `localStorage`** |
| `frontend/src/views/ResumeEditorView.vue` | 进页面 `detectLocalDraft()`，有差异就弹「检测到上次未保存的草稿，是否恢复？」 |
| `app/repositories/student_profile_repository.py::replace_profile_content` | **全量替换**：父表 update + 三张子表 `DELETE` 后整批重新 `INSERT`，**每次都是新 UUID** |
| `app/schemas/profile.py::ProfileRead` | 已暴露 `updated_at`，但经 `format_shanghai_datetime` 后是**秒级**（`timespec="seconds"`），库里是 `DATETIME(6)` |

两处需要修正需求描述：

1. **「刷新即丢失」不成立**。失焦后 400ms 内内容会写进 `localStorage`，刷新会弹出恢复横幅。
   真实缺陷是「**没有进数据库**」：换浏览器/换设备就没了，横幅也容易被忽略，
   而且这份本地草稿和「保存档案」是两套并存的机制。
2. **`PUT /profiles/me` 是全量替换，且子行 ID 每次重生成**。这决定了任何「频繁自动保存」
   方案都会带来两个副作用：子表行 ID 抖动；两处同时保存 = 后写者覆盖前者，无版本校验。

---

## 3. 决策一（D1）：用户名大小写处理

### 选项 A：应用层 `.lower()`（注册归一 + 登录归一）

- 改动：`RegisterRequest` 或 `AuthService.register` 里 `username.lower()`；
  `AuthService.login` 侧同样归一后查询。**表结构不变**（这一步没有迁移）。
- 存量数据处理（三种做法，代价不同）：
  - A-i **不回填**：老的大写用户名在库里保持原样，但因为查询已经 `lower()`，
    需要**要么**查询用 `LOWER(username) = :normalized`（放弃索引，走全表扫，`users` 表小可接受），
    **要么**把存量一次性回填成小写（一次性迁移脚本，非 DDL）。
  - A-ii **回填**：`UPDATE users SET username = LOWER(username)`。
    在当前 `ai_ci` 下这一步**不会产生新的唯一性冲突**——因为 collation 早已保证不存在
    仅大小写不同的两行。**但仍必须在真实库上先 `SELECT` 复核再执行**，不能凭推理。
  - A-iii **回填 + 加 CHECK**：MySQL 8.0.16+ 支持 `CHECK (username = LOWER(username))`，
    但给已有表加 CHECK 要新建迁移，且 `LOWER()` 是逐行计算，对大表不友好。
- 利：
  - 语义写在代码里，换库/换 collation 行为不变；可用普通 `WHERE username = :v` 走索引。
  - 数据库里大小写统一，运营侧导出、`GROUP BY`、客服查询都不会出现两种写法。
  - 可以顺手补一条「大小写不同也能登录」的测试，把这个行为**锁住**。
- 弊：
  - **会丢失用户输入的大小写展示**（`ZhangSan` 注册后显示成 `zhangsan`）。如果以后产品
    想要「昵称式显示」，得再加一列。
  - Python `.lower()` 与 MySQL `LOWER()` 对个别字符（如 `İ`）结果不同，
    必须**只在一处归一**（建议全在 Python 侧），否则两边规则打架。
  - 回填是**不可逆的数据变更**（原大小写信息永久丢失），需要备份。

### 选项 A′：加一列 `username_normalized`（展示与归一分离）

- 改动：新增列 + 唯一索引 + 回填迁移；`username` 保留原样用于展示，
  所有查重/登录都走 `username_normalized`。
- 表结构变更：`users` 加 `username_normalized VARCHAR(100) NOT NULL`，
  加 `UNIQUE(username_normalized)`，回填 `LOWER(username)`；`UniqueConstraint("username")` 保留还是删除需决定。
- 利：展示保留原大小写，判等严格可控，未来还能扩成 Unicode 折叠（`casefold`）。
- 弊：多一列要维护（写入路径必须同步两份，容易漏）；迁移最重；
  Phase 1 只有 `users` 一张账号表，为一个显示细节加列是否值得，取决于产品是否真的在意展示大小写。

### 选项 B：不改代码，只补文档（显式承认「靠 collation」）

- 改动：`docs/adr/ADR-004` 或 `docs/API.md` 补一句「用户名大小写不敏感由
  `utf8mb4_0900_ai_ci` 保证」，外加一条测试锁定行为。**表结构不变、零迁移**。
- 利：零成本、零数据风险；现在的行为本来就是正确的（用户要的「CapsLock 也能登录」已满足）。
- 弊：
  - 意图仍不在代码里。谁改迁移里的 `mysql_collate`（或谁把库迁到别的引擎），
    行为静默翻转，而且**只有用户会发现问题**。
  - 顺带继承了 `ai_ci` 的重音不敏感语义，这属于「免费赠送的副作用」。
  - 如果以后要做「用户名只允许小写」这类运营规则，仍然得回到选项 A。

### 选项 C：其他方案（供参考，非穷举）

- **C-1 注册时直接拒绝非小写**（`username != username.lower()` → 422「用户名只能用小写字母」）。
  零迁移，但把成本转给用户，且已有大写账号的用户会被自己的账号挡住（必须先回填）。
- **C-2 用邮箱当登录名，`username` 降级为昵称**。彻底绕开大小写问题（邮箱本来就该 `lower()`，
  `validate_email` 已经在做），但这是产品级改动，超出「修 Bug」范围。
- **C-3 `username` 列改为 `_as_cs` collation + 显式 `LOWER()` 查询**。
  把「大小写不敏感」从存储层挪到查询层，唯一索引变成区分大小写（可存 `A` 和 `a` 两行），
  必须配合 A 的归一才有意义。属于「既要显式、又要精确控制」的组合拳，迁移成本与 A′ 相当。

### 对比

| 维度 | A（lower 归一） | A′（加归一列） | B（只补文档） | C-1（只许小写） |
|---|---|---|---|---|
| 表结构变更 | 无（可选 CHECK） | 加列 + 唯一索引 | 无 | 无 |
| 需要迁移 | 可选（仅数据回填） | **必须** | 无 | 可选（数据回填） |
| 存量数据风险 | 中（回填不可逆） | 中 | 无 | 中 |
| 保留展示大小写 | ✗ | ✓ | ✓ | ✗ |
| 换 collation 后行为稳定 | ✓ | ✓ | ✗ | ✓ |
| 前端改动 | 注册页提示「统一小写存储」 | 同 A | 无 | 注册页提示「只能小写」 |
| 可加的测试 | 「大小写不同可登录」+「回填后无重复」 | 同 A + 「归一列唯一」 | 「大小写不同可登录」 | 「大写注册被拒」 |

### 需人类拍板

- **Q1-1**：是否接受「用户名展示统一为小写」（选 A 就必须接受）？
- **Q1-2**：存量大写用户名是**回填**（不可逆）还是**保持原样 + `LOWER()` 查询**（放弃索引）？
- **Q1-3**：是否需要保留「重音也视为相同」（`ai_ci` 的额外语义）？若要，选 B 反而更贴切；若不要，A/A′/C-3 都可能需要处理。

---

## 4. 决策二（D2）：Token 静默续期

前提事实（第 2.2 节）：**必须先有一个续期端点**。本节的 A/B 都不是「单点改动」，
而是「续期端点 + 前端时序」两半的组合。下表按「refresh 凭据存在哪」来分，
因为那是唯一会改变表结构/基础设施的变量。

### 选项 A：双 Token（Access 15 分钟 + Refresh 7 天）

不变的部分：`POST /auth/refresh`（入参 refresh 凭据，出参新 access + 新 refresh）、
`decode_token` 要拆出 refresh 类型（现在硬校验 `type == "access"`，**依赖**：需要把
`ACCESS_TOKEN_TYPE` 泛化成两个常量 + 两条校验路径）。

**A-1 落 MySQL 新表**

- 表结构变更：新增 `refresh_tokens`
  - `id CHAR(36) PK`
  - `user_id CHAR(36) NOT NULL`，FK → `users.id`（`ON DELETE CASCADE` 或 `RESTRICT` 需定）
  - `token_hash CHAR(64) NOT NULL`（存 SHA-256，**不存明文**）
  - `issued_at` / `expires_at DATETIME(6)`
  - `revoked_at DATETIME(6) NULL`
  - `rotated_from CHAR(36) NULL`（轮换链，用于复用检测）
  - `user_agent VARCHAR(255) NULL` / `ip VARCHAR(45) NULL`（可选，审计用）
  - 索引：`UNIQUE(token_hash)`、`INDEX(user_id, expires_at)`
- 轮换：每次 refresh 都「旧行置 `revoked_at` → 插新行」；
  若拿来用的 refresh 行**已被撤销**，说明凭据泄露被重放 → 把该用户整条链全部撤销并强制重新登录。
- 利：可吊销（登出即失效）、可轮换、可审计、可「查看登录设备」；不引入新基础设施。
- 弊：每次续期一次 DB 写 + 一次查；7 天 × N 台设备的过期行需要清理任务（或惰性清理）；
  多一个表 = 多一份迁移与测试负担。

**A-2 无状态 Refresh JWT（不落库）**

- 表结构变更：**无**。
- 利：零迁移、零查询；实现最小。
- 弊：**不能吊销**——登出只是前端丢弃，被盗的 refresh 在 7 天内一直有效；
  无法做轮换/复用检测（没有「旧凭据是否已用过」这个状态）。
  本质上是把 ADR-004 里「短有效期缓解不可吊销」的取舍，换成「长有效期 + 不缓解」。

**A-3 Redis 白名单**

- 表结构变更：无；但引入 Redis（依赖 + 运维）。
- 利：TTL 自动过期，无需清理表；吊销天然支持；后续限流也能一起搬到 Redis。
- 弊：与 `ADR-004` 明确记录的「Redis 在 Phase 1 冻结清单内」冲突；
  本地/CI 多一个服务要起，违背「零配置可跑」。若真要上，通常是和「CI/CD 部署」一起做。

**传输方式（与存储正交，必须单独定）**

- 响应体 + `localStorage`：与现状一致，改动最小；代价是 XSS 可读走 7 天凭据。
- `HttpOnly` Cookie：XSS 拿不到；代价是 CSRF 防护 + 跨站 Cookie 约束
  （ADR-004 已论证过本地 `http://localhost` 无法验证 `Secure`）。

### 选项 B：前端定时器（在过期前 N 分钟触发）

- 关键事实：**定时器本身不是方案，只是调度器**。它调的那个接口仍是 A 的 `/auth/refresh`，
  所以 B 在实现上是 A 的前端部分，不构成独立选项。
- 若要 B 独立成立，只能是这两种退化形态：
  - **B-1 定时器 + 提前跳登录页**：到点前 1 分钟弹「会话即将过期，请重新登录」。
    不改后端、不加表。代价是把「静默」变成「打扰」，长表单场景没真正解决。
  - **B-2 定时器 + 延长 access 有效期**（例如 8 小时）。
    只改 `jwt_expire_minutes` 一行。代价是 token 被盗窗口从 15 分钟拉到 8 小时，
    与 ADR-004「短有效期缓解不可吊销」的取舍直接冲突。
- 前端改动点（B 无论哪种形态都要动）：
  - `auth.ts`：**持久化过期时刻**（现在 `expires_in` 被丢弃）；`refresh()` 动作；
    定时器生命周期（登录设、登出清、刷新页面重建）。
  - `client.ts`：401 拦截器从「终态」改成「续期 + 重放」，且必须 **single-flight**
    （并发 5 个请求同时 401，只能触发 1 次 refresh，其余排队等结果）。
  - `router/index.ts`：`ensureUser()` 失败时不能立即 `logout()`，否则续期还没跑就被踢。
  - 多标签页：`storage` 事件同步，避免两个标签各自持有一份会轮换掉的 refresh。

### 选项 C：其他方案

- **C-1 滑动续期（每次请求都在响应头下发新 token）**：用户永不掉线。
  代价是要改中间件对所有受保护响应注入头部，等价于长期有效 token，且前端每次都要换 token。
- **C-2 401 后静默续期 + 重放（不预置定时器）**：只在 401 时续期，平时零开销。
  严格说这是「A 的前端实现风格」，与 B 的区别只是**触发时机**（被动 vs 主动）。
  被动式的问题是：携带大表单的 `PUT` 在 401 后重放要保证幂等（当前 `PUT /profiles/me` 是全量替换，天然幂等，可重放）。
- **C-3 会话保活 `/auth/me` 心跳**：每 5 分钟 ping 一次维持「活跃感」，
  但 `/auth/me` 不会延长 `exp`，**不解决掉线**，只能更早发现掉线。不建议单用。

### 对比

| 维度 | A-1（MySQL 表） | A-2（无状态 JWT） | A-3（Redis） | B-1（只提示） | B-2（延长有效期） |
|---|---|---|---|---|---|
| 新增表 | **是**（`refresh_tokens`） | 否 | 否（但需 Redis） | 否 | 否 |
| 新增依赖 | 无 | 无 | **是**（redis 客户端） | 无 | 无 |
| 可吊销 | ✓ | ✗ | ✓ | — | ✗ |
| 轮换/复用检测 | ✓ | ✗ | ✓ | — | ✗ |
| 用户真的不掉线 | ✓ | ✓ | ✓ | ✗ | 部分（8h 后仍掉） |
| 实现量 | 大 | 小 | 中 | 小 | 极小 |

### 需人类拍板

- **Q2-1**：refresh 凭据存哪（A-1 表 / A-2 无状态 / A-3 Redis / 暂不做）？
- **Q2-2**：传输用「响应体 + localStorage」还是「HttpOnly Cookie」？（安全 vs 改动量）
- **Q2-3**：是否要求「登出即失效」（决定 A-2 是否可用）？
- **Q2-4**：若选 B-2，是否接受安全窗口从 15 分钟放宽到 8 小时？（这是产品风险偏好，不是技术问题）
- **Q2-5**：`/auth/refresh` 是否也纳入现有 5 次/分钟限流？（同一 IP 多标签页会共享额度）

---

## 5. 决策三（D3）：纸面内容编辑回写

先明确目标状态（三种「解决」的定义，请人类选其一后再谈实现）：

- **目标 1（弱）**：改动不会因为刷新而**消失**。→ 现状（`localStorage` 草稿 + 恢复横幅）**基本已满足**。
- **目标 2（中）**：改动会进数据库，但允许用户明确知道「已保存 / 未保存」。
- **目标 3（强）**：改动自动落库，用户完全不用管。

三个选项分别对应不同目标，**不是同一件事的三种做法**。

### 选项 A：防抖自动保存（1 秒）→ 目标 3

- 改动：store 里再加一个 1s 防抖定时器，`dirty` 时调 `save()`；`client.ts` 无需改。
- 必须同时处理的四件事（任一缺失都会变成新 Bug）：
  1. **并发冲突**（见下方「冲突处理三选」）；
  2. **与手动保存互斥**：`saving` 期间新改动要排队，不能两路 `PUT` 交叉；
  3. **与 localStorage 草稿的关系**：不收敛就是双写体系（400ms 写本地 + 1s 写服务端），
     刷新后「本地草稿」和「服务端档案」谁为准需要明确规则；
  4. **生成前 flush**：`StylePanel.handleGenerate()` 现在只判 `dirty` 然后 `confirm`，
     改自动保存后要改成「等在途保存完成再生成」，否则会生成旧版本。
- 表结构变更：**当前接口下无**（`updated_at` 可当版本号，见下）。
  但要注意 **子行 UUID 抖动**：`replace_profile` 每次都 `DELETE`+`INSERT`，
  自动保存越频繁，`skills.id` / `experiences.id` 变化越频繁——
  一旦 Phase 2 后面做「拖拽排序」「PDF 导入映射到某一行」，这些引用会随时失效。
- 利：用户无感，最贴近「所见即所得」的承诺。
- 弊：写放大最严重（每次 1 个字 → 全量替换 4 张表）；冲突面最大；
  一旦自动保存静默失败，用户会以为已保存（**比现在更危险**）。

### 选项 B：失焦（`blur`）时自动保存 → 目标 3（更保守的触发）

- 改动：`ResumePreview.vue` 的 `commitText()` / `updateExpDate()` 里，回写 store 后触发一次保存。
- 关键事实：纸面上的 `contenteditable` **已经是 blur 驱动**的，所以触发点天然存在。
- 利：请求次数比 1s 防抖少（只在真的离开单元格时写一次），语义也更清楚（「这一段我写完了」）。
- 弊：
  - blur 会被「点一下别处」触发，用户其实没写完也可能提交；
  - 同 A 的写放大问题，且更集中（连续改 5 个字段 = 5 次全量替换）；
  - 与「保存档案」按钮的最后一次点击会竞态（点按钮 → blur 先触发自动保存 → 按钮再发一次）。

### 选项 C：显式「保存当前修改」按钮 → 目标 2

- 改动：
  - 纸面工具栏（`preview-toolbar`）加一个按钮，`dirty` 时高亮/显示未保存提示；
  - 复用现有 `store.save()`（`ResumeEditor.vue` 已有 `handleSave` + 反馈文案，可直接抽出共用）。
- 利：**零并发问题**（人不会一秒点五次）；用户明确知道发生了什么；
  与「生成前先保存」的现有交互（`StylePanel` 的 confirm）语义一致；
  实现最小、最容易被人类验收。
- 弊：**仍然会忘**。忘记点 = 数据只在 `localStorage`（现状），目标 3 没达成。
  另外与左侧「保存档案」按钮功能重复，需要合并成一个概念（否则用户会困惑「两个保存有什么区别」）。

### 选项 D（补充）：局部更新接口 `PATCH /profiles/me`

- 改动：新端点接受 `{field_path, value}` 或部分子结构，只更新对应行；
  子行要**保留原 ID**（改成 upsert 而非 delete+insert）。
- 表结构变更：不必须，但想保留子行 ID 就得改 `replace_profile` 的写法（行为变更，非 DDL）。
- 利：写放大消失；多标签页冲突面大幅缩小（改不同字段不互相覆盖）；为后续拖拽排序铺路。
- 弊：接口语义从「整体替换」变成「增量合并」，前端 `toUpsertPayload()` 的「过滤空行」逻辑要重写；
  空值语义（「清空某字段」vs「没提交」）要重新定义；改动量最大，且会牵动已有 59 个测试里的档案用例。

### 冲突处理三选（选 A 或 B 才有意义）

| 做法 | 代价 | 是否需要迁移 |
|---|---|---|
| 后写覆盖（last-write-wins） | 最简；两个标签页互相覆盖，且后覆盖者自己不知道 | 无 |
| `If-Match: updated_at` → 不匹配返回 409 | 需要把 `updated_at` 当版本号；**但它序列化后只有秒级精度**（`timespec="seconds"`，库内是 `fsp=6`），同一秒内两次保存会拿到相同版本号 → 校验形同虚设，要么改成毫秒输出，要么接受误放行 | 无（但需改序列化精度） |
| 新增 `version INT` 列，每次 +1，`If-Match` 比对 | 唯一可靠的乐观锁；语义清晰 | **是**（加列 + 回填） |

### 对比

| 维度 | A 防抖 1s | B blur | C 按钮 | D PATCH |
|---|---|---|---|---|
| 达成目标 | 3（强） | 3（强） | 2（中） | 取决于前端怎么用 |
| 写放大 | 最严重 | 严重 | 最小 | 小 |
| 并发复杂度 | 高 | 高 | 无 | 中 |
| 子行 ID 抖动 | 严重 | 严重 | 轻微 | 无（若改 upsert） |
| 静默失败风险 | **高**（用户以为存了） | 高 | 低（有按钮反馈） | 中 |
| 改动量 | 中 | 中 | **小** | 大 |
| 表结构变更 | 无（除非加 `version`） | 同 A | 无 | 无 |

### 需人类拍板

- **Q3-1**：要达成哪个目标（弱 / 中 / 强）？这直接排除掉两个选项。
- **Q3-2**：若选自动保存，冲突策略取「覆盖 / `updated_at` 比对 / 新增 `version` 列」哪个？
- **Q3-3**：自动保存后，现有的 `localStorage` 草稿机制是**保留**（离线兜底）还是**移除**（单一真相）？
- **Q3-4**：纸面「保存当前修改」与左侧「保存档案」是否合并为一个动作？

---

## 6. 三项之间的耦合（选型时必须一起看）

1. **D2 × D3：自动保存 + Token 过期 = 静默丢数据。**
   自动保存若在 401 时被拦截器直接清 token 跳登录页，那一次保存就**丢了**，
   而用户已经继续在写下一段。所以选「自动保存」时，D2 必须至少做到
   「401 → 续期 → 重放一次」，否则两个改动叠加会比现状更糟。
2. **D3 × 现有生成流程**：`StylePanel.handleGenerate()` 现在是
   「`dirty` → confirm → save → generate」。改成自动保存后，这段要重写成
   「等在途保存完成 → generate」，否则会生成上一版内容。
3. **D2 × 限流**：`/auth/refresh` 若复用 `AUTH_RATE_LIMIT`（5 次/分钟），
   同一 IP 多标签页可能自己撞 429，而 429 会打断自动保存。
4. **三项 × 测试库老坑**：`tests/test_auth.py` 的 `drop_all` 与 `test_migrations` 的 3730 冲突仍未修。
   若 D2 引入 `refresh_tokens` 表，这个坑会更频繁地咬人（新表也要参与 drop/建）。
5. **D1 × D2 的登出入口**：加了 refresh 之后，「登出」才真正需要服务端动作（撤销 refresh）。
   如果 D1 选了「只补文档」而 D2 选了 A-2（无状态），登出依然是「前端丢弃」——两个取舍会互相强化成「都无法吊销」。

---

## 7. 待人类拍板的完整问题清单

| 编号 | 问题 | 影响 |
|---|---|---|
| Q1-1 | 能否接受用户名统一显示为小写？ | 决定 A 是否可行 |
| Q1-2 | 存量大写用户名：回填还是保留 + `LOWER()` 查询？ | 数据不可逆风险 |
| Q1-3 | 「重音也视为同名」是特性还是缺陷？ | 决定是否要脱离 `ai_ci` |
| Q2-1 | refresh 凭据存 MySQL / 无状态 / Redis？ | 是否新增表与依赖 |
| Q2-2 | 传输走响应体 localStorage 还是 HttpOnly Cookie？ | 安全模型 + 前端改动量 |
| Q2-3 | 是否要求「登出即失效」？ | 排除/保留 A-2 |
| Q2-4 | 若只延长有效期到 8h，是否接受该风险？ | 可能一行配置结案 |
| Q2-5 | `/auth/refresh` 是否纳入限流？ | 与自动保存的交互 |
| Q3-1 | 目标取「不丢 / 可知 / 无感」哪一级？ | 直接决定 A/B/C/D |
| Q3-2 | 冲突策略：覆盖 / `updated_at` / 新 `version` 列？ | 是否需要迁移 |
| Q3-3 | `localStorage` 草稿保留还是移除？ | 是否双写体系 |
| Q3-4 | 两个「保存」是否合并？ | 交互一致性 |

---

## 8. 无论选哪个方案，都应满足的验证要求

（写在这里是为了让实现阶段的验收标准不随选型漂移。）

1. **D1**：新增一条测试，断言「注册 `ZhangSan` 后，用 `zhangsan` / `ZhanGSan` 都能登录」。
   若选 A 且做回填，另加一条断言「回填后 `username` 全部为小写且无重复」。
2. **D2**：至少覆盖「过期 access + 有效 refresh → 200 新 token」、
   「过期 refresh → 401 并清会话」、「同一 refresh 用两次 → 第二次被拒（若做轮换）」
   「并发 5 个 401 → 只触发 1 次 refresh」（前端可用 SSR 脚本或单测验证）。
3. **D3**：纸面改一处 → 等一个防抖周期/失焦/点按钮 → `GET /profiles/me` 能读到新值；
   强刷页面后仍在；两个标签页同时改不同字段不互相覆盖（若选了冲突策略）。
4. 全部后端测试仍连真实 MySQL（`TEST_DATABASE_URL`），不使用 SQLite；
   新增表必须附 upgrade/downgrade 可逆验证。

---

## 9. 本 ADR 未做的事

- 未选定任何方案，未修改任何 `.py` / `.ts` / `.vue`，未改数据库结构，未新增迁移。
- 未评估「中 / 低」优先级条目（PDF 导入、拖拽排版、多简历、真实 LLM、CI/CD）。
- 未评估 refresh token 的具体加密/哈希参数、Cookie 属性细节、Redis 拓扑——
  这些属于选定方向后的实现设计，不宜在拍板前展开。
