/**
 * ESLint flat config —— 依据 docs/adr/ADR-012。
 *
 * 为什么是这四个包：ESLint 9 核心只有 espree，读不了 TypeScript 类型标注，也读不了 `.vue` SFC。
 * 实测（ADR-012 背景表）：core-only 配置对 `src/api/client.ts` 报「no matching configuration」
 * 并且退出码 0 —— 那就是一个只对 `dist/` 压缩产物报错、对 42 个源文件零覆盖的假闸门。
 *
 * 为什么用 strongly-recommended 再叠 no-layout-rules：strongly-recommended 已经包含 essential
 * （正确性层：`v-for` 缺 key、直接改 props、未注册组件等），但它同时带进 4 条模板格式化规则
 * （html-indent / html-self-closing / max-attributes-per-line / singleline-html-element-content-newline）。
 * 项目没有前端格式化器，让 lint 兼职重排模板会把一次全量修变成大 diff。`no-layout-rules` 是
 * eslint-plugin-vue 自带的关闭层，用它而不是自己手写 4 条 off：将来插件新增格式化规则也会被一并关掉。
 *
 * 为什么 ignores 里必须有 dist：不排除时闸门的第一批报错来自构建产物（实测）。dist 已在 .gitignore。
 */

import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },

  js.configs.recommended,
  tseslint.configs.recommended,
  pluginVue.configs['flat/strongly-recommended'],
  pluginVue.configs['no-layout-rules'],

  {
    // 业务代码跑在浏览器里；`src/api/client.ts` 用 localStorage，`stores` 碰 document。
    files: ['src/**/*.{ts,vue}'],
    languageOptions: {
      globals: globals.browser,
      // 让 vue-eslint-parser 把 `<script lang="ts">` 交给 TS 解析器。
      parserOptions: { parser: tseslint.parser },
    },
  },

  {
    // vite.config.ts / vitest.config.ts 在 node 里跑，不该按浏览器全局判定。
    files: ['*.config.ts'],
    languageOptions: {
      globals: globals.node,
      parserOptions: { parser: tseslint.parser },
    },
  },

  {
    rules: {
      // 只对**参数**放行 `_` 前缀：桩类（如 ResumePreview.spec.ts 的 StubResizeObserver）
      // 必须接住构造签名里的 callback 又不能用它，下划线是这个意思。
      // 变量/函数不加这条豁免——本轮抓到 job.spec.ts 里一个从未被调用的 `aiError`，
      // 加了豁免就正好把它藏起来。
      '@typescript-eslint/no-unused-vars': [
        'error',
        { args: 'after-used', argsIgnorePattern: '^_' },
      ],
    },
  },
)
