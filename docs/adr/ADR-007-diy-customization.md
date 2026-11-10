# ADR-007：DIY 个性化（纸张 / 配色 / 排版）的技术方案选型（草案）

- 状态：**草案（待人类拍板，尚未选定任何方案）**
- 日期：2026-11-03
- 决策人：本项目人类负责人
- 关联需求：`docs/PHASE2_BACKLOG.md` §二「简历 DIY 拖拽排版」+ 本轮新增的「纸张 / 配色」需求
- 关联文档：`docs/adr/ADR-006-multiple-resumes-design.md`（D4.1 数据模型先例）、
  `docs/DATABASE.md` §5（索引与 JSON 使用原则）、
  `docs/adr/ADR-004-auth-jwt.md`（归属语义）

> **本文档不替人类做决定。**
> 每个决策点只提供「代码事实 → 选项 → 代价」三样东西。
> 标 **【硬约束】** 的是技术上绕不过去的依赖；标 **【待拍板】** 的是偏好问题，收在第 13 节清单。
>
> **请先读第 2 节。** 它纠正了五处会直接影响选型的事实误解：
> 1. 项目里**从来没有 `@page`**，所谓「A4」只是一条 `aspect-ratio`，打印时纸张和边距都是浏览器给的；
> 2. 预览区的「A4 · 1 页」是**硬编码字符串**，「页数」这个概念在代码里根本不存在；
> 3. 纸面版式色是 **8 个硬编码色值**，没有 CSS 变量，做主题色等于先做一次重构；
> 4. 后端生成的 `content_html` **不带任何 `<style>`**，是「半成品」而非自包含文档；
> 5. 整份简历的「顺序」是**三处硬编码**（生成器模板、生成器函数体、预览模板），
>    而「模块顺序」和「数据数组顺序」是两件事——混为一谈会毁掉教育经历的倒序语义。

---

## 1. 背景与范围

本轮的原始需求（人类口述，整理后）：

> **纸张大小切换、配色自定义，以及简历 DIY 拖拽排版（模块顺序调整、自定义标题）。**

这三件事被放在一句话里，但它们的技术性质完全不同，必须拆开评估：

| 子需求 | 改动性质 | 触及层 | 风险 |
|---|---|---|---|
| 纸张大小 | 视觉常量，1 个枚举 | CSS + 1 列 + 生成器包裹层 | 低，但会牵出「打印边距」和「页数」两个旧债 |
| 配色 | 视觉常量，1 个枚举（或 1 个 hex） | CSS 变量化（重构）+ 1 列 + 生成器 | 中低，真正的坑是**双端色板漂移**与**对比度** |
| 模块顺序 + 自定义标题 | **数据结构变更** | 1 列 JSON + 前后端两套模板 + 交互（拖拽库） | **高**，是本文档 80% 篇幅所在 |

结论先行（论证见后）：**前两者是「换皮肤」，后者是「换骨架」。换皮肤可以一次做完；换骨架必须先把数据模型钉死，再谈交互，最后才谈拖拽。**

本轮任务：只读评估 + 起草 ADR。
**不写业务代码、不改库结构、不新增迁移、不安装依赖、不 commit。**

---

## 2. 只读核实结论（代码事实，非推测）

### 2.1 纸张：从来没有 `@page`，所谓 A4 只是一条 `aspect-ratio`

`frontend/src/components/ResumePreview.vue`：

```css
.paper { aspect-ratio: 210 / 297; min-height: 720px; overflow: auto; padding: 56px 52px;
         color: #263330; background: #fff; box-shadow: 0 14px 36px #2633301c; }
```

全仓搜索 `@page`：**`frontend/src` 下零命中**（`dist/` 和 `node_modules/` 里的不算）。
也就是说：

1. **屏幕上的「A4」是算出来的**：宽度由栅格决定（`ResumeEditorView.vue:85` 的
   `grid-template-columns: 340px minmax(420px,1fr) 220px`），高度 = 宽度 × 297/210。
2. **打印时的「A4」是浏览器给的**：没有 `@page` 就等于「用浏览器默认纸张 + 默认边距」。
   于是打印结果实际是 **三层边距叠加**：浏览器默认页边距（约 10mm）
   + `.paper` 的 `padding: 56px 52px`（≈14.8mm / 13.8mm）+ 用户打印机自身的不可打印区。
3. **`min-height: 720px` 会在窄屏破坏 A4 比例**。算术可验：`min-height` 胜出后，
   纸面宽 420px 时高度被顶到 720px，实际比例 0.583，而 A4 是 0.707；
   拐点约在 `720 × 210/297 ≈ 509px`，宽度低于它就**不是 A4 了**，
   但界面上仍写着「A4」（见 2.2）。
4. `.paper` 是 `overflow: auto`：**内容超长是在纸内部滚动，不是翻页**。
   屏幕上永远只看到「一张」纸。

> **推论**：现在这个「A4 预览」是一个近似值。一旦允许切纸张（Letter / A5），
> 继续用 `aspect-ratio` + 固定 px padding 的做法会立刻失效——
> 因为 padding、字号、行高全是 px，换纸张只改比例而不改这些，等于换了个裁切框。

### 2.2 「A4 · 1 页」是硬编码字符串，页数从未被测量

`ResumePreview.vue:207`：

```html
<span class="page-count">A4 · 1 页</span>
```

`StylePanel.vue:82-84`：

```html
<div class="status-row"><span>纸张</span><strong>A4</strong></div>
<div class="status-row"><span>页数</span><strong>1 页</strong></div>
<div class="status-row"><span>配色</span><strong>青绿</strong></div>
```

三行全是字面量。没有任何地方量过 `scrollHeight`，也没有任何地方知道「这一页装了多满」。

**这不是一个小瑕疵，而是本轮的前置债**：

- 用户把最长的「项目经历」拖到最上面（D5.2 要做的功能），分页点会移动，
  但界面上「1 页」不会变——**功能一上线，这个标签立刻开始说谎**；
- 换到 Letter（比 A4 矮 8.5%，见 2.7）时，「1 页」更可能已经不准。

### 2.3 版式颜色是 8 个硬编码色值，没有 CSS 变量

`ResumePreview.vue:561-592` 的非 scoped 样式块（这一段同时负责「实时预览」和「已生成」的观感，
是「所见即所得」的实现方式），里面硬编码了 8 个颜色：

| 变量（建议名） | 现值 | 用在哪 | 对比度 vs 白底（实测） | 能当正文色吗 |
|---|---|---|---|---|
| `--paper-accent` | `#527873` | `h2` 栏目标题 | **4.89:1** | 能（WCAG AA 4.5 刚刚过） |
| `--paper-rule` | `#6b8f8a` | 页眉下边框 2px | 3.55:1 | 否（仅够「非文本 UI」3:1） |
| `--paper-ink` | `#263330` | 姓名、各级标题 | 13.14:1 | 能 |
| `--paper-body` | `#52635f` | 联系方式、经历描述、教育 meta | 6.35:1 | 能 |
| `--paper-muted` | `#687875` | 日期 | 4.63:1 | 勉强（低于 6，长文本慎用） |
| `--paper-faint` | `#8fa5a1` | 分隔符 `·` `｜` `—` | 约 2.7:1 | 否（装饰） |
| `--paper-line` | `#b8c8c4` | 技能下划线、空占位 | **1.74:1** | 否（纯装饰） |
| `--paper-paper` | `#ffffff` | 纸面底色 | — | — |

> 对比度用 WCAG 相对亮度公式实算（见第 5.4 节，可复算）。
> 这张表的意义：**主题色不是「随便挑一个好看的颜色」，它必须落在「能当正文」的那一档**，
> 或者只能用在 `h2` / 边框这类不承担可读性的位置。

### 2.4 生成物不自包含：后端只产 `body`，不带任何 `<style>`

`app/integrations/resume_generator.py::TemplateResumeGenerator`：

```python
self._template = template or (
    '<html><head><meta charset="utf-8"><title>{title}</title></head><body>'
    '<header class="paper-header">'
    '<h1 class="resume-name">{full_name}</h1>'
    '<p class="contact">{contact_items}</p>'
    "</header>"
    "{education_section}"
    "{skill_section}"
    "{experience_section}"
    "</body></html>"
)
```

`head` 里只有 `meta` 和 `title`，**一行 CSS 都没有**。
它之所以看起来对，是因为前端把 `content_html` 塞进了 `.paper` 里：

`ResumePreview.vue:19-23`：

```ts
const generatedBody = computed(() => {
  const html = store.generatedHtml
  if (!html) return ''
  return new DOMParser().parseFromString(html, 'text/html').body.innerHTML
})
```

**两个直接后果**：

1. 把 `content_html` 单独存成 `.html` 打开，是**无样式的裸 HTML**——
   未来做「导出 HTML」「简历分享页」时，必须先补一步样式内联；
2. 主题色的落点因此有**两个**：前端 CSS（管预览/打印）、生成器 HTML（管导出）。
   只做一个就会出现「预览是酒红、导出是青绿」。（详见第 5.6、7.2 节）

补充事实：`resumes` 表只有写入路径。
`app/api/v1/resumes.py` 只有 `POST /generate` 一个端点，**没有读接口**，
所以 `content_html` 目前是「只写不读」——这也意味着主题做进 HTML 之后，
在出现读接口之前**没有任何自动化手段能验证它是对的**。

### 2.5 顺序与标题是「三处硬编码」

| 位置 | 形态 | 改顺序要动什么 |
|---|---|---|
| `resume_generator.py:57-59` | 模板里三个占位符 `{education_section}{skill_section}{experience_section}` | 调整占位符顺序 |
| `resume_generator.py:84-133` | 三段各自 `if 有内容才生成`，标题写死 `技能` / `项目经历` / `教育经历` | 改三个 `f'<h2>…</h2>'` |
| `ResumePreview.vue:271-409` | 三个 `<section class="paper-section">` 顺序写死 | 调整三个 `<template>` 块的先后 |

三处必须**同时**改，且必须保持同序，否则「实时预览」和「已生成」会不一致。
这条约束今天靠注释维持（`resume_generator.py:36-38` 明确写了「必须与 `ResumePreview.vue` 的
DOM 结构、CSS 类名一一对应」），没有任何测试或类型系统在守它。

**【硬约束】** 任何「顺序」能力都不能只改一处。

### 2.6 内容量已有硬上限：溢出今天是低频，明天是常态

- 技能 ≤ 8（`ProfileUpsertRequest.skills` `max_length=8`）
- 项目经历 ≤ 3（`max_length=3`）
- 教育经历 ≤ 5（`max_length=5`）

这是为什么「A4 · 1 页」至今没被戳穿——内容根本撑不满。

**但要注意一个反直觉的点**：拖拽改顺序**不会增加内容**。
把「项目经历」拖到最上面，总高度不变，变的只是**分页点落在哪**
（以及会不会出现「标题在页尾、正文在下一页」这种孤行）。
所以第 9 节的结论是「加 CSS 分页规则」而不是「限制一页」——理由在这里。

### 2.7 纸张尺寸的真实版面差（算术，不是感觉）

纸面宽度固定为 720px（1440 视口下栅格算出）时：

| 纸张 | 尺寸 mm | 比例 | 同宽时高度 | 相对 A4 |
|---|---|---|---|---|
| A4 | 210 × 297 | 0.7071 | 1018px | — |
| Letter | 216 × 279 | 0.7742 | 932px | **−86px（−8.5%）** |
| Legal | 216 × 356 | 0.6067 | 1187px | +169px（+16.5%） |
| A5 | 148 × 210 | 0.7048 | 1021px | 面积仅 50%，字相对放大 √2 倍 |

**Letter 比 A4 矮 8.5%**：同一份内容，在 Letter 上更容易溢出。
这正好说明第 2.2 节的判断——换纸张功能一上线，页数就必须是测量值。

### 2.8 前端没有测试框架（这条会反复咬人）

`frontend/package.json` 的 `devDependencies` 里没有 vitest / jest / @vue/test-utils，
`scripts` 只有 `dev` / `build` / `typecheck` / `lint`。

**对选型的直接影响**：任何「前端与后端各算一遍」的逻辑（例如主题色派生、排版校验）
都**无法用测试保证两端一致**。第 5.3 节因此倾向于「只在后端算」或「只用静态预设」。

---

## 3. 决策一：样式数据挂在谁身上

### 3.1 三个候选

| 方案 | 形态 | 语义 |
|---|---|---|
| **A. 跟简历** | `student_profiles` 加 `paper_size` / `theme` / `layout_config` 三列 | 每份简历一套外观，互不影响 |
| **B. 跟用户** | `users` 或独立 `user_settings` 表 | 全站一套外观 |
| **C. 两级** | 用户级默认值 + 简历级可覆盖 | A 的超集 |

### 3.2 为什么 A 是唯一合理的起点

1. **需求本身就这么说**：人类原话「不同岗位可以投不同风格」。
   投国企用墨黑、投设计岗用藏青，这是**同一用户在同一时刻的两种需求**，
   用户级配置在语义上就表达不了。
2. **D4.1 已经把「简历」确立为聚合单位**：`copy_from_id` 深拷贝整份内容、
   回收站以简历为粒度、顶栏切换以简历为单位。样式跟着简历走，与既有心智一致。
3. **B 是数据事故制造机**：用户在「投算法」里把颜色改成酒红，
   「投后端」那份也变了——用户不会认为这是「设置」，会认为这是 bug。
4. **C 的成本现在不值**：多一张表或 3 个 `users` 列 + 一套「未设置则继承」的解析链，
   换来的只是「改一次全部生效」。真实出现这个诉求时再加，
   加成 **C 的形态**（简历列为 NULL 时回落到用户默认），不需要改 A 的结构。
   ——这是 A 相对 C 的关键优势：**A 是 C 的子集，可以无痛升级**。

### 3.3 【硬约束】样式字段必须进 draft，且必须进 `comparableOf()`

这条极易漏，且漏了就是「改了颜色不生效/会丢」：

- `frontend/src/stores/resume.ts::DEFAULT_LAYOUT_SECTIONS` `ProfileDraft` 是草稿的形状；
- `resume.ts:436` `comparableOf()` 决定「本地草稿是否值得提示恢复」——
  它把 draft 投影成一串可比较的字符串，**不包含的字段等于不存在**；
- `resume.ts:1150-1157` 自动存草稿的 `watch(draft, …, { deep: true })`
  只在 `dirty` 为真时才写。

若把 `theme` 放在 draft 外面（例如单独的 `ref`）：

- 改颜色 → `dirty` 仍是 false → **不写本地草稿**（刷新即丢）；
- 切简历 → `applyDraft()` 只重置它管的字段 → **颜色残留到下一份**。

**结论：`paper_size` / `theme` / `layout_config` 三者都必须落在 `ProfileDraft` 内，
并同步加进 `comparableOf()` 的投影、`toUpsertPayload()` 的载荷、`applyDraft()` 的赋值。**

---

## 4. 决策二：纸张尺寸

### 4.1 候选集

| 候选 | 支持 | 理由 |
|---|---|---|
| A4（210×297） | ✅ 必做 | 中国大陆招聘事实标准，ATS 与打印店都按它 |
| Letter（216×279） | ✅ 建议做 | 海外投递 / 北美 ATS；也是本轮唯一能验证「换纸张真的生效」的第二档 |
| A5 | ❌ 不建议 | 面积只有 A4 一半，招聘系统不接收；会诱导用户做出无法投递的简历 |
| 自定义 mm | ❌ 不建议 | 打印店/ATS 均不支持非标尺寸；只会制造「看着好、投不出去」的产物 |
| Legal | ⚠️ 可选 | 北美在用，但比 A4 高 16.5%，溢出方向相反，可作为第三档的备选 |

**建议先只做 A4 + Letter 两档**：一个是本地标准，一个是海外标准，
且一高一矮正好覆盖两个溢出方向。**纸张种类越多，版式回归面积越大**（每种纸都要验证一遍）。

### 4.2 三条实现路径

#### P1：只加 `@page`，纸面继续 px 流式（最小改动）

```css
@page { size: A4; margin: 0; }
.paper { width: 210mm; height: 297mm; padding: 14mm 13mm; }  /* mm 取代 px */
```

- 改动：`ResumePreview.vue` 两处（`.paper` 规则 + `@media print`），
  加一个动态 `<style>` 注入 `@page size`。
- 优点：改动最小，`contenteditable` 与现有栅格完全不受影响。
- 缺点：**不是真 WYSIWYG**。屏幕上纸面宽度由栅格决定（1440 视口下 720px），
  而 A4 在 96dpi 下是 794px；字号是 px，所以**屏幕上看到的密度比打印密度小**
  （屏幕上 12px 字占纸宽的 1/60，打印时占 1/66… 具体比值随视口变化）。
  窄屏时纸面被压到 420px，同一份内容看起来更挤，打印出来反而更松。

#### P2：mm 定尺 + 外层 `transform: scale()`（真 WYSIWYG）★ 推荐

```css
.paper-stage { --paper-zoom: 1; height: calc(297mm * var(--paper-zoom)); }
.paper { width: 210mm; height: 297mm; transform: scale(var(--paper-zoom));
         transform-origin: top left; }
```

- 纸面**永远按真实 mm 排版**，字号 px 与打印完全 1:1；
  屏幕放不下就用 zoom 整体缩小（只缩显示，不改排版）。
- 优点：所见即所得成立；页数测量变简单（`scrollHeight / 297mm`）；
  窄屏不再破坏比例（缩的是整体，不是比例）。
- 缺点：
  1. `transform` 会创建新的包含块，**内部的 `position: fixed` 会失效**——
     当前 `.paper` 内只有 `position: absolute` 的水印，安全；但以后加浮层要注意；
  2. **与拖拽库冲突**：sortablejs 用 `getBoundingClientRect()` 算落点，
     缩放后判定会偏（见第 8.4 节的交叉影响，这是本文档最容易被忽略的一处）；
  3. 需要额外维护 zoom 值（ResizeObserver 观察容器宽度）。

#### P3：完全 mm 排版 + 字号用 pt（最正确，改动最大）

把 `font-size: 12px` 全部换成 `10.5pt` 之类的绝对单位，纸面与外层都用 mm。
- 优点：打印保真度最高，`pt` 是印刷单位，不受 DPI 影响。
- 缺点：要重排整个 `ResumePreview.vue` 非 scoped 样式块（约 30 行），
  且要重新校所有间距；与现有 px 体系的混用期最容易出错。
- **不建议本轮做**，但如果 P2 落地后发现打印仍有偏差，P3 是下一步。

### 4.3 三方案对比

| | 改动面 | 屏幕=打印 | 窄屏不破坏比例 | 与拖拽冲突 | 风险 |
|---|---|---|---|---|---|
| P1 | 小（2 处 CSS + 1 处注入） | ❌ | ❌ | 无 | 低 |
| **P2** | 中（纸面容器 + zoom + 测量） | ✅ | ✅ | ⚠️ 需处理 | 中低 |
| P3 | 大（全量单位替换） | ✅ 最好 | ✅ | ⚠️ | 中 |

### 4.4 `@page` 的三个坑（都是规范/浏览器行为，不是实现失误）

1. **`size` 不接受 CSS 变量**。`@page { size: var(--paper) }` 无效，
   必须**动态注入一段 `<style>`**（改 `textContent` 或换 `href`），
   不能像普通属性那样靠 `style` 绑定。这是「纸张切换」唯一必须用 JS 的地方。
2. **`margin: 0` 必须显式给**。不给就是「浏览器默认边距 + `.paper` 自己的 padding」叠加，
   打印出来四周多一圈白边，而屏幕上没有——这是「为什么打印和预览不一样」最常见的答案。
   代价是用户若勾选「页眉页脚」，页眉会压在纸面顶部 14mm 内（当前只能靠 `StylePanel` 那句提示）。
3. **`@page size` 会被用户的打印对话框覆盖**。用户手动选「A4」就按 A4 印，
   即使简历设的是 Letter；「缩放」选项也会破坏 1:1。
   ——这类**只能提示，不能保证**，要写进 `StylePanel` 的打印提示里。

> 待实测：Chrome「另存为 PDF」是否严格遵循 `@page size`（不同版本行为有差异），
> 需要 D5.1 落地时用真实 PDF 页尺寸核对。本轮未验证，不下结论。

### 4.5 纸张与页数的耦合

换纸张改变了**垂直可用高度**（Letter 少 8.5%），页数随之改变。
所以 4.2 的 P2 与第 9 节的页数测量必须**同一批做**，否则会出现
「切到 Letter，纸面明显更挤，但标签还写着 1 页」。

---

## 5. 决策三：配色

### 5.1 三个候选

| 方案 | 存储 | 对比度安全 | 双端一致 | 用户自由度 |
|---|---|---|---|---|
| **A. 固定预设**（主题 = 枚举 id） | `theme VARCHAR(20)` | ✅ 一次性设计时保证 | ✅ 静态表，零漂移 | 低（8 选 1） |
| B. 任意 hex | `theme_color CHAR(7)` | ❌ 运行时才能判断 | ❌ 派生算法要写两遍 | 高 |
| C. 预设 + 自定义 | 两列或 `theme` 允许 hex | 需运行时时钳制 | 需同源算法 | 高 |

### 5.2 为什么推荐 A（并且这是「对比度安全」最省力的答案）

先看实测数据（8 个候选种子 vs 白纸的对比度，公式见 5.4）：

| 预设 id | 名称 | hex | vs `#fff` | AA 正文（≥4.5） | AA 大字/UI（≥3） |
|---|---|---|---|---|---|
| `forest` | 青绿（当前默认） | `#527873` | 4.89 | ✅ 通过 | ✅ |
| `ink` | 墨黑 | `#263330` | 13.14 | ✅ | ✅ |
| `navy` | 藏青 | `#2F4A6B` | 9.08 | ✅ | ✅ |
| `wine` | 酒红 | `#7A3B3B` | 8.35 | ✅ | ✅ |
| `plum` | 深紫 | `#4B3A6B` | 9.92 | ✅ | ✅ |
| `cocoa` | 深棕 | `#6B4A2F` | 7.94 | ✅ | ✅ |
| `slate` | 石板灰蓝 | `#3D4F5C` | 8.50 | ✅ | ✅ |
| `olive` | 橄榄 | `#4A5A32` | 7.50 | ✅ | ✅ |

**这 8 个全部通过 AA 正文标准**，而且是**离线算出来的**——不是运行时才发现的。

对比 B 的困境：用户选 `#FFE066`（亮黄），对比度约 1.3:1，白纸上几乎看不见。
你有两个选择：

- **拦**：直接报错「颜色太浅」→ 用户困惑，因为他觉得很好看；
- **钳**：自动压暗到能看清 → 用户选的颜色**不是他看到的颜色**。

两者都不好。**预设把这个问题消灭在设计阶段，而不是甩给运行时。**

### 5.3 如果一定要做自定义 hex：【硬约束】双端必须同源

钳制算法本身不复杂，我实测过（Python `colorsys`，无新依赖）：
在 HLS 空间里把亮度按 1% 步长下调，直到对比度 ≥4.5，最多 100 步。

| 输入 | 输出 | 步数 | 输出对比度 |
|---|---|---|---|
| `#8FB3AB`（浅青） | `#557D74` | 22 | 4.59 |
| `#9DB4E8`（浅蓝） | `#4672D4` | 21 | 4.55 |
| `#F2A65A`（橙） | `#B4610E` | 27 | 4.51 |
| `#7ED6A5`（薄荷） | `#2B8653` | 32 | 4.53 |
| `#C8A2C8`（藕紫） | `#A05FA0` | 21 | 4.53 |

**但问题不在算法，在「谁来算」**：

- 若前端算一份、后端算一份，两份必然漂移（浮点、色彩空间、步长任一不同就不同）；
- 前端**没有测试框架**（2.8 节），漂移无法被自动化发现；
- 表现是「预览是 `#557D74`，导出的 HTML 是 `#547C73`」——肉眼几乎看不出，
  但属于「同一份简历两种颜色」，属于数据不一致。

三个解法，按推荐度排序：

1. **只信后端**：保存后回读 `theme_color`，前端**只渲染服务端返回的值**。
   漂移消失，代价是「拖动取色器时预览不实时」（需要防抖 + 一次请求，或本地先预览、松手后校正）。
2. **共享夹具 + 双端各写测试**：一份 `docs/design/theme-tokens.json`，
   后端 pytest 断言、前端……**没有测试可写**，只能靠人眼。→ 保证是弱的。
3. **限定预设**（就是方案 A）。

**因为 (3) 的成本几乎为零而 (1)(2) 都有真实代价，所以推荐先用 A；
把自定义 hex 作为「等你确认用户真的需要」的后置项。**

### 5.4 对比度红线：写数字，不写形容词

WCAG 2.x 相对亮度：

```
L = 0.2126·R + 0.7152·G + 0.0722·B      （R/G/B 为 sRGB 线性化后的分量）
contrast = (L_light + 0.05) / (L_dark + 0.05)
```

标准要求：

- **正文（< 18.66px 粗体 / < 24px 常规）：≥ 4.5:1**
- **大字与「非文本 UI 边界」：≥ 3:1**

对本项目的映射（字号来自 `ResumePreview.vue:568-580`）：

| 用途 | 字号 | 门槛 | 现有色 | 实测 | 判定 |
|---|---|---|---|---|---|
| `h2` 栏目标题 | 13px / 650 | 4.5 | `#527873` | 4.89 | ✅ 刚过 |
| 页眉下边框 | 2px 线条 | 3.0（UI） | `#6b8f8a` | 3.55 | ✅ |
| 姓名 | 32px / 700 | 3.0（大字） | `#263330` | 13.14 | ✅ |
| 经历描述 | 12px | 4.5 | `#52635f` | 6.35 | ✅ |
| 日期 | 11px | 4.5 | `#687875` | 4.63 | ⚠️ 勉强 |
| 技能下划线 | 1px | 3.0（UI） | `#b8c8c4` | **1.74** | ❌ 未达（装饰性，可豁免，但换深色主题时会更明显） |

**结论：主题色只能用在「h2 / 页眉下边框」这两处；绝不能扩展成「正文色」或「背景色」。**
（把背景染成主题色，等于把整页对比度重算一遍，是另一个量级的工作。）

### 5.5 CSS 变量化清单（这是「配色」真正的第一步）

把 `ResumePreview.vue:561-592` 的 8 个色值替换为变量，**变量声明挂在 `.paper` 上**：

```css
.paper { --paper-accent: #527873; --paper-rule: #6b8f8a; --paper-ink: #263330;
         --paper-body: #52635f; --paper-muted: #687875; --paper-faint: #8fa5a1;
         --paper-line: #b8c8c4; }
.paper .paper-section > h2 { color: var(--paper-accent); }
.paper .paper-header { border-bottom-color: var(--paper-rule); }
/* …其余 6 处同理 */
```

**注意这是纯重构，行为不变**，验收方式很干净：
改前改后各截一张同数据图，逐像素比对（或肉眼确认无差异）。
**这一步可以完全脱离数据库和 API 独立合入**——建议单独成为一个小提交。

### 5.6 生成器怎么拿到主题（两种落点，必须都做）

| 落点 | 谁负责 | 管什么 | 不做会怎样 |
|---|---|---|---|
| ① `.paper` 上的 CSS 变量 | 前端（从 `profile.theme` 映射） | 实时预览 + 打印 + 「已生成」视图 | 换主题后已生成的视图不变色 |
| ② 生成 HTML 里的内联变量 | 生成器 | 未来导出 / 分享页 | 导出的文件丢主题 |

②的具体形态（在 `body` 里包一层，而不是塞进 `head`，因为前端只取 `body.innerHTML`）：

```html
<body>
  <div class="paper-doc" style="--paper-accent:#7A3B3B;--paper-rule:#…">
    <header class="paper-header">…</header>
    …
  </div>
</body>
```

**两条落点读的是同一个 `theme` 枚举值**，因为是静态预设表，不存在漂移。

> **【待拍板】Q19**：改样式后是否自动回到「实时预览」并提示「需重新生成」？
> 若不提示，用户会以为「已生成」视图里的旧颜色是 bug。

---

## 6. 决策四：排版数据模型（本文档的核心）

### 6.1 先明确一件事：要存的是「展示顺序」，不是「数组顺序」

有两条路都能实现「技能排到教育前面」：

| 路线 | 做法 | 后果 |
|---|---|---|
| ❌ 重排数据数组 | 把 `experiences` / `skills` 数组按用户拖拽结果重排 | 毁掉语义：教育经历本该按入学时间倒序（`resume_generator.py:126` `sorted(..., reverse=True)`），重排后「最近学历在前」失效；将来做「按时间排序」开关会与手工顺序打架 |
| ✅ 存展示顺序 | 数据数组不动，另存一份 `[{id, title}]` 的有序列表 | 零语义污染；顺序可以随时重置为默认；换模板/换渲染器都能复用 |

**本文档所有选项都走第二条。** 这一点确定了，剩下的只是「这份有序列表存哪」。

### 6.2 选项 A：`student_profiles.layout_config` JSON 列

```json
{
  "version": 1,
  "sections": [
    { "id": "experiences", "title": "项目经历" },
    { "id": "skills",      "title": "技能" },
    { "id": "educations",  "title": "教育背景" }
  ]
}
```

- 存储：1 条 `ALTER TABLE … ADD COLUMN layout_config JSON NULL`
- `NULL` 的语义 = **从未自定义**，读时套默认值。
  （这比 `NOT NULL DEFAULT '[]'` 好在两点：语义明确；且**规避 MySQL 8.0.13 之前
  JSON 列不支持默认表达式**的兼容问题——`ALTER TABLE` 加 NOT NULL 的 JSON 列在旧 8.0.x 上会失败。）
- 优点：
  1. **读是原子的**：一个 `SELECT` 拿到全部顺序，不需要 join；
  2. **写是原子的**：与内容在同一个 `UPDATE` 里落库，不会出现「顺序存了、标题没存」；
  3. 天然可扩展：将来加「自我评价」「奖项荣誉」，只是列表里多个 id，**不需要迁移**；
  4. 与既有约定一致：`docs/DATABASE.md` §5.3 原文——
     「JSON 仅用于 AI 原始输出、解释和**可变的简历区块**；核心筛选字段使用独立列」。
     排版顺序属于「可变的简历区块」，且**从不参与筛选/排序/join**，正好落在允许区间。
- 缺点：
  1. 数据库层无法约束形状（MySQL JSON 没有 schema 校验）→ 校验必须全在应用层；
  2. `select(StudentProfile)` 会把 JSON 一起读出来（列表接口也读）——
     单份几百字节 × 20 份上限，量级可忽略；若要洁癖可用 `load_only()` 排除；
  3. **原地修改不被 SQLAlchemy 追踪**（见 6.8 的实现陷阱）。

### 6.3 选项 B：新表 `profile_layouts`

两种形态，代价不同：

**B1：一份简历一行（整个数组塞进一列）**

```sql
CREATE TABLE profile_layouts (
  student_profile_id VARCHAR(36) NOT NULL PRIMARY KEY,
  config JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_profile_layouts_profile FOREIGN KEY (student_profile_id)
    REFERENCES student_profiles(id) ON DELETE RESTRICT
);
```

→ **它和选项 A 存的东西完全一样**，只是把 JSON 挪到了另一张表。
**唯一收益是「主表行宽」和「可以单独乐观锁」，代价是一次 join + 一张表 + 一套仓储 + 一个级联删除点。**
在本项目的访问模式下（每次打开编辑器只读一份、保存时整份写回），join 的收益是负的。

**B2：一个模块一行（真正关系化）**

```sql
CREATE TABLE profile_layout_sections (
  id VARCHAR(36) NOT NULL PRIMARY KEY,
  student_profile_id VARCHAR(36) NOT NULL,
  section_key VARCHAR(32) NOT NULL,   -- educations / skills / experiences
  title VARCHAR(50) NOT NULL,
  position INT NOT NULL,
  visible TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_layout_section (student_profile_id, section_key),
  KEY ix_layout_order (student_profile_id, position)
);
```

- 优点：`section_key` 可加 FK/枚举约束；`position` 可加唯一约束（`UNIQUE(profile_id, position)`）；
  按模块做局部更新（PATCH 一个标题）不需要重写整份。
- 缺点：
  1. **写入炸成 N 行**：`replace_profile_content()` 现在是「父表 UPDATE + 子表先删后插」，
     加进来就是第 5 张要「先删后插」的子表，且必须插进 `_CHILD_TABLES` 的正确位置
     （`student_profile_repository.py:48` 的 `_CHILD_TABLES`，`hard_delete`/`purge_expired` 都依赖它）；
  2. **必须为每份新简历预插 3 行**，否则「没有 layout 行」和「默认顺序」两种空态要各写一套兜底；
  3. **`copy_from_id` 深拷贝要多拷贝一组行**（`duplicate_profile` 目前只拷贝 skills/experiences/educations，
     漏一组就是「复制出来的简历排版丢了」）；
  4. 顺序的**完整性**从「一个数组」变成「一组 position」，删除中间一行要重排 position，
     否则出现空洞——这是关系化排序的经典税。
- 优点是否值这个税：**只有当你需要「按排版配置做查询/统计」（例如「80% 用户把技能放前面」）时才值**。
  现在没有这个需求。

### 6.4 选项 C：3 个标题列，不存顺序

```sql
ALTER TABLE student_profiles
  ADD COLUMN education_title  VARCHAR(50) NULL,
  ADD COLUMN skills_title     VARCHAR(50) NULL,
  ADD COLUMN experiences_title VARCHAR(50) NULL;
```

- **只满足「自定义标题」，不满足「调顺序」**，也就是只做了需求的一半。
- 优点：最简单，零 JSON，零校验，SQL 可读。
- 缺点：加第 4 个模块（自我评价）要再加一列 + 一次迁移；顺序完全做不到。
- 定位：**如果人类决定「先不做拖拽、只做改名」，C 是最省的选择，
  但它会成为后面做顺序时的技术债**（届时要么加 JSON 再迁数据，要么加第 4 列）。

### 6.5 选项 D：块模型 `profile_blocks`（评估后不推荐）

每段内容（一段教育、一段经历、一个技能组）都是 `blocks` 表的一行，
带 `type` / `content JSON` / `position`，像 Notion 一样自由排列。

- 听上去最「DIY」，但要求：
  - 现有三张子表（`skills` / `experiences` / `educations`）全部迁移进 `blocks`，
    或者长期维持「双写」；
  - 前端要有「新增任意块」的能力（否则只是把顺序问题换个地方）；
  - 富文本编辑、Schema 校验、AI 生成全部要重做。
- **这是 Phase 3 的架构，不是 D5 的架构。** 本轮排除。

### 6.6 四方案对比

| | A（JSON 列） | B1（侧表单列） | B2（侧表分行） | C（3 列） | D（块模型） |
|---|---|---|---|---|---|
| DDL 量 | 1 列 | 1 表 + FK | 1 表 + FK + 2 索引 | 3 列 | 1 表 + 迁移三张子表 |
| 支持顺序 | ✅ | ✅ | ✅ | ❌ | ✅ |
| 支持改名 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 读代价 | 0（同表） | +1 join | +1 join + N 行 | 0 | +1 join + N 行 |
| 写原子性 | ✅ 一条 UPDATE | ⚠️ 两次写 | ⚠️ 删 N 插 N | ✅ | ⚠️ 复杂 |
| 加第 4 个模块 | 无需迁移 | 无需迁移 | 无需迁移 | 需迁移 | 天然 |
| 级联删除风险 | 无（跟着父行） | ⚠️ 要加进 `_CHILD_TABLES` | ⚠️ 同左 | 无 | ⚠️ |
| `copy_from` 改动 | 1 行 | 1 段 | 1 段 | 3 行 | 大改 |
| 可查询性 | 差（JSON） | 差 | ✅ 好 | ✅ 好 | ✅ 好 |
| 回滚成本 | `DROP COLUMN` | `DROP TABLE` | `DROP TABLE` | 3 × `DROP COLUMN` | 高 |

### 6.7 推荐 A 的五条理由

1. **访问模式是「整份读、整份写」**——每次进编辑器只读一份简历，保存时整体覆盖
   （`replace_profile_content()` 连子表都是先删后插）。关系化表擅长的「局部更新」在这里没有用武之地。
2. **原子性免费**：顺序和内容在同一条 `UPDATE` 里，不存在「存了一半」。
   用侧表则必然两次写（父表 + 侧表），要考虑事务与失败回滚。
3. **级联删除免费**：JSON 跟着父行走，`soft_delete` / `hard_delete` / `purge_expired` 一行都不用改。
   侧表则必须插进 `_CHILD_TABLES`，插错顺序就报 1451（D4.1 踩过这个坑）。
4. **`copy_from` 只改一行**：`duplicate_profile()` 加 `layout_config=deepcopy(source.layout_config)` 即可。
   侧表要再写一段「拷 N 行」的逻辑。
5. **有既有约定背书**：`DATABASE.md` §5.3 明确允许「可变的简历区块」用 JSON。
   本条不是「我偏好 JSON」，而是「项目已定的规矩，本条正好落在允许区」。

**唯一真正让 B2 更好的场景**是「要按排版做统计/搜索」。若人类认为这是近期需求，选 B2；
否则 A 是明确更优解。

### 6.8 选 A 的话，这五处必须做对（否则会以 bug 的形式还回来）

**（1）版本字段**

```json
{ "version": 1, "sections": [...] }
```

`version != 1` → 当作默认顺序，**不报错**。给未来留一条平滑升级的路
（例如将来支持「两栏布局」时，version 2 可以带 `columns` 字段）。

**（2）完整快照，不是「差异」**

存**完整的顺序列表**，不存「把 skills 移到 index 1」这种差异。
理由：差异表示需要回放顺序，一旦默认顺序变了，历史差异的含义就变了。
完整快照读时零计算，且「重置」= 删掉该字段。

**（3）降级矩阵（这是最容易写崩的地方）**

| 情况 | 处理 | 为什么 |
|---|---|---|
| `layout_config IS NULL` | 用默认顺序 | 从未自定义 |
| JSON 非法（手改库/截断） | 用默认顺序 + 记 `warning` 日志 | **绝不能 500**，一份简历不该让编辑器打不开 |
| `version` 未知 | 用默认顺序 | 向前兼容 |
| `sections` 为空数组 | 用默认顺序（**不是**「什么都不渲染」） | 空数组最可能是数据损坏，不是用户意图 |
| 缺某个 id | **追加到末尾**（按默认顺序补齐） | 用户升级前只见过 3 个模块；将来加第 4 个，老配置要能自动带上它 |
| 出现未知 id | **丢弃** + 日志 | 可能是新版本写入、旧版本读取 |
| `id` 重复 | 保留第一次出现的位置，后面的丢弃 | 重复 id 会让 `v-for` key 冲突 |
| `title` 为空/全空白 | 回落到该模块的默认标题 | 空标题在纸面上是个空 `h2`，很丑且不可解释 |

**核心原则：排版配置是「装饰信息」，任何异常都退化为默认，永远不让它阻断主流程。**
（这与 `validateForSave()` 的哲学相反——那里要把错误抛给用户，因为那是用户的输入；
这里坏掉的多半是数据，用户无从修复。）

**（4）标题长度上限：12 个字符**

版面预算（算术）：

- `h2` 是 `font-size: 13px` + `letter-spacing: .14em`（=1.82px），
  一个中文字符的推进宽度 ≈ **14.82px**；
- 最窄的纸面出现在 360px 视口（单栏，`padding: 20px 16px`）：
  纸宽 ≈ 328px，内容宽 = 328 − 104 = **224px** → 最多 **15 字**不折行；
- 1440 视口下内容宽 616px → 41 字。

**取 12 是留了 3 字余量**（考虑到标点、英文混排、以及未来换成 mm 定尺后的微调）。
超过 12 字在手机上会折成两行，`h2` 的行高会被撑开，与下方内容的间距失衡。

> 硬上限可以设 20（数据库层 `VARCHAR(50)` 只是兜底），但**前端输入框要 `maxlength=12` 并给出提示**，
> 否则用户会输入「项目经历与开源贡献说明」，然后发现手机上版式塌了。

**（5）SQLAlchemy 的原地修改陷阱**

```python
# ❌ 不会落库：SQLAlchemy 默认不追踪 JSON 内部的可变对象
profile.layout_config["sections"].append({...})

# ✅ 整体赋值
profile.layout_config = {"version": 1, "sections": [...]}
```

若一定要原地改，得用 `MutableDict.as_mutable(JSON)` 包一层。
**建议约定「只整体赋值」**，并在 ORM 注释里写死这条——否则将来某次「只手滑加一个 append」
会表现为「保存成功但顺序没变」，极难排查。

同理，`duplicate_profile()` 里**必须 `copy.deepcopy(source.layout_config)`**，
不能直接赋引用：两个 ORM 实例共享同一个 dict 对象，任何一方的原地修改都会污染另一方。

**（6）第一版不要 `visible` 字段**

「隐藏某个模块」听起来和「排序」是一家人，但它引入新问题：
全隐藏 = 一张只有姓名的纸；隐藏「项目经历」后生成接口的完整性校验
（`resume_service.py:47` 要求 `skills` 和 `experiences` 都非空）会与 UI 打架。
**建议 D5.3 只做「顺序 + 标题」，`visible` 单独评估。**

### 6.9 迁移草案（A 方案，3 条 DDL，双向可逆）

```python
# upgrade
op.add_column("student_profiles", sa.Column("paper_size", sa.String(16),
              nullable=False, server_default="A4"))
op.add_column("student_profiles", sa.Column("theme", sa.String(20),
              nullable=False, server_default="forest"))
op.add_column("student_profiles", sa.Column("layout_config", mysql.JSON(), nullable=True))

# downgrade（无损：三列都是「可丢弃的装饰信息」）
op.drop_column("student_profiles", "layout_config")
op.drop_column("student_profiles", "theme")
op.drop_column("student_profiles", "paper_size")
```

与 D4.1 迁移（`20261103_0007`）的差异，值得记一笔：

- 这次**没有索引改动、没有外键依赖**，所以不会重演 `1553 Cannot drop index` 那个坑；
- 这次 `downgrade` **是无损的**（D4.1 的降级是有损的，要删每用户多余的简历）；
- `paper_size` / `theme` 用 `NOT NULL + server_default`（枚举有天然默认值），
  `layout_config` 用 `NULL`（「未自定义」需要与「自定义成默认顺序」区分）。

---

## 7. 决策五：前后端渲染同步

### 7.1 现状：两套模板靠「手工对齐」维持

| | 生成器 | 预览 |
|---|---|---|
| 载体 | Python f-string 拼 HTML | Vue 模板 |
| 对齐方式 | 类名手抄 + 注释约定（`resume_generator.py:36-48` 那张对应关系表） | 同名 class |
| 顺序 | 模板占位符顺序 | `<section>` 排列顺序 |
| 校验 | 无 | 无 |

引入 `layout_config` 后，这条「手工对齐」的绳子要**同时套住顺序和标题**，断了就是
「预览里技能在前、生成的 PDF 里技能在后」——用户绝对会发现，而且无法自行修复。

### 7.2 三种同步策略

| | 形态 | 优点 | 缺点 |
|---|---|---|---|
| **S1. 继续双模板 + 契约** | 后端按 layout 拼 HTML，前端按 layout 渲染 `<section>`，两边读同一份 `layout_config` | 改动最小；生成物仍是 HTML（导出/分享直接可用） | 两套模板，长期漂移风险 |
| **S2. 后端只出 JSON，前端统一渲染** | 生成接口返回结构化区块，前端渲染；`content_html` 不再存 HTML | 单一渲染源，永不漂移 | 生成物不再是 HTML，导出/分享要重新拼；等于把「生成」变成「快照数据」，与 T7.2 的语义反转 |
| **S3. 前端渲染后回传** | 前端把当前 DOM 的 `outerHTML` POST 给后端存 | 绝对一致（所见即所存） | 后端失去内容控制权（XSS 面扩大、无法校验）；生成物包含前端临时属性；`content_html` 变成不可信输入 |

**推荐 S1（本轮），S2 是吸引人的陷阱**：
S2 看起来「更干净」，但它把「简历是一份 HTML 文档」这个既成事实推翻，
连带影响未来的导出/分享/版本对比。**在 HTML 仍是交付物的前提下，双模板是对的**，
要做的是把「对齐」从注释变成可执行的检查（7.4）。

### 7.3 生成器改造的最小形态

核心思路：**把「顺序」从「模板位置」里抽出来**，模板不再写死三段。

```python
# 现状：模板里三个占位符，顺序固定
# '{education_section}{skill_section}{experience_section}'

# 改造后：先算出 {id: 渲染好的片段}，再按 layout 顺序拼接
blocks: dict[str, str] = {}
if education_items:
    blocks["educations"] = f'<section class="paper-section"><h2>{escape(layout.title_of("educations"))}</h2>{education_items}</section>'
if skill_items:
    blocks["skills"] = …
if experience_items:
    blocks["experiences"] = …

body = "".join(blocks[section.id] for section in layout.sections if section.id in blocks)
```

三个关键点：

1. **「有内容」判断保持在片段生成阶段**（`if skill_items`），不在拼接阶段。
   否则「用户删光了技能」会渲染出一个空 `技能` 标题。
2. **缺模块自动跳过**：`if section.id in blocks` 就是优雅降级——
   用户把「教育」拖到第一位但没填教育，第一位就直接是「技能」。
3. **标题来自 layout，不是字面量**，且 `escape()` 不能省（用户可输入任意字符）。

### 7.4 契约测试怎么写（不加依赖）

现有测试都在后端（pytest），前端没有测试框架（2.8 节）。所以可行的检查只有两类：

**（a）后端能测的**：

- `layout_config` 各种降级输入 → 生成 HTML 里 `<h2>` 的**文本与顺序**符合预期
  （`re.findall(r'<h2>(.*?)</h2>', html)`，无需解析库）；
- 缺模块时不渲染空标题；
- 标题含 `<script>` 被 `escape`；
- 顺序为 `[skills, educations, experiences]` 时，HTML 里三个 `<h2>` 的下标顺序正确。

**（b）后端测不了的（必须诚实标注）**：

「预览的 `<section>` 顺序 == 生成 HTML 的 `<section>` 顺序」这条**没有自动化手段**，
因为预览是 Vue 渲染的。可选做法只有：

- 手工 e2e（改顺序 → 生成 → 用肉眼比对两张纸）；
- 或者引入 Playwright（本轮明确不装依赖）。

**这是本轮评估发现的最大质量缺口**：一个「必须两端一致」的约束，
却只有一端有测试。建议把「前端引入 vitest」列入 Phase 2 技术债（见 13 节 Q20）。

---

## 8. 决策六：拖拽交互与 `contenteditable` 的冲突

### 8.1 为什么这是本轮最危险的一处

不是技术难，是**两个手势抢同一块区域**：

- 「编辑」要求点击/双击进入光标编辑（`contenteditable="true"` 在 `ResumePreview.vue` 里有 13 处，
  且都在 `v-for` 里，实际渲染出的可编辑节点会按条数翻倍）；
- 「拖拽」要求按住并移动。

浏览器对 `contenteditable` 内的 `mousedown + move` 默认解释为**选择文本**。
用户想把「技能」拖走，实际得到的是「技能」两个字被选中——**这是必然发生，不是偶发**。

`ResumePreview.vue:582-585` 还有一层：失焦回写（`@blur` → `commitText()` → `store.updateXxx()`）。
拖拽会触发 `blur`，于是「拖一下 → 顺手写了一次 store → `dirty` 变 true」。
不致命，但会让「拖拽算不算修改」的账变得混乱。

### 8.2 三种交互形态

| | 形态 | 新增依赖 | 体积 | 学习成本 | 触摸屏 | 键盘/读屏 | 与 contenteditable 冲突 |
|---|---|---|---|---|---|---|---|
| **G1** | 原生 HTML5 `draggable` | ❌ | 0 | 低（但要自己写落点判定） | ⚠️ 差（移动端 DnD 基本不可用） | ❌ | 高（`draggable` 与 `contenteditable` 同元素行为不一致） |
| **G2** | `vuedraggable@4`（内含 sortablejs） | ✅ | 量大（量级估算：sortablejs + 封装，gzip 数十 KB；**以实现时 `npm run build` 前后 chunk 差为准**） | 中（`v-model` + `item-key` + `handle` 三板斧，但细节坑多） | ✅ 好 | ❌（需自建 fallback） | 中（可用 `handle` / `filter` 规避） |
| **G3** | 上/下箭头按钮 | ❌ | 0 | 极低 | ✅ 好 | ✅ 好 | **无** |

### 8.3 体积与学习成本，具体一点

**G2 的坑（按踩到的概率排序）**：

1. `item-key` 必填且必须稳定——用 index 会在重排后错乱（与 `v-for` 同一个坑）；
2. sortable 插入的「幽灵元素 / 拖拽副本」**不带 Vue 的 scoped 属性**，
   所以 `ghost-class` 的样式必须写在**非 scoped** 块里——
   本项目已有这个先例（`ResumePreview.vue:561` 的纸面样式就是非 scoped，并写明了原因）；
3. 与 `transform: scale()` 冲突：sortablejs 用 `getBoundingClientRect()` 与鼠标坐标比对，
   **纸面被 zoom 缩小后，判定会整体偏移**（这是第 4.2 节 P2 与本节交叉产生的隐藏 bug）。
   缓解：`forceFallback: true` 并让拖拽层脱离缩放容器，或干脆在「未缩放」的编辑层里做拖拽；
4. 触摸与滚动抢手势：移动端要在 `touchmove` 与页面滚动之间取舍（`delay` / `delayOnTouchOnly`）；
5. `v-model` 绑定的是数组，**重排会替换整个数组引用**——
   若 store 里 `layout_config.sections` 是嵌套对象，需要 `@update:model-value` 手动赋值而不是直接双向绑定
   （否则会触发 6.8(5) 的原地修改陷阱）。

**G3 的成本几乎为零**：三个模块，最多两种「上移/下移」，加起来 6 个按钮，
约 40 行模板 + 10 行逻辑，无依赖、无触摸问题、天然可访问。

### 8.4 冲突的四种具体表现与解法

| 表现 | 原因 | 解法 |
|---|---|---|
| 拖拽变成选中文字 | `contenteditable` 抢 `mousedown` | **拖拽手柄**：只有手柄（`⋮⋮`）可拖，正文区域 `draggable=false`；配合 `handle` 选项 |
| 拖拽后光标乱跳 | `blur` → 回写 → Vue patch DOM | 拖拽期间给 `.paper` 加 `pointer-events: none` 或者在「排版模式」下把 `contenteditable` 置 false |
| 拖拽时文本被编辑（移动端长按） | 长按触发编辑菜单 | `filter: '[contenteditable]'` + `preventOnFilter` |
| 缩放后落点偏移 | `transform: scale` 改变坐标系 | `forceFallback: true`，或拖拽层不参与缩放 |

### 8.5 强烈建议：拖拽放进独立的「排版模式」

不做模式切换（在编辑态直接拖）意味着：**每个 `contenteditable` 都要同时是「可编辑」和「可拖拽」**，
这在三个浏览器上都要单独调，且每次改版式都可能回归。

做模式切换（`编辑模式` ⇄ `排版模式`）：

- 排版模式下 `contenteditable` 全部置 `false`（或整体加 `pointer-events: none`），
  纸面上只剩「手柄 + 标题」，拖拽判定干净，**没有任何手势冲突**；
- 编辑模式下手柄隐藏，行为与今天完全一致（不破坏 `contenteditable` 机制）；
- 代价：多一个 UI 状态（顶栏或 `StylePanel` 一个开关）。

### 8.6 【待拍板】G3 先行是否足够？

事实：**需要排序的对象只有 3 个**（教育 / 技能 / 项目经历）。
3 个元素的排序，全排列只有 6 种，「上移/下移」两步内可达任意顺序。

**收益对比**：

- G3：约 50 行代码、0 依赖、0 手势冲突、可访问性天然达标，覆盖 100% 的排序需求；
- G2：+1 个依赖、+1 套拖拽态样式、+1 个模式开关、跨浏览器调试，**覆盖的是同一个功能**，
  差别只在「手感」。

**建议：D5.3 先做 G3，把拖拽作为「用户抱怨箭头太笨」之后的独立小版本。**
这样数据模型（6 节）完全不用改——顺序就是那一个数组，交互只是往数组里写值。

### 8.7 可访问性：拖拽必须有非拖拽替代

WCAG 2.2 SC 2.5.7（Dragging Movements）要求：**任何依赖拖拽的操作都要有单点替代方案**。
即使最终做了 G2，也**必须保留 G3 的箭头**（可以隐藏为「辅助操作」）。
这不是「可选优化」，是达标项。

---

## 9. 决策七：A4 溢出与分页

### 9.1 先把事实说清楚：现在根本没有「第二页」

- `.paper` 是 `overflow: auto`：内容超长在纸内部滚动，屏幕上永远一张纸；
- **屏幕不分页，打印才分页**，而分页点由浏览器按纸高自行决定，项目无法控制；
- 所以「第二页只有一行字」这个现象**今天就可能存在**（只是内容量上限让它没发生），
  拖拽上线后它不会变多，只会**变得更不可预测**（分页点位置变了）。

### 9.2 溢出风险的真实来源

| 来源 | 是否被 reorder 放大 | 说明 |
|---|---|---|
| 总内容高度超一页 | ❌ 不变 | reorder 不增删内容 |
| **孤行标题**（`h2` 在页尾、正文在下一页） | ✅ 显著放大 | 换顺序改变了各模块的起始位置 |
| 单条经历被跨页切开 | ✅ 放大 | 例如把「项目经历」拖到页尾 |
| 技能区被拆成两半 | ✅ 放大 | 技能是 `flex-wrap` 流式，天然容易断 |

**所以正确的解法是「控制断点」，不是「控制总量」。**

### 9.3 四个候选

| | 做法 | 成本 | 效果 | 风险 |
|---|---|---|---|---|
| **Y1** | CSS 分页规则：`break-inside: avoid`（每条经历）、`break-after: avoid`（`h2`）、`orphans/widows: 3` | 极小（约 6 行 CSS） | 消除孤行、避免单条经历被切 | 可能出现「整段被推到下一页」导致页尾留白变大 |
| Y2 | 硬限制一页：超了就拦（禁止保存/生成） | 中（要测量 + 拦截 + 文案） | 强制一页 | **会逼用户删内容**，见 9.4 |
| Y3 | 动态缩放：测量后 `scale` 到一页 | 中 | 「压进一页」 | 见 9.5 |
| Y4 | 真分页预览：在纸面上画出页边界 | 高 | 真正 WYSIWYG | 与 `contenteditable` 冲突（见 9.6） |

### 9.4 为什么不能「硬限制一页」

1. **两页不是错误**。应届生一页是行业惯例，但「三端项目 + 两段实习」的硕士生一页装不下，
   硬拦等于让他删掉真实经历——这是**产品在教用户撒谎**。
2. **硬拦无法自查**。用户看到「超出 1 页」但不知道超了多少、超在哪，
   只能反复删一句、点一次保存。体验比不管更差。
3. **技术上也不好拦**：`ProfileUpsertRequest` 的保存是草稿式的（不拦必填），
   在保存路径上引入「版面测量」会让「保存」依赖渲染结果，后端无法验证（后端没有浏览器）。

**结论：不做 Y2。**

### 9.5 Y3 的参数（如果做）

- **只对「导出/打印」生效**，不影响编辑；
- `scale = min(1, pageHeight / contentHeight)`，**下限 0.85**（再小正文就小于 9pt，HR 看不清，等于白做）；
- **永远不自动应用**，必须用户点「压缩到一页」；
- 缩放用 `zoom` 或 `transform: scale` 只包**打印时的纸面**，
  不改 `contenteditable` 的编辑态（否则输入坐标全乱）。

### 9.6 Y4 的实现代价，以及一个便宜的替代

真分页需要在内容中插入「分页占位」，而占位节点会：
破坏 `contenteditable` 的文本流（用户在占位前后打字会把占位当成内容）、
污染 `innerText`、让 `@blur` 回写读到脏文本。

**便宜的替代：只读的页边界覆盖层**

- 用一个绝对定位的层，在 `n × 297mm` 处画一条虚线 + 页码；
- 不插入任何 DOM 到 `.paper` 内部，**完全不碰编辑**；
- 需要 `ResizeObserver` + 一次高度测量，约 60 行；
- 缺点：内容仍然在纸内滚动，「第 2 页」的部分要靠缩放/滚动才能看全
  （或者在覆盖层里做一个小地图）。

**建议：D5.4 先做 Y1 + 页数测量（真实页数替换掉「1 页」字面量），
Y4 的覆盖层作为后续增强。**

---

## 10. 决策八：API 契约变更

### 10.1 新增字段（都在 `PUT /profiles/{id}` 上）

```python
class ProfileUpsertRequest(BaseModel):
    # …既有字段不动…
    paper_size: PaperSize | None = None          # Literal["A4", "Letter"]
    theme: ThemeId | None = None                 # Literal["forest", "ink", …]
    layout_config: LayoutConfig | None = None    # {version, sections[]}
```

`ProfileRead` / `ProfileSummary` 同步加 `paper_size` / `theme`；
`layout_config` **只加进 `ProfileRead`**（列表不需要，避免列表请求变大）。

### 10.2 【硬约束】PUT 是整体替换，但新字段必须「缺省即保留」

这是本轮**最容易造成线上数据损坏**的一条。

现状：`PUT /profiles/{id}` 是整体覆盖（`replace_profile_content()` 的 docstring 明确写了语义）。
若新字段按「没传 = None = 恢复默认」处理，那么：

> 任何仍在使用旧前端（浏览器缓存了旧版 JS）的用户，
> **点一次「保存修改」就会把主题和排版全部清成默认**，而且没有任何提示。

**解法**：用 pydantic 的 `model_fields_set` 区分「没传」与「显式传 null」：

```python
if "theme" in payload.model_fields_set:
    profile.theme = payload.theme or DEFAULT_THEME      # 显式 null = 恢复默认
# 没传 → 什么都不做（保留库里原值）
```

这样得到三态语义，且向后兼容：

| 请求体 | 语义 |
|---|---|
| 不含 `theme` | **保持原样**（旧客户端安全） |
| `"theme": null` | 恢复默认 |
| `"theme": "wine"` | 设为酒红 |

这条**必须写进 `tests/test_profiles.py`**：「不带 theme 的 PUT 不改变已有 theme」。

### 10.3 要不要独立的 `PUT /profiles/{id}/style`、`/layout`？

D4.3 已经有先例：`PUT /profiles/{id}/title`（因为重命名是「元信息」，与内容保存解耦，
且下拉列表里就能触发，不必先进编辑器）。

**样式与排版不应该照抄这个先例**，理由：

1. 它们与内容在**同一个编辑器、同一次保存**里产生（改颜色和改文字都会让 `dirty` 变 true），
   拆成两个端点就意味着「保存」要发两次请求 → 半保存状态（颜色存了、文字没存）；
2. 拆端点还会引出「两次请求之间用户切了简历」的竞态——D4.2 的 `requestSeq` 只保护读，
   写路径没有类似的闸门；
3. 内容保存本就是一条 `UPDATE`，多带三列不增加任何成本。

**结论：走 `PUT /profiles/{id}`，不新增端点。**
（若将来做「一键换肤」这种不打开编辑器的操作，再加 `/style` 也不迟——那时它确实是元信息操作。）

### 10.4 错误语义

| 情况 | 状态码 | code | 说明 |
|---|---|---|---|
| `paper_size` 不在枚举内 | 422 | `VALIDATION_ERROR` | pydantic `Literal` 自动拦 |
| `theme` 不在枚举内 | 422 | 同上 | 同上 |
| `layout_config.sections` 有重复 id 或未知 id | **422**（写入时严格） | 同上 | 写入是用户行为，应该报错让他改 |
| 读取时 `layout_config` 损坏 | **不报错**，降级默认 | — | 6.8(3)：读路径永不因装饰信息失败 |
| 简历不存在 / 他人简历 | 404 | `NOT_FOUND` | 沿用 D4.1 的「越权与不存在同码」 |

注意「写入严格、读取宽松」这个不对称是**刻意的**：
前者是用户输入（可修复），后者是数据状态（用户不可修复）。

### 10.5 【能省一半工作量的判断】生成接口不需要新增参数

有人会想「生成时把 `theme` / `paper_size` / `layout` 作为请求参数传过去」。
**不需要**——它们已经存在简历上了，`ResumeService.generate()` 拿到的 `profile`
就是这个 ORM 行，直接读三个新属性即可。

```python
content_html = self._generator.generate(
    profile=…, skills=…, experiences=…, title=…, educations=…,
    style=DocumentStyle(
        paper_size=profile.paper_size,
        theme=profile.theme,
        layout=resolve_layout(profile.layout_config),
    ),
)
```

**唯一的例外**：如果将来支持「不保存也能预览生成效果」，那才需要请求参数。
当前流程强制「先保存再生成」（`StylePanel.vue:35-53` 有 `window.confirm` 拦着），所以不需要。

---

## 11. 与既有约束的交叉影响（最容易漏的一节）

### 11.1 import-linter：新代码放哪一层

现有 5 条契约（`pyproject.toml:37-71`）规定的层次是
`api → services → repositories → integrations`，且 services 不能依赖 FastAPI、
api 不能依赖 repositories/integrations、repositories 不能依赖 services。

**排版/主题的纯逻辑放哪，有三个候选**：

| 位置 | 可行性 | 说明 |
|---|---|---|
| `app/integrations/document_style.py` | ✅ | 生成器要读预设表和顺序，放这里最顺手；但 **api 不能 import 它**，所以 API 层要用的枚举校验不能放这 |
| `app/core/document_style.py` | ✅ **推荐** | `core` 不在任何契约的层里，api / services / integrations 都能 import；放**纯常量 + 纯函数**（预设色板、默认顺序、`resolve_layout()`） |
| `app/schemas/profile.py` | ⚠️ 部分 | 适合放 pydantic 模型（`LayoutConfig` / `SectionLayout`），但不宜放色板常量与解析函数 |

**结论：pydantic 模型进 `schemas/profile.py`，预设与解析进 `core/`，
生成器从这两处 import。** 这样 `lint-imports` 的 5 条契约一条都不用改。

### 11.2 `copy_from_id` 深拷贝必须带上样式

`student_profile_repository.py:335-347` 的 `duplicate_profile()` 是**逐字段显式构造**
`StudentProfile(...)`。新列不加进去的后果：

- 若 ORM 列有 Python 侧 `default=`：静默变成默认值 → **复制出来的简历「换了个样」**；
- 若没有默认值：`NOT NULL` 插入直接崩（500）。

**建议：`paper_size` / `theme` 复制，`layout_config` 深拷贝（`copy.deepcopy`）。**
理由：「复制这份简历去投另一个岗位」的语义是「同一个样子、换点内容」，
若复制后颜色变了，用户会以为坏了。（反方观点：也许用户想要全新默认样式——但改颜色成本极低，
反向则要用户自己想起「哦我得把颜色改回来」。）

### 11.3 `create_profile()` 的显式 kwarg 也要补

`student_profile_repository.py:304-316` 同样是显式列举全部列。
新列必须在这里给出值（或依赖 Python 侧默认值），否则 INSERT 缺列。

**注意**：`server_default` 只对 `ALTER TABLE` 的老行生效，
**新 INSERT 时 SQLAlchemy 若没有设置该属性，会发送 `NULL` 而不是省略该列**。
所以 ORM 模型上必须同时写 `default=`（Python 侧），不能只靠 `server_default`。

### 11.4 `replace_profile_content()` 要写三列

`student_profile_repository.py:412-415` 只覆盖 `full_name` / `email` / `contact`，
docstring 明确说「`title`/`is_default`/`deleted_at` 不在这里改」。
新增的三列**属于内容（不是元信息）**，应该在这里一起写：
否则 `PUT /profiles/{id}` 保存后样式不生效，表现为「改了颜色，保存，刷新又变回去」。

### 11.5 本地草稿的兼容

`comparableOf()` 加入三个字段后，**旧草稿（没有这些字段）** 在
`JSON.stringify` 里是 `undefined` → 与服务器值不等 → 会被判定为「有未保存的修改」。

**处理**：`normalizeDraft()` / `readDraft()` 里给缺省值补齐
（`paper_size ?? 'A4'`、`theme ?? 'forest'`、`layout_config ?? null`），
否则老用户升级后第一次进编辑器会看到一个**假的「检测到未保存草稿」横幅**。

### 11.6 回收站与物理删除：零改动

`layout_config` 是父表列，跟着父行走；
`hard_delete` / `purge_expired` 走 `_CHILD_TABLES`（`resumes/educations/experiences/skills`），
**不需要动**。（这正是 6.7 第 3 条的优势。）

### 11.7 打印水印的位置

`ResumePreview.vue:544-551` 的打印态水印用 `right: 6mm; bottom: 4mm`（已经是 mm，很好）。
换纸张后水印位置不受影响，但**若采用 P2 的 `transform: scale`，绝对定位以未缩放坐标系为基准**——
`bottom: 4mm` 会跟着纸面一起缩放，位置仍然正确。**这点 P2 是安全的**，可以确认。

### 11.8 与 `PHASE2_BACKLOG` 的其它条目冲突

| 条目 | 冲突点 |
|---|---|
| 「纸面内容编辑回写机制」（已由 D3 解决） | 无 |
| 「Token 静默续期」 | 长表单 + 拖拽 = 更容易撞上 15 分钟过期；改版式时若正好掉线，`dirty` 的改动靠本地草稿兜底 |
| 「测试隔离问题修复」 | 无直接关系，但本轮新增的降级矩阵用例会**增加**测试库状态依赖，建议同批解决 |

---

## 12. 分阶段落地建议

排期原则：**先钉数据模型，再做视觉，最后做交互**。
理由是「交互可以反复改，schema 不能」——顺序若先做成「3 个标题列」，
后面加拖拽时就得迁一次数据。

### 12.1 阶段总览

| 阶段 | 内容 | 改动面 | 风险 | 依赖 |
|---|---|---|---|---|
| **D5.1** | 纸张尺寸（A4/Letter）+ `@page` + 真页数 | 迁移1列 · 生成器包裹层 · 预览容器重构 | 中低 | 无 |
| **D5.2** | 配色（CSS 变量化 → 预设 → 生成器内联） | 迁移1列 · 预览样式块 · StylePanel | 中低 | 建议在 D5.1 之后（纸面定尺后再调色，避免调两次） |
| **D5.3** | 排版（模型 → 标题 → 箭头 → 可选拖拽） | 迁移1列 · 生成器 · 预览 · 新面板 | **高** | 依赖 D5.1（纸面高度决定溢出判断） |
| **D5.4** | 分页与溢出（CSS 规则 → 页边界 → 可选压缩） | 纯 CSS + 测量 | 低 | 依赖 D5.3 |

### 12.2 D5.1 纸张（建议拆 3 个小步，每步可独立验收）

1. **`paper_size` 落库 + API + 生成器包裹层**（`<div class="paper-doc" data-paper="A4">`）——
   此时前端还没有选择器，但数据链路通了；
2. **`@page` 动态注入 + `.paper` 改 mm 定尺 + zoom 容器（P2）**——
   视觉变化最大的一步，验收方式：1440 / 1100 / 760 / 360 四个视口截图比对，比例都是 A4；
3. **页数测量**：用 `scrollHeight / paperContentHeight` 算出真实页数，
   替换 `ResumePreview.vue:207` 与 `StylePanel.vue:83` 的字面量。
   顺带修掉「窄屏显示 A4 但实际不是」的问题（2.1 第 3 点）。

### 12.3 D5.2 配色（3 个小步）

1. **纯重构：8 个色值 → CSS 变量**（无行为变化）。**这一步可以完全脱离后端独立合入**，
   验收方式是「改前改后截图逐像素比对」。建议单独一个 commit，方便回溯；
2. **`theme` 落库 + `StylePanel` 的选择器**（把「配色 青绿」那行换成 8 个色块）；
   前端把 `profile.theme` 映射成 CSS 变量写到 `.paper` 上；
3. **生成器内联变量**（`<div class="paper-doc" style="--paper-accent:…">`）。

> 为什么重构先于功能：如果先做功能，`theme` 一上线就要同时在「硬编码色值」和「变量」之间
> 做映射，等于把重构和功能纠缠在一起，出问题时分不清是哪边坏的。

### 12.4 D5.3 排版（4 个小步，前两步是重点）

1. **`layout_config` 落库 + 校验 + `PUT` 支持**——**先只做数据，不做 UI**。
   验收：pytest 覆盖 6.8(3) 的完整降级矩阵 + 10.2 的三态语义；
2. **自定义标题**（`StylePanel` 里 3 个输入框）。**这是性价比最高的一步**：
   改动小、用户感知强、完全不碰拖拽。**如果只做一件事，就做这个**；
3. **上/下箭头调序**（G3，无依赖）；
4. **可选：真拖拽**（G2 + 「排版模式」开关）。**建议等用户抱怨箭头不够用再做。**

### 12.5 D5.4 分页（2 个小步）

1. **CSS 分页规则**（`break-inside: avoid` / `break-after: avoid` / `orphans` / `widows`）——
   约 6 行，但能消掉最刺眼的「标题在页尾、正文在下一页」；
2. **只读页边界覆盖层**（Y4 的廉价版）+ 真实的「共 N 页」标签。

### 12.6 依赖关系（文字版）

```
D5.1(纸张) ──┬─→ D5.2(配色)          # 纸面定尺后调色，避免调两次
             └─→ D5.3(排版) ──→ D5.4(分页)
                     │
                     └─ D5.3.2(自定义标题) 可以提前插队 # 与纸张/配色无耦合
```

**可以提前插队的两项**：D5.2.1（CSS 变量化）和 D5.3.2（自定义标题）。
它们各自独立、风险低、用户感知明显，适合当作「等拍板期间就能做」的活。

---

## 13. 待人类拍板的完整问题清单

| # | 问题 | 选项 | 我的看法（仅供参考） | 影响面 |
|---|---|---|---|---|
| **Q1** | 样式跟简历还是跟用户？ | A 跟简历 / B 跟用户 / C 两级 | **A**（需求原话就是「不同岗位不同风格」；A 可升级为 C） | 迁移列的位置 |
| **Q2** | 纸张做几档？ | A4 / A4+Letter / 再加 Legal | **A4+Letter**（一高一矮，覆盖两个溢出方向，回归面积可控） | 枚举 + 版式回归 |
| **Q3** | 是否支持自定义 mm / A5？ | 是 / 否 | **否**（招聘系统与打印店都不接收非标尺寸） | 枚举 + 校验 |
| **Q4** | 纸面是否改 mm 定尺 + 缩放（P2）？ | P1 最小 / **P2** / P3 最正 | **P2**（真 WYSIWYG；P1 会让「所见」与「打印」继续有偏差） | 预览容器重构 |
| **Q5** | 打印边距是否改为 `@page margin:0` + 纸内 mm padding？ | 是 / 否 | **是**（现在三层边距叠加是「打印多一圈白」的根因） | 打印样式 |
| **Q6** | 配色用预设还是任意 hex？ | A 预设 / B hex / C 两者 | **A**（对比度在设计期解决；前端无测试框架，B 的双端漂移无法自动化发现） | 存储类型 + 校验 |
| **Q7** | 预设几套？ | 6 / **8** / 更多 | **8**（表见 5.2，全部通过 AA 正文对比度） | 设计 |

| **Q8** | 主题色是否也用于「姓名大字」？ | 是 / 否 | **否**（姓名应保持最高对比度 13:1 的墨黑；主题色只修饰 `h2` 与页眉线） | 变量映射 |
| **Q9** | 排版数据模型选哪个？ | **A JSON 列** / B1 侧表单列 / B2 侧表分行 / C 3 列 | **A**（读写原子、级联免费、copy_from 一行、有 DATABASE.md §5.3 背书） | 迁移 + 三处代码 |
| **Q10** | 是否支持「隐藏某个模块」？ | 是 / 否（第一版） | **否，单独评估**（全隐藏会与生成接口的完整性校验打架） | 模型 + 校验 |
| **Q11** | 自定义标题长度上限？ | 8 / **12** / 20 | **12**（最窄屏 360px 下 15 字不折行，留 3 字余量） | 校验 + UI |
| **Q12** | 「复制简历」是否复制样式/排版？ | 复制 / 不复制 | **复制**（「复制」的语义就是同一个样子；改颜色成本极低，反向要用户自己想起来改回） | `duplicate_profile` |
| **Q13** | 排序用箭头还是拖拽？ | G3 箭头 / G2 拖拽 / 先 G3 后 G2 | **先 G3**（只有 3 个模块，拖拽的收益是手感，成本是依赖 + 手势冲突 + 跨浏览器调试） | UI + 依赖 |
| **Q14** | 是否引入 `vuedraggable`？ | 是 / 否 | **暂不**（若 Q13 选先 G3，则本轮不需要；引入时务必先验证与 `transform: scale` 的冲突） | package.json + 体积 |
| **Q15** | 拖拽是否要独立「排版模式」？ | 是 / 否 | **是**（不做就会有「拖拽变选中文字」这个必然发生的 bug） | 交互 |
| **Q16** | 是否硬限制一页？ | 是 / 否 | **否**（会逼用户删真实经历；且后端无法验证版面） | 保存路径 |
| **Q17** | 是否提供「压缩到一页」？ | 是（下限 0.85）/ 否 | **可作为后续增强**（必须用户手动触发，绝不自动；低于 0.85 正文小于 9pt） | 导出路径 |
| **Q18** | 是否做真分页预览？ | 覆盖层（廉价）/ 插占位（贵）/ 不做 | **先覆盖层**（插占位会破坏 `contenteditable`） | 预览层 |
| **Q19** | 样式/排版改动后是否自动回到「实时预览」并提示重新生成？ | 是 / 否 | **是**（否则用户会以为「已生成」视图里的旧颜色是 bug） | store + 提示 |
| **Q20** | 是否给前端引入测试框架（vitest）？ | 是 / 否 | **建议列入技术债**（本文档多处「必须两端一致」的约束，现在只有一端有测试） | 工程基建 |

---

## 14. 本 ADR 未做的事

1. **未写任何业务代码**，未新增/修改 `.py` / `.ts` / `.vue`；
2. **未改数据库**，未新增迁移文件，未在任何库上执行 DDL；
3. **未安装依赖**（`package.json` 未改，未装 `vuedraggable`）；
4. **未做真机验证**：`@page` 在 Chrome「另存为 PDF」的实际页尺寸、Safari 的打印行为、
   移动端 `contenteditable` + 拖拽的手势冲突，**都需要落地时实测**（本文档只能给出规范层面的推演）；
5. **未验证打印机**：物理打印的不可打印区（通常 4–6mm）会吃掉一部分 mm padding，
   真实效果必须用真打印机或真实 PDF 核对；
6. **未做性能评估**：JSON 列的读写性能在「单用户 20 份简历」量级下不构成问题，
   未做压测，也未做 `load_only()` 优化；
7. **未评估国际化**：`Letter` 的引入会让「中英文混排的换行规则」差异暴露（CJK 可在任意字符断行，
   英文按词断行），可能需要 `word-break` / `line-break` 的额外调整。

---

## 15. 已知遗留问题（与本轮改造的交叉影响）

| 遗留问题 | 与本轮的关系 |
|---|---|
| **前端无测试框架**（`package.json` 无 vitest） | 本文档有 4 处「必须两端一致」的约束（颜色、顺序、标题、页数），**只有后端能测**。这是最大的质量缺口，建议单独立项 |
| **`content_html` 只写不读**（`/resumes` 只有 `POST /generate`） | 主题/排版做进 HTML 后，**在出现读接口之前无法自动化验证**；建议 D5.2 时顺手加 `GET /resumes/{id}`（只读，风险极低），让生成物可被验证 |
| **「页数」标签是字面量** | 本轮必须先修（4.2/12.2 第 3 步），否则换纸张和调顺序会让它开始说谎 |
| **`@media print` 里 `.paper { min-height: 0 }`**（`ResumePreview.vue:539`） | 改成 mm 定尺后这行要重新校：`min-height:0` 会让打印高度塌陷，必须显式给 `height: 297mm` |
| **`grade` 列保留但业务已下架** | 与排版无关，但它是「加了列却没人维护」的先例——新增三个样式列时，务必同步写进 `docs/DATABASE.md` 与 `ProfileRead`，不要再留一个孤儿列 |
| **测试库必须置空才能跑 pytest**（`test_auth` 的 `drop_all`） | D5.3 的降级矩阵用例会让这个纪律更重要；建议与「测试隔离」条目一起解决 |
| **A4 比例在窄屏失效（<509px）** | 已在 2.1 记录；若选 P2 则自动修复，若选 P1 则需显式放弃 `aspect-ratio` 改用固定尺寸 |

---

## 16. 结论摘要（一屏能读完的版本）

- 纸张和配色是**换皮肤**：各加 1 列、各改 1 处样式，风险中低。
  真正要小心的是 `@page`（不能靠 CSS 变量）和三态 PUT 语义（旧前端不能把主题清掉）。
- 排版是**换骨架**：1 个 JSON 列（选项 A），3 处模板改造，
  以及一个绕不开的交互难题（拖拽 vs `contenteditable`）。
- **三件事都不要一次做完**。顺序：纸张 → 配色 → 排版 → 分页；
  其中「CSS 变量化」和「自定义标题」可以立刻插队做。
- 如果需要一份「最小可交付」，是：**纸张 A4/Letter + 8 套预设色 + 自定义 3 个标题 + 上下箭头调序**。
  这三样加起来已经是完整的 DIY 体感，且**不需要引入任何依赖**。
- 拖拽本身不增加能力，只增加手感；它的成本（依赖、手势冲突、缩放冲突、可访问性）
  与收益（3 个元素的排序）不成比例，建议押后。

---

> **本文档不替人类做决定。** 第 13 节的 20 个问题里，
> Q1 / Q4 / Q6 / Q9 / Q13 是「选错就要返工」的五个，其余都是可逆的实现细节。
