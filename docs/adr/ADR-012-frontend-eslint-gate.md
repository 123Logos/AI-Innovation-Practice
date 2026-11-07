# ADR-012：前端 lint 工具链与 CI 闸门

- 状态：已接受
- 日期：2026-11-07
- 决策人：本项目人类负责人（批准原话：「按你推荐的方式解决前端 lint 的问题」，推荐内容为「补 flat config + 一次全量修 + 上闸，照 ruff format 闸门的顺序」）

## 背景

`frontend/package.json` 从建立之初就宣称 `"lint": "eslint . --ext .ts,.vue"`，但仓库里从来没有
任何 eslint 配置文件。ESLint 9（本机实测 9.39.4）已不再回落查找 `.eslintrc.*`，脚本一跑就死：

```
Oops! Something went wrong! :(
ESLint: 9.39.4
ESLint couldn't find an eslint.config.(js|mjs|cjs) file.
```

`.github/workflows/ci.yml` 的前端 job 只有 `Frontend unit tests` / `Frontend type check` /
`Frontend build` 三步，**没有 lint 步骤**。于是前端处于最坏的一种状态：脚本对外宣称存在、
实际不可运行、闸门也不存在——按 AGENTS.md 第 7 节，这属于「假装存在」，要么补真，要么删掉别骗人。

本轮先用「只装 package.json 里已有的包」能不能凑出闸门做了实测（2026-11-07，探针配置文件已删）：

| 探针 | 结果 |
|---|---|
| core-only flat config（`@eslint/js` recommended）跑 `src/api/client.ts` | `File ignored because no matching configuration was supplied`，**退出码 0** |
| 同一 config 强制 `files: ['**/*.ts','**/*.vue']` | `22:8 error Parsing error: Unexpected token AxiosInstance` |
| 同一 config 跑整个 `frontend/` | 只有 **1 个文件**产出问题，且它是构建产物 `dist/assets/index-*.js` |

结论：ESLint 核心的 espree 解析器读不了 TypeScript 类型标注，也读不了 `.vue` SFC。用现有依赖
凑出来的「闸门」会**静默跳过全部 16 个 `.vue` + 26 个 `.ts` 源文件、只对压缩产物报错、并且退出码为 0**。
这不是宽松的闸门，是零覆盖的闸门——把假脚本换成假闸门不解决问题。因此必须新增解析器与规则集，
而「技术栈新增依赖」按 AGENTS.md §4.6 必须先有 ADR，故有本文。

## 决策

1. **新增 4 个 devDependencies**（全部只在开发/CI 期使用，不进浏览器 bundle，运行时依赖零变化）：

   | 包 | 作用 |
   |---|---|
   | `typescript-eslint` | `.ts` 解析器 + TS 规则集；`.vue` 内 `<script lang="ts">` 也靠它 |
   | `eslint-plugin-vue` | `.vue` SFC 解析（内含 `vue-eslint-parser`）+ Vue 专用规则 |
   | `@eslint/js` | `js.configs.recommended` 规则来源 |
   | `globals` | browser / node 全局变量表，供 `no-undef` 判定 |

   `@eslint/js` 与 `globals` 当前已作为 eslint 的**传递依赖**躺在 `node_modules` 里。本 ADR 选择把它们
   **显式写进 devDependencies**，而不是蹭传递依赖：直接 import 的包必须出现在清单上，否则将来 npm
   调整 eslint 的依赖树，配置文件会在同一份代码下突然解析失败。

   `eslint` 本体（`^9.17.0`）已在清单中，不动版本号。

   版本不是「一律 latest」：`npm i -D @eslint/js`（不带版本）实测拉到 `10.0.1`，其 peer 要求
   `eslint@^10.0.0`，与清单里的 `eslint@^9.17.0` 直接 ERESOLVE 失败。因此 `@eslint/js` 按 major=9 安装，
   清单写入 `^9.39.5`（本机 eslint 为 9.39.4）。`typescript-eslint@8.72.1` 与
   `eslint-plugin-vue@10.12.0` 的 peer 都写明接受 `^9.0.0`，可取 latest；
   `vue-eslint-parser@10.4.2` 随插件作为传递依赖落地。**闸门自己先要能装上**，这一段是安装实测，不是推测。


2. **配置文件**：`frontend/eslint.config.js`，flat config 格式（ESLint 9 唯一支持的格式）。

3. **规则集层级**：`js.configs.recommended` + `typescript-eslint` 的 `recommended` +
   `eslint-plugin-vue` 的 `flat/strongly-recommended`，再叠插件自带的 `no-layout-rules` 关闭格式化层。

   这里有一条**写了又改的记录**：本文初稿写的是「strongly-recommended 不含模板格式化规则，
   格式化在更高的 recommended 层级」。实测打脸（遍历 `pluginVue.configs` 的规则名）：
   `flat/strongly-recommended` 的 110 条规则里**已经包含** `vue/html-indent`、`vue/html-self-closing`、
   `vue/max-attributes-per-line`、`vue/singleline-html-element-content-newline` 四条格式化规则。
   因此改用插件自带的 `no-layout-rules`（实测 48 条布局规则设为 off）来精确实现原意——
   「要正确性层，不要模板重排」。用官方关闭层而不是手写 4 条 off：将来插件新增格式化规则也会被一并关掉。

4. **必须 `ignores: ['dist/**']`**：上面第三行实测已经证明，不排除 `dist/` 的话闸门的第一批报错来自构建产物。

5. **`lint` 脚本改为 `eslint .`**：实测 ESLint 9.39.4 的 `--ext` **仍然可用**（`--help` 里列着，
   也不报错），带不带它结果完全一致——都是 45 个文件、0 个问题。也就是说覆盖范围由配置里的
   `files` 决定，`--ext` 在这里是**冗余的第二真相来源**：将来加一类文件要同时改脚本和配置，
   漏一个就静默少查一类。所以删掉它，而不是因为它报错。（本文初稿写的是「`--ext` 已废弃」，
   与实测不符，已改。）

6. **CI 增加 `Frontend lint` 步骤**，位置与后端一致（先 format/lint，再 test）。

7. **提交顺序沿用 ruff format 闸门的纪律**：ADR → 配置文件 + 依赖 → 修掉闸门抓到的真问题 → CI 步骤。
   即「先拿到干净基线，闸门才上线」，不允许出现「闸门上线但长期红」或「闸门和修法混在一个提交里、
   diff 看不出哪些是规则要求」两种情况。

   实测基线比预想干净得多：42 个源文件只抓到 3 个问题，且三个都是真问题（见「后果」），
   所以本轮**没有**产生需要进 `.git-blame-ignore-revs` 的大规模机械修提交；初稿为 ruff format
   那次预留的这一步，因不存在该提交而取消。


## 后果

- `npm run lint` 从死脚本变成真闸门。实测进入检查 **45 个文件**（16 个 `.vue` + 28 个 `.ts` +
  `eslint.config.js` 自己），其中 `src/**` 42 个。
- 本地与 CI 都需要重新 `npm ci` 拉这 4 个包；CI 前端 job 时长略增。
- **闸门上线当天就抓到 3 个真问题**，全部与格式无关：
  1. `frontend/src/stores/__tests__/job.spec.ts:146` 的 `aiError()` **定义了但从未被调用**——
     C2 轮那七种 AI 失败情形实际是各用例内联构造错误体的。已删除。
     （曾考虑反向修法「把用例改成调用它」，不采用：那是借 lint 之名重构测试，§7.8。）
  2. `frontend/src/env.d.ts:6` 的 `DefineComponent<{}, {}, unknown>`：`{}` 允许 `0`、`""` 等任意非空值，
     改成 `Record<string, unknown>`，`vue-tsc` 仍全绿。这是 Vue 官方文档里的写法，说明它只是**沿抄**、
     不是被验证过。
  3. 同一条规则下 `ResumePreview.spec.ts` 桩类的构造参数 `_callback` 被误报——这条**不是代码问题**，
     是规则默认不认识「下划线 = 故意的未用参数」这一约定。处理方式是给规则加
     `argsIgnorePattern: '^_'`，**只豁免参数**，变量/函数不豁免——否则正好把第 1 条那种死函数藏起来。
- **闸门有效性经过反向验证**（不是「0 问题所以通过」的空转）：临时植入两个探针文件后，
  `vue/no-mutating-props`、`vue/require-v-for-key`、`@typescript-eslint/no-explicit-any`、
  `@typescript-eslint/no-unused-vars`（变量）、`no-cond-assign`、`no-debugger` 全部如实报错；探针已删。
- **两条规则按设计不生效，覆盖在别处**：`no-undef` 由 typescript-eslint 主动关掉，同一个未定义标识符
  实测由 `vue-tsc` 报 `TS2304`；`no-cond-assign` 的默认档 `except-parens` 允许被括号包裹的赋值。
  即 lint 与 type check 合起来才是完整覆盖，单看任何一边都会高估自己。
- **边界要说清**：lint 只保证「不违反规则」，不保证组件行为正确。行为仍由 vitest 用例负责，
  本 ADR 不减少任何既有测试要求，也不允许用 lint 通过来代替测试输出。
- 仍未做：前端**格式化**（Prettier 一类）。`.editorconfig` 的现状是刻意不立法行尾与模板缩进，
  本 ADR 不改变这一决定。

## 替代方案

- **只用清单里已有的包凑一个 core-only flat config**：不采用。实测见背景表格——它跳过全部源文件、
  退出码 0、只对压缩产物报错。等于把假脚本换成假闸门。
- **删掉 `lint` 脚本，承认前端不做静态检查**：诚实且零依赖，曾认真考虑。不采用，因为 AGENTS.md §4.5
  要求流水线含 lint，而前端长期零覆盖会让「CI 全绿」的含金量系统性低于后端；老板已拍板补真。
- **保留 `flat/strongly-recommended` 的模板格式化规则**（不叠 `no-layout-rules`）：不采用——那 4 条
  规则会把一次 lint 修变成模板重排 diff，而项目没有格式化器，规则与编辑器实际行为互相打架。
  若将来引入 Prettier，应把格式化整体交给 Prettier 并从 ESLint 侧关闭对应规则（届时有新 ADR）。
- **`eslint --fix` 全量重排后再逐条人工判断**：不采用为本轮主路径。实测 3 个问题**没有一个可自动修**，
  所以「先跑 --fix」这一步在本轮是空操作；仍然保留 `--fix` 作为日常手段，但不拿它当基线来源。
- **Biome / Oxlint 替代 ESLint**：工具本身可用，但 Vue SFC 支持成熟度与既有生态不如
  `eslint-plugin-vue`，且会引入与后端 ruff 无关的新工具心智。不采用。

