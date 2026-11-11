/**
 * Vitest 配置（前端单测）。
 *
 * 为什么单独一份、而不是 `mergeConfig(viteConfig, …)`：
 * - `vite.config.ts` 里的 `server.proxy` 指向本地后端、`host: 0.0.0.0` 对外监听，
 *   测试既不需要也不该依赖它们；单测要能在一台没有后端进程的机器上跑绿。
 * - 显式写死 `@` 别名，测试和构建的模块解析规则就**不靠继承**：将来 Vite 配置
 *   调了别名，这里不会跟着悄悄变化，而是直接报「找不到模块」。
 *
 * 环境用 `jsdom`：store 会碰 `localStorage`（草稿）和 `document.documentElement`
 * （全局换肤），这两样是它的真实依赖，用 node 环境测等于测了个假的分支。
 */

import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  // 本轮只测 store，但保留 vue 插件：将来加组件测试（@vue/test-utils）不必再改配置。
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    // 用例里显式 `import { describe, it, expect } from 'vitest'`，
    // 不开 globals：避免 tsconfig 里再补一份 global 类型声明，也让依赖来源可见。
    globals: false,
    include: ['src/**/__tests__/**/*.spec.ts'],
    // 关掉 VTU 对 <Transition> 的默认桩（理由与 `settled()` 助手都在这个文件里）。
    setupFiles: ['./src/__tests__/setup.ts'],
    // 每个用例都应该是独立的：mock 与 spy 不留到下一个用例。
    clearMocks: true,
    restoreMocks: true,
  },
})
