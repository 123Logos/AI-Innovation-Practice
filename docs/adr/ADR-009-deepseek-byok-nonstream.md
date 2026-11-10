# ADR-009：DeepSeek BYOK 非流式生成（AI 只写文案，模板负责排版）

- 状态：已接受。**[部分被取代]** 业务流程已由 [ADR-010](ADR-010-decouple-ai-polish-from-generate.md) 更新为「生成与润色解耦」——本 ADR 的「`/generate` 默认走 AI」与「`use_fallback` 模板降级」两条决策已废止；BYOK Key 生命周期、`deepseek-flash` + 非流式 + `thinking=disabled` + 30 秒预算、日志脱敏与「AI 不产 HTML、排版归模板」仍有效并被 ADR-010 继承。
- 日期：2026-11-04
- 决策人：本项目人类负责人
- 相关：ADR-002（AI 先用 stub）、ADR-006（多份简历）、ADR-007（DIY 排版与主题）

## 背景

Phase 1 的简历生成走 `TemplateResumeGenerator`：字段完整、排版干净，但文案是用户自己写的原话。
课程要求与真实用户反馈都指向同一件事——学生缺的不是排版，而是「把自己做过的事说清楚」的能力，
模板生成器满足不了这一条。ADR-002 当初选择 stub 是为了不打断垂直切片，现在切片已被真实用户验证，
接入真实 AI 的前提成立。

本 ADR 同时是 AGENTS.md §3.2 冻结项「真实 DeepSeek / 任何真实外部 AI 服务调用」的**书面解除记录**：
人类负责人在本轮任务书中明确下达接入指令，本文件即其批准凭证。AGENTS.md §1.5、§3.2 与
`docs/MVP_SCOPE.md` §5、§7 已于 2026-11-04 同步修订（见「后果」§6）。

约束由人类负责人逐条拍板：

1. 唯一 Provider 是 DeepSeek，不做多 Provider 抽象；
2. 用户自带 Key（BYOK），服务端不落库、不写日志、用完即焚；
3. 非流式，模型固定 `deepseek-flash`；
4. AI 失败时**绝不静默降级**成模板——那是「完成度叙事失真」的产品版本。

## 决策

### 1. AI 只产出文案，排版仍由模板负责

`DeepSeekCopyWriter` 返回结构化 JSON（润色后的经历 `description` / `responsibility` 与技能排序），
服务层把它合并回原始输入，再交给现有的 `TemplateResumeGenerator` 渲染并落库。

**不做**「模型直接返回整页 HTML」，四条理由都是可验证的既有契约：

- `ResumePreview.vue` 用 `DOMParser().parseFromString(html).body.innerHTML` 注入、`:384` 用
  `v-html` 渲染。模型产 HTML 等于开一条**存储型 XSS** 通道（`<img onerror>` 会执行）；
  现在这条路径上每个用户字段仍由 `escape()` 兜住。
- 生成物必须与预览 DOM 的类名一一对应（ADR-006/007），否则在线编辑与导出错位。
- 主题（`--paper-accent` / `--paper-rule`）与模块顺序、自定义标题都存在
  `student_profiles` 里，模型不知道也不该知道。
- A4 一页上限（MVP_SCOPE F8）无法由自由 HTML 保证。

合并规则是本决策的安全边界，四条都写进了测试：技能不新增也不丢（模型给的名字必须对得上库里的，
否则丢弃；漏掉的按原顺序补回）；经历必须数量与 `index` 一一对应，否则判为契约违规；
标题与起止时间是库内事实，模型无权改；润色成空白时回落原文。

### 2. BYOK：Key 的生命周期止于一次请求

- 前端存在 `sessionStorage`（键名 `deepseek-api-key`），顶栏「AI 设置」写入，关标签页即失；
  **不写 `localStorage`**，弹窗里也不回填已存 Key（避免屏幕上是明文）。
- 请求头 `X-DeepSeek-Key` 只挂在 `POST /resumes/generate` 上，由 axios 请求拦截器注入。
- 后端在 `app/services/dependencies.py` 的 `get_resume_service(request)` 里取头，
  构造 `DeepSeekCopyWriter(api_key)`；Key 只作为该对象的构造参数存在于本次请求内存中，
  不进数据库、不进日志、不进返回给浏览器的错误信息。
- `app/repositories/` 与所有 ORM 模型**没有**新增任何 Key 字段（本轮零迁移）。

### 3. 调用参数钉死

`model="deepseek-flash"`、`stream=False`、`extra_body={"thinking": {"type": "disabled"}}`、
`timeout=30.0`、`max_retries=0`。

- `deepseek-chat` / `deepseek-reasoner` 两个旧模型名已于 2026-07-24 停用，`.env.example` 同步更正。
- `deepseek-flash` **默认开启思考模式**，不显式关掉就会多烧一段思维链的 token 与延迟。
- SDK 默认重试 2 次，同步接口上就是最坏 3 × 30s = 90s；重试交给前端按钮，不藏在服务端。
- 前端生成请求的超时单独放宽到 45s：`http` 实例默认 15s < 后端 30s 预算，
  按默认值会先把自己掐断并报成「无法连接到服务」。

### 4. 三种失败，三个不同的用户动作

| 情形 | HTTP | `error.code` | 前端动作 |
|---|---|---|---|
| 没带 Key 且没点降级 | 400 | `AI_KEY_MISSING` | 提示去「AI 设置」；前端在发请求前就本地拦住 |
| DeepSeek 判定 Key 无效/欠费 | 401 | `AI_KEY_INVALID` | 提示修 Key；**不刷新令牌、不踢下线** |
| 超时 / 429 / 5xx / 返回不合契约 | 502 | `AI_SERVICE_UNAVAILABLE` | `window.confirm` 询问是否改用基础模板 |

降级由 `ResumeGenerateRequest.use_fallback=true` 显式表达，后端收到它时零外呼。
错误处理器专门注册在 `app/main.py`，而不是在路由里 raise `HTTPException`：通用 4xx 处理器会把
`code` 统一压成 `CLIENT_ERROR`，前端就分不出这三类。401 分支必须先于 JWT 续期逻辑判断，
否则「Key 打错」会表现为「账号被登出」。

### 5. CORS 与日志脱敏

- `CORS_ALLOW_HEADERS` 增加 `X-DeepSeek-Key`。漏这一项的后果只在跨源部署（手机走局域网 IP）
  时出现，本地 Vite 代理同源，测试跑不出来。
- `openai` / `httpx2` / `httpcore` / `httpx` 四个库的 logger 在 `app/main.py` 里压到 `WARNING`：
  SDK 在 DEBUG 级别会把整个 request options（含 `Authorization: Bearer <key>`）打进日志。
  压级别挡的是第三方库，比在业务代码里小心更可靠。
- 三个错误处理器的文案固定，不拼 `str(exc)`：上游异常文本可能含 request_id 或用户 Key。

### 6. 测试不依赖真实网络

`openai` 3.x 的 HTTP 层是 `httpx2`，而 `pytest-httpx` / `respx` 只给 `httpx` 打补丁。实测：
用 pytest-httpx 写的「Mock 测试」会把请求真的发到 `api.deepseek.com` 并拿回线上 401。
因此**不引入任何 HTTP mock 库**，改为在 `copy_writer._build_http_client()` 这个接缝上注入
`httpx2.MockTransport`：拦截发生在传输层，断言的是真实发出去的字节
（`model` / `stream` / `thinking` / `Authorization` 全部可见），且天然满足 CI 不出网的要求。

## 后果

1. 用户必须有 DeepSeek Key 才能用 AI 生成。没有 Key 的人仍然能拿到模板简历，但**需要自己点一次
   「改用基础模板」**——这是刻意的：把默认路径留给了拍板的 AI 优先策略。演示/答辩现场若不想现场
   注册 Key，需要提前在「AI 设置」里配好。
2. Key 由用户提供，服务端不代为保管，也不为 Key 泄露承担存储侧责任；但服务端的**日志脱敏义务**
   是本决策的一部分，已用 `test_api_key_never_reaches_logs` 在全库 DEBUG 配置下钉住。
3. `sessionStorage` 仍可被 XSS 读取，这是明知的取舍：注入面只有一条且字段全转义。想更保守就改成
   每次生成时手输 Key（不存储），代价是体验。
4. DeepSeek 生成的文案不保证事实正确。合并规则从结构上剥夺了模型「新增技能 / 新增经历 / 改时间」
   的能力，但**描述性内容的真假仍由用户自负**——预览页与编辑器的「生成后请自行校对」提示不能删。
5. Key 有效性、真实延迟、真实输出格式都**没有**在本轮验证（本轮不持有任何真实 Key）。
   Mock 测试能证明「我们发出去的请求是对的」和「各种失败被正确分流」，不能证明 DeepSeek 会按
   预期返回。首轮真实 Key 的手测是本切片验收的一部分。
6. AGENTS.md §1.5、§3.2 与 `docs/MVP_SCOPE.md` §5 的「真实 DeepSeek 不接入」条目已于 2026-11-04
   按本节凭证同步解除（`MVP_SCOPE.md` 文末变更记录已补一行）。§7 的耗时口径同日定稿为
   「模板路径 ≤ 2 秒，AI 路径 ≤ 30 秒（前端超时 45 秒）」，原先那句容易引起误解的「≤ 10 秒」已删除；
   `MVP_SCOPE.md` §3 用户主路径第 3 步的返回时限同日同步改为 30 秒（模板路径 ≤ 2 秒）。
   `ADR-002` 的状态行同步标注为部分被本 ADR 取代。
7. 旧占位类 `DeepSeekResumeGenerator`（`# PHASE-2: not wired`）已删除：它的「生成器」定位与
   本 ADR 的「文案写手」定位冲突，留着会让人以为 AI 该返回 HTML。
8. 既有 22 个模板路径用例改为显式携带 `use_fallback=true`（经由新增的 `_payload()` helper）。
   它们验的是排版契约，与 AI 无关，给它们塞假上游只会让「模板测试」其实测的是 AI 路径。

## 替代方案

- **接 OpenAI 或其它 Provider**：不采用。课程要求与用户都在 DeepSeek 生态，多一个 Provider 就多一套
  Key 管理与格式适配；也不做「Provider 抽象层」，因为只有一个实现时抽象是负债。
- **服务端存 Key（加密落库）**：不采用。落库就多一处泄露点（备份、日志、拖库），且用户难以确信
  「用完即焚」；BYOK 的价值恰恰是服务端不留凭据。
- **流式（SSE）输出**：不采用。生成物只有约 300 字的文案，流式带来的主要收益是「看起来更快」，
  代价是 `ResumeGenerator` 契约要改成增量、预览要做半截 HTML 渲染、错误处理要区分「流中断」。
  非流式 + 明确的「AI 生成中，请稍候…」状态已经满足体验需要。
- **模型直接返回整页 HTML**：不采用，理由见「决策 §1」四条契约与 XSS。
- **后端静默降级到模板**：不采用（人类负责人明确否决）。用户以为拿到的是 AI 结果，实际是模板，
  是 AGENTS.md 第 2 节点名的「完成度叙事失真」。
- **用 `pytest-httpx` / `respx` Mock**：不采用，实测拦不住 `httpx2`，会变成「测试真的打线上 API」。
- **在 `DeepSeekCopyWriter` 里同时负责渲染 HTML**：不采用。那会让 integrations 层同时握有文案与
  排版两套契约，主题/顺序/DOM 对齐都要在两个地方维护。
