/**
 * 单测的全局装配（批次 F4）。
 *
 * 只做一件事：**关掉 `@vue/test-utils` 对 `<Transition>` 的默认桩**。
 * 那个桩会渲染一个真实的 `<transition-stub>` 元素，于是
 * 「`.user-area` 的直接子项是哪三个」「`.app-content` 的直接子项是不是视图」
 * 这类结构判据量到的是测试工具自己插的一层，不是产品的 DOM。
 * 关掉之后跑的是真的过渡：jsdom 没有布局引擎，`getComputedStyle` 给不出时长，
 * Vue 因此在两帧之后收尾——断言「关掉之后不在了」的地方要等那两帧（`settled()`）。
 */
import { config } from '@vue/test-utils'

config.global.stubs = {
  ...config.global.stubs,
  transition: false,
  'transition-group': false,
}

/** 等过渡收尾：Vue 用两层 `requestAnimationFrame` 判定「没有时长就立刻结束」。 */
export async function settled(): Promise<void> {
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  await new Promise((resolve) => setTimeout(resolve, 0))
}