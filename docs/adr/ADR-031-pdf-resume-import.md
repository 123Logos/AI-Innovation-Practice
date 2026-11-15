# ADR-031: 导入外部 PDF 简历（pypdf 抽取 + DeepSeek 结构化）

- 状态：已采纳（Accepted，ADR 先行，代码随本文件同分支落地）
- 日期：2026-11-15
- 关联需求：FR-01（档案填写）、FR-03（AI 只产出可编辑建议）；`ADR-009`/`ADR-010`（BYOK 与「哪些请求能带 Key」）、`ADR-011` 2.A/2.F（纯计算端点的同构先例）、`ADR-014`（解冻不覆盖 BYOK 逐条同意）
- 关联裁定：第 28 轮老板指令「引入 pypdf 或 pdfplumber」「新增 `POST /resumes/import-pdf`（需登录，需 `X-DeepSeek-Key`）」「限制 5MB」「提取前 3–5 页」「无 Key 或 AI 失败返回 422」「将 `/resumes/import-pdf` 加入前端的 BYOK 白名单」
- 前置：`ADR-015`（版本链与 `is_head`）、`ADR-017`（上限与截断必须可见）

## 1. 背景：这一批要解决的三件事

1. **学生手上已经有一份 PDF**（求职时改过几十轮的成品），而我们的档案表单要他从零重填。
   重填一次的代价比第一次填还高——他已经知道内容长什么样，只是没有结构。
2. **这是一条「AI 替用户做决定」风险最高的路径**：整份档案从外部进来，用户没有逐格确认的机会，
   除非我们把「不覆盖、不落库、看得见」做成结构性的，而不是靠一句提醒。
3. **两个新依赖面必须过一次书面决策**：① 引入 PDF 解析库（`AGENTS.md` §4.6「技术栈新增依赖」）；
   ② 一条新的 BYOK URL（`AGENTS.md` §3.2 末行明确这条**不在** 2026-11-08 全面解冻的范围内，
   每加一条都要老板逐条书面同意——第 28 轮指令就是那次同意，本 ADR 负责把「为什么是这一条、
   且只有这一条」写死）。

## 2. 决策

### 2.1 数据流：全程不落库，结果只回给草稿

```
用户选文件（前端 accept=application/pdf）
     │  dirty 时先弹「导入将覆盖当前未保存的修改」确认
     ▼
POST /api/v1/resumes/import-pdf   multipart/form-data: file
     │  ① 体积 ≤ 5 MiB、② 魔数是 %PDF、③ 只读前 3 页文本（pypdf，本地，不出网）
     ▼
DeepSeek（用户自己的 Key，非流式，thinking disabled，max_tokens=2000）
     │  严格 JSON：full_name / contact / target_role / educations[] / experiences[] / projects[] / skills[]
     ▼
服务层归一：projects[] 并入 experiences[]、按前端上限裁剪（技能 8 / 经历 3 / 教育 5）
     │  ★ 被裁掉的条数原样回传（dropped），一句都不许静默
     ▼
响应 = { profile: {...}, meta: {pages_total, pages_used, text_chars, prompt_version, dropped} }
     │
     ▼
前端把 profile 合并进 Pinia 草稿 → dirty = true → 左侧表单 + 右侧实时预览可见
     │
     ▼
用户点「保存修改」才 PUT /profiles/me 落库（本端点一个字节都不写库）
```

**为什么不落库**：与 `/resumes/polish`（`ADR-010`）和 `/jobs/extract-skills`（`ADR-011` 2.A）
同族——AI 的产物先回显、由用户逐条删改、点保存才进库。一旦服务端直接写库，
「AI 把教育经历写成三段假的」这件事就没有回退点：库里已经没有旧版本了。

### 2.2 依赖：`pypdf`，不选 `pdfplumber`；`multipart` 要 `python-multipart`

| | pypdf | pdfplumber |
|---|---|---|
| 系统级依赖 | 无（纯 Python） | 依赖 `pdfminer.six`，且推荐装 Pillow 做版面 |
| 体积/安装面 | 单包 | 多层 |
| 文本抽取质量 | 文字型 PDF 足够 | 更好（表格、坐标） |
| 我们要的能力 | **只要纯文本** | 用不上它的版面/表格 |

我们的下游是一个「读文本、吐 JSON」的模型，不需要版面与坐标；而**扫描版 PDF 谁都抽不出字**
（没有文字层），换库不改变这件事，所以选安装面小的那一个。许可同为 BSD 系。

**第二个新依赖是老板那条接口契约带进来的，不是我们自己加的**：`multipart/form-data`
的解析在 Starlette / FastAPI 里必须由 `python-multipart` 完成，没装它时
`request.form()` 直接抛 `AssertionError`。路由因此是 `Annotated[UploadFile, File(...)]`
（`app/api/v1/resumes.py`），而不是把 PDF 塞进 JSON 里 base64——后者会让一份 5 MB 的文件
变成一次约 6.7 MB 的 JSON 体，还把「这是文件」这件事从协议层挪进了约定。

两条依赖的来源（2026-11-15 实测并报备）：本机对清华镜像 `/packages/` 的文件下载被拒
（直连与走代理都是 403，而 `/simple/` 索引仍返回 200——所以**解析能过、下载必败**），
出口 IP 是杭州 CHINANET、系统代理已确认绕过。`pyproject.toml` 的 `index-url`
**保持清华不变**（其余依赖的来源一格没动），只在本地把这两个 wheel 按镜像自己公布的
sha256 逐字节校验后从 PyPI 官方源装进 venv。`uv.lock` 里这两条仍然记的是清华的 URL 与
镜像公布的哈希，所以 CI 与干净环境的安装路径没有变化。

### 2.3 限制：三条封顶，每条都对应一种故障

| 限制 | 取值 | 为什么是这个数 |
|---|---|---|
| 文件体积 | **5 MiB** | 老板指定。超过即 422，不先读进内存再判（`UploadFile` 流式读，累计超限立刻中止） |
| 页数 | **前 3 页** | 简历的正常长度是 1–2 页；3 页已经能兜住「一页纸简历 + 附录」。多页论文式 PDF 全文进 prompt 会挤掉档案本身该有的 token，而模型对超长输入的首尾注意力最差 |
| 送模型的字符数 | **12000** | 中文约 1 字 ≈ 1 token 量级，12000 字 + 输出 2000 token 仍在 `deepseek-flash` 的上下文内且 30 秒可回（`ADR-009` 的非流式超时口径） |

`pages_total` 与 `pages_used` 都回传：截了哪几页要能说出来（`ADR-017` 的「截断必须可见」）。

### 2.4 输出契约与归一（服务层，不在 integrations）

模型必须回**严格 JSON**（一个对象，无围栏也接受；有围栏剥围栏；前后有散文取第一个平衡的 `{}`
——全部复用 `copy_writer.py` 的 `_FENCE` / `_first_object`，不另写一份解析器）。

字段与前端 `ProfileDraft` 逐格对齐：

| 模型字段 | 归一后去处 | 上限与规则 |
|---|---|---|
| `full_name` | `full_name` | 裁到 100 字 |
| `contact` | `contact` | 只留数字；不是大陆 11 位则**置空**并在响应里说明（后端 PUT 会 422，不该由 AI 造一个非法值） |
| `target_role` | `target_role` | 裁到 200 字 |
| `skills[]` | `skills[]` | 名字 NFKC+casefold 去重，≤ 8 条，超出计入 `dropped.skills` |
| `experiences[]` | `experiences[]` | 与 `projects[]` 合并（经历在前、项目在后），≤ 3 条 |
| `projects[]` | 并入 `experiences[]` | 前端没有独立的「项目」区块——纸面上就是「项目经历」一节，加一格等于加一个区块 |
| `educations[]` | `educations[]` | ≤ 5 条；`edu_start` 解析不出年月则整条丢弃并计入 `dropped.educations`（后端该列必填） |

**归一留在服务层**：`integrations/` 不知道前端上限，也不知道哪一格是必填（`AGENTS.md` §4.1 的分层方向）。

### 2.5 错误与降级：一个状态码，五个码，各给一句修得动的话

老板指定「无 Key 或 AI 失败返回 422」。本端点因此**统一 422**，用 `code` 分叉——
前端按 `code` 决定给不给「点此配置 AI 密钥」那行链接（第 25 轮的 `AiKeyHint`），
按状态码分叉会把这些情况混成一锅。

| `code` | 触发 | 上屏文案给的下一步 |
|---|---|---|
| `PDF_TOO_LARGE` | > 5 MiB | 换小一点的文件 |
| `PDF_INVALID` | 魔数不是 `%PDF` / 抽不出任何文字（扫描版、图片版） | 手动录入，或换有文字层的文件 |
| `AI_KEY_MISSING` | 请求没带 `X-DeepSeek-Key` | 去「设置」填 Key（带 hint） |
| `AI_SERVICE_UNAVAILABLE` | 超时 / 上游 4xx-5xx / 空返回 | 稍后再试一次 |
| `AI_OUTPUT_INVALID` | 输出不是合法 JSON 或结构不符 | 再试一次，或手动录入 |

三条刻意的选择：

1. **不降级成模板**。生成简历那条路可以纯模板兜底（`ADR-009`），导入不行——
   兜底等于凭空造一份「看起来填好了」的档案，而它一个字都没读过 PDF。
2. **Key 无效（`AuthenticationError`）归到 `AI_KEY_MISSING`**，文案仍指向设置页：
   用户能修的只有那一格（与 `ADR-013` §6.5 匹配端点的分档同一理由）。
3. **异常文本一律不上屏**：原始 `str(error)` 只进日志，句子住在 `app/core/error_messages.py`
   （G3 的既有纪律）。

### 2.6 BYOK 白名单：扩一条，且只扩这一条

`frontend/src/api/client.ts::BYOK_KEY_PATHS` 增加 `POST /resumes/import-pdf`（精确匹配路径 + 方法）。

- 不新增 pattern：这条路径没有路径参数（PDF 还没有 id），精确匹配比正则更难被拼错的路径蹭到。
- `byok.spec.ts` 的反向用例同步扩：除白名单那几条外，任何请求（包括同前缀的
  `POST /resumes/generate`）都不许带 `X-DeepSeek-Key`。
- Key 的生命周期一格没改：只来自请求头、只在本次请求内存中、不落库、不写日志、
  不进异常消息（`ADR-009`）。

### 2.7 数据模型：零新表、零新列、零迁移

导入的结果**不进库**（§2.1），所以本批不建任何表。将来若要留「导入历史」，那是新的一张表 +
新的 ADR（`AGENTS.md` §4.2），不是在这条端点上顺手加一列。

### 2.8 隐私边界

- PDF 原文只在内存里：不落盘、不缓存、不上传第三方（DeepSeek 是**用户自己的**凭据与账号）。
- 出网的只有抽取出的文本（≤12000 字）+ Prompt。用户自己决定要不要导——这与润色
  把草稿文案发给模型是同一条边界，不新增暴露面。
- 日志记：页数、字符数、prompt 版本、request_id、错误码。**不记**文本内容与档案字段值。

## 3. 后果

**正面**

1. 学生从「重填一遍」变成「核对一遍」，档案填写成本从几十格降到一次确认。
2. AI 产物没有落库路径，最坏情况是「白填一次草稿」，不是「库里多了假数据」。
3. 截断、丢弃、失败三类都必须在屏幕上说出来，静默降级的老毛病没有新入口。

**代价**

1. 多一个第三方解析库依赖（`pypdf`），纯 Python、BSD，安装面小但仍是供应链上的一格。
2. 扫描版 PDF 抽不出字——这是格式的物理限制，不是 bug，但用户会当成 bug，
   所以 `PDF_INVALID` 的文案必须把「这份 PDF 里没有可复制的文字」说清楚。
3. AI 抽取质量决定于模型本身：`contact` 可能被写进经历正文、`projects` 与 `experiences` 会混。
   归一规则（§2.4）只能兜住形状，兜不住语义——所以**回填后必须用户确认才落库**。
4. 这条端点会消耗用户自己的 AI 额度，且一次导入是一次长请求（非流式，30 秒上限）。
   限流沿用 `/resumes/polish` 那一档，不单独放宽。

## 4. 替代方案（都想过，都不选）

| 方案 | 为什么不选 |
|---|---|
| 服务端直接落库（导入即保存） | 违反 `ADR-010` 立的「AI 产物先回显、用户点保存才落库」；且没有回退点 |
| `pdfplumber` | 我们要的能力只有「拿纯文本」，它带来的是版面/表格与更重的安装面 |
| OCR（扫描件也读） | 引入 Tesseract 级系统依赖 + 图像管线，MVP 阶段不值；扫描版走手动录入 |
| 不用 AI，规则式解析（正则找邮箱/电话/学校） | 简历版式千种，规则库会变成一个长期维护的黑洞；且中文姓名/公司/学校切分本身是模型强项 |
| 前端直接调 DeepSeek（像 ASR/TTS 那样浏览器直连） | PDF 文本要先在后端抽（pypdf 只有 Python 侧），前端拿不到文本；且 multipart 出网带 Key 会多一个注入面 |
| 上传即建一份新简历（`POST /profiles`） | 「导入」与「新建」是两个动作；导入先填草稿，用户保存时自然走既有 PUT，不需要第二条写路径 |

## 5. 验收判据

1. 后端：假 PDF（文字型）上传 → 200 且 `profile` 各格与模型返回一致、`meta.dropped` 在无截断时全 0。
2. 后端：>5 MiB → 422 `PDF_TOO_LARGE`；非 PDF 字节 → 422 `PDF_INVALID`；无 Key → 422 `AI_KEY_MISSING`；
   模型返回乱码/非 JSON → 422 `AI_OUTPUT_INVALID`；上游超时 → 422 `AI_SERVICE_UNAVAILABLE`。
3. 后端：模型返回 12 条技能 → 只留 8 条且 `dropped.skills == 4`（不静默）。
4. 后端：本端点**不写任何业务表**——用「导入前后 `student_profiles` / `resumes` 行数不变」钉住。
5. 前端：`/resumes/import-pdf` 在 `BYOK_KEY_PATHS` 里；同前缀的 `POST /resumes/generate` 不带 Key（反向用例）。
6. 前端：`dirty == true` 时点导入必先弹确认；确认后走上传；成功回填后 `dirty` 仍为真且**不发 PUT**。
7. 教师端导出（同批修复）：一个学生两份有效简历 → 清单两条、各取其链头；回收站那份不出现，
   点名 id 批量下载也进 `skipped`。

## 6. 遗留与下一步

1. **导入历史不记录**：没有表就没有「上周导入过哪几份」。要做先补一张表 + 一篇 ADR。
2. **多文件批量导入不做**：一次一份，用户看得见每一份的结果；批量会把「核对」这一步压没。
3. **版面还原不做**：导入的是**结构数据**，纸面由我们自己的排版引擎生成。用户如果要求
   「长得和原 PDF 一样」，那是另一件事（也超出 `ADR-007` 的纸面契约）。
4. **`projects` 与 `experiences` 的语义合并**是形状层面的：模型把实习写成 project 时，
   合并顺序会让它排在项目之后。若后续观察到普遍错位，改法是 Prompt 侧收紧，不是加第二个区块。
