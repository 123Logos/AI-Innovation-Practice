# ADR-025：登录标识（用户名 / 邮箱 / 手机号）与改密

- 状态：已实现（2026-11-13）
- 关联：`docs/PRD.md` FR-01、`ADR-004`（JWT 与双 Token）、`ADR-005`（用户名大小写归一）、
  `ADR-021` §2.2 / §2.4（`extra="forbid"` 与「教师必须带组织码」）、`ADR-023`（邀请制）、
  `docs/ROADMAP.md` §1 B6、第 18 轮最终裁定（路径收口 / 渐进式强制 / 「登录标识与改密照旧推进」）

## 1. 背景：三个标识、一个入口，以及一个必须同时拆掉的旧入口

今天的三件事实：

1. `POST /auth/login` 的请求体只有 `username`/`password`，服务层只按 `users.username`
   查一行（`AuthService.login`）。**邮箱与手机号都不是登录标识**，尽管 `users.email`
   已经存在且带唯一键。
2. `users` 表**没有手机号列**。学生档案里的 `contact`（`student_profiles.contact`）
   是「简历上展示的手机号」，与学生账号无关——把两者混为一谈会让「改简历上的电话」
   变成「改登录凭据」，那是两个完全不同权限等级的动作。
3. 注册入口同时承担了两件事：建账号 + 加入组织（`organization_code` + `role`）。

第 3 件已经被第 18 轮裁定拆掉：`organization_code` 入口废弃，统一「注册当散人 →
登录后凭邀请码加入」。所以本批不是「顺手加两个字段」，而是**一个进一个出**：
登录入口从 1 个标识扩到 3 个，注册入口从 2 条路收到 1 条。

### 1.1 为什么「拆掉旧入口」必须和本 ADR 同批

`ADR-021` §2.4 的规则是「声明 `teacher`/`school_admin` 就必须带有效组织码」。
一旦注册不再收 `role`，这条规则**没有存在的余地**：能声明角色的地方只剩「加入组织」，
而加入组织时角色由**邀请码**决定（`ADR-023` §2.1，发码人指定 `role`）。

留着 `role` 字段但只允许 `student` 会更坏：客户端发 `role: "teacher"` 得到 201，
而 `/auth/me` 回 `role: "student"`——这正是 `ADR-021` §2.2 判定为「静默降级」的形态。
所以 `role` 与 `organization_code` 一起从 `RegisterRequest` 删除，`extra="forbid"`
会把任何还在发这两个字段的客户端当场 422，而不是让它在生产里静默降级。

## 2. 决策

### 2.1 一个字段 `identifier`，三种形状

`LoginRequest` 改为：

```json
{ "identifier": "alice | alice@example.com | 13800000000", "password": "…" }
```

- `identifier` 是**唯一**字段，不接受「同时发 `username` 和 `email`」这种两义写法
  （两种说法一定会有一天互相矛盾，而那天没人知道该信哪个）。
- 过渡期兼容：`validation_alias=AliasChoices("identifier", "username")`，旧客户端的
  `{"username": …}` 仍能登录，但内部只有一个字段。**这是输入别名，不是第二套语义**：
  两者都进同一个解析流程，行为完全一致。等旧客户端清零后再删别名（记在 §5）。

### 2.2 归一化：三列各有各的规则，且都在落库侧统一

| 标识 | 归一 | 校验 |
|---|---|---|
| 用户名 | `strip().lower()`（沿用 `ADR-005` D1） | 3–100 字符、不含空白 |
| 邮箱 | `strip().lower()` | 一个 `@`、两侧非空、域名内不再有 `@`（沿用现有轻校验，不做 RFC 全量） |
| 手机号 | 去掉空白与 `-`，去掉 `+86` / `86` 前缀 | `^1[3-9]\d{9}$`（与 `app/schemas/profile.py::PHONE_PATTERN` 同一个正则） |

两条纪律：

- **归一化必须发生在查重与落库之前**（`ADR-005` 已经为用户名写过这条，本批把它
  推广到三列）：`AuthService` 入口再归一一次（幂等），保证不经 schema 的内部调用也安全。
- **手机号只存 11 位数字**，不存 `+86`、不存分隔符。存两种写法等于让「唯一键」
  只挡住一半的重复。

### 2.3 多命中时的优先级：`username` > `email` > `phone`

`identifier` 是一个字符串，而三个列各自可能命中。规则必须**确定且可解释**，
否则「为什么我今天登不上去」永远查不清。

判定顺序（**先按形状缩小，再按优先级取一行**）：

1. 把 `identifier` 归一成小写串 `raw`；
2. 若 `users.username == raw` 命中一行 → **用它**；
3. 否则若 `raw` 含 `@` 且 `users.email == raw` 命中一行 → 用它；
4. 否则若 `raw` 匹配手机号且 `users.phone == raw` 命中一行 → 用它；
5. 都不命中 → `401`（与「密码错」同一句，不区分，避免账号枚举）。

为什么用户名优先，而不是「邮箱形状优先」：用户名历史上允许任意非空白字符，
**存量库里可能真的有人叫 `a@b`**。若按形状先判邮箱，这个人从今天起再也登不进来，
而且他没有任何办法自救（改名入口都没有）。用户名优先是唯一一种「不伤存量」的顺序。

这条优先级的代价写清楚：一个用户名恰好等于另一个人的手机号时，手机号主人
**不能**用手机号登录（会被用户名那一行截胡）。为了让这种碰撞只可能来自存量数据：

- 新增注册校验：**用户名不得长得像邮箱或大陆手机号**（`username must not look like
  an email or a mobile number`）；新数据永远不会制造这种碰撞；
- 存量碰撞不做数据迁移（第 18 轮裁定 ① 的同一口径：不刷存量），但它是**可解释的**：
  用户仍可用自己的用户名登录，客服也能按 §2.3 的顺序一眼看出发生了什麼。

`phone` 的冲突处理与邮箱一致：`IntegrityError` 里出现 `phone` 就翻成
`PhoneAlreadyExistsError` → `400` + `ErrorMessage.PHONE_EXISTS`。

### 2.4 改密：撤销**全部** refresh token，让所有设备重新登录

`POST /auth/password`，请求体 `{current_password, new_password}`，成功 `204`。

| 规则 | 决定 | 理由 |
|---|---|---|
| 必须验当前密码 | 是 | 拿着**已泄露的 access token** 的人不该能直接改密（token 15 分钟有效，这 15 分钟就是他唯一的窗口） |
| 新密码能否等于旧密码 | 不能（`400`） | 「改密成功」但实际没改，是用户最不该被欺骗的一种成功 |
| 新密码强度 | 与注册同一套（≥8、≤72 字节 UTF-8） | 两处口径必须同一个 `RegisterRequest` 校验器；否则改密能绕过注册的下限 |
| 其它会话 | **全部撤销**（含当前会话） | 改密的全部意义是「把别处的人踢下去」。保留当前会话需要客户端额外提交 refresh token，而「哪一条是当前会话」又是一层要维护的状态；直接全撤 = 前端在改密成功后跳登录页，语义最直白 |
| 当前 access token | 不主动作废，最多再活 15 分钟 | 无状态 JWT 的既有契约（`ADR-004`）；要立刻失效就得引入令牌黑名单，与本批无关 |
| 审计 | `AuditAction.PASSWORD_CHANGE`，`result=ok` | 改密是安全事件，必须能回答「谁在什么时候改过」 |

`204` 而不是返回新令牌对：**改密不是登录**。返回令牌会让「我不知道旧密码但拿到了
一个 access token」这条路径变成永久的账号接管；要求重新登录把这一步关掉。

### 2.5 渐进式强制：数据层可空，动作层拦截（第 18 轮裁定 ① 的落地形态）

组织归属在数据层**永久可空**，本批不动 `users` / `organization_members` 的既有形状。
拦截只发生在「必须依托组织才能完成」的动作上，且**判据只有一处**：

`app/core/action_gate.py`

```python
require_organization(organization_id: str | None, *, action: OrganizationAction) -> str
```

- 返回 `organization_id`（非空）或抛 `ActionRequiresOrganization(action)`；
- `action` 是枚举（`PUBLISH_JOB` …），**文案由调用方按枚举查表**，本模块不带任何
  上屏文案（文案只许住在 `app/core/error_messages.py`，G3 判据）；
- 服务层把 `ActionRequiresOrganization` 翻成自己的域异常（岗位侧是
  `JobOrganizationRequiredError` → `ErrorMessage.JOB_ORGANIZATION_REQUIRED`）。

为什么值得抽一个只有 6 行的模块：与 `app/core/deadline.is_expired` 完全同构——
第 7 轮裁定要求「动作入口必须过同一个判据」，否则每个端点各写一遍 `if not org`，
下一个端点一定会漏。抽出来之后，「哪些动作被拦」是一张**可枚举的表**，
而不是散在若干服务里的 `if`。

**个人版功能一律不受影响**（这是裁定 ① 的另一半，必须有测试守着）：档案读写、
简历生成 / 润色 / 隐私检查、岗位浏览与匹配、面试练习，全部在「无组织」状态下可用。

### 2.6 数据迁移：只加一列，不刷存量

`users` 加 `phone VARCHAR(20) NULL` + `uq_users_phone` 唯一键。

- 存量行全部 `NULL`，唯一索引允许多个 `NULL`，因此**不需要回填、不会撞键**；
- 长度为 20 而不是 11：给「将来加国际号码」留余量，校验仍由应用层按大陆 11 位卡死；
- 存量用户**不需要**补手机号才能登录：用户名仍是第一优先级标识。

## 3. 后果

**好的**：

- 用户可以用自己更容易记住的标识登录，且三列的唯一键都在数据库里；
- 注册入口只剩一条路，与邀请制（`ADR-023`）拼成完整链路：
  注册当散人 → 邀请码加入 → 角色由发码人指定；
- 改密有了真实语义（踢掉所有会话），而不是「改完还能继续用旧会话」。

**代价 / 明确不做**：

- 不支持「用邮箱或手机号找回密码」——本仓没有邮件/短信通道，写一个假入口比不做更坏；
- 不支持改用户名 / 换绑定邮箱或手机（没有端点，`users` 写入面只有注册与改密）；
- 手机号不做短信验证码验证（同上，没有通道）。所以**手机号登录的强度等于
  「知道这个号码 + 知道密码」**，不构成「持有该号码」的证明。这条必须写进
  `docs/API.md`，免得以后有人把它当成二因素。

## 4. 验收判据（每条都先红过）

| 判据 | 用例 |
|---|---|
| 邮箱 / 手机号都能登录，且都能拿到可用 token | `tests/test_login_identifiers.py::test_login_with_email` / `::test_login_with_phone_and_store_it_normalized` |
| 用户名优先于邮箱形状（存量 `a@b` 用户不被锁死） | `::test_username_wins_over_email_for_legacy_accounts` |
| 三列都不命中 → 401，且文案与密码错逐字一致 | `::test_unknown_identifier_is_indistinguishable` |
| 归一化真的落在库上（`+86 138-0000-0000` → `13800000000`） | `::test_login_with_phone_and_store_it_normalized` / `::test_duplicate_phone_is_rejected_even_with_a_different_spelling` |
| 注册不再接受 `role` / `organization_code`（422，不是静默丢） | `tests/test_organizations.py::test_registration_rejects_a_role_field` / `::test_registration_rejects_an_organization_code_field` |
| 注册后 `/auth/me` 是散人（`organization=null`、`role=student`） | `tests/test_organizations.py::test_registration_creates_an_outsider` |
| 改密后旧密码登不上、新密码能登、旧 refresh token 全废 | `tests/test_login_identifiers.py::test_change_password_revokes_every_refresh_token` |
| 当前密码错 → 400，且密码没被改 | `::test_change_password_revokes_every_refresh_token`（改密**之前**先断言两个 400，且之后的成功改密证明旧哈希没动过） |
| 新密码等于旧密码 → 400 | `::test_change_password_revokes_every_refresh_token` |
| 无组织账号可正常用个人版全部功能 | `tests/test_organization_gate.py::test_profile_features_work_without_an_organization` / `::test_job_drafting_and_listing_work_without_an_organization` |
| 无组织账号提交审核 → 400 + 「请先绑定组织」 | `tests/test_organization_gate.py::test_submitting_a_job_without_an_organization_is_rejected` / `::test_joining_an_organization_unblocks_submission` |

## 5. 遗留与下一步

1. `LoginRequest.username` 的**输入别名**待旧客户端清零后删除（本批保留，见 §2.1）；
2. 找回密码 / 改邮箱 / 改手机号需要邮件与短信通道，排到有通道之后，届时另写 ADR；
3. `users.phone` 一旦允许国际号码，`PHONE_PATTERN` 与归一化规则必须同批改，
   否则「校验放过、归一化丢弃前缀」会让同一号码存成两种形状。
