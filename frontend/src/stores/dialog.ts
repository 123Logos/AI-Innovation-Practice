/**
 * 页面内弹层的唯一入口（批次 F1b，守则 C6 / L3）。
 *
 * 为什么要有这个文件：`window.confirm` / `window.alert` 是**同步阻塞**的——它把 JS 主线程
 * 钉在那里等鼠标，于是浏览器自动化通道（CDP）在这一步既拿不到返回值、也发不出后续指令，
 * 整条验收流程停在弹窗上不能继续。`JobDetailView.vue` 的自建弹层先记录了这次事故，
 * 本文件把那个先例变成全站只有一个形态：确认是一次普通 click 事件，可读、可点、可截图、可断言。
 *
 * 为什么状态放在 store 而不是各视图自己 `ref`：六处调用点里有一处在 `stores/resume.ts` 的
 * 动作内部（切换简历前的未保存拦截），那里没有模板可以渲染；另一处调用方（`AppTopBar`）
 * 打开着下拉菜单。散着写就会出现六套遮罩、六套 Esc 行为，正是 F2 要收的东西。
 *
 * 同时只有一个弹窗：后来的请求按「取消」处理并立刻返回 false。这不是偷懒——每个调用点
 * 拿到 false 就停在自己的动作之前，世界状态没被动过，用户再点一次就是新的一次。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

/** `confirm` = 两个按钮（取消 + 确认）；`notice` = 一个按钮（知道了）。 */
export type DialogKind = 'confirm' | 'notice'

/** 确认按钮的颜色。危险动作（删除、彻底删除）用 `danger`，其余用 `default`。 */
export type DialogTone = 'default' | 'danger'

export interface DialogRequest {
  kind: DialogKind
  /** 弹窗标题：一句话说清「要做什么」。 */
  title: string
  /** 正文：一句话说清**后果**，尤其是能不能撤回。 */
  body: string
  /** 标题里出现的关键对象（如被删的简历名），单独一行显示，方便用户核对。 */
  subject?: string
  confirmLabel: string
  cancelLabel: string
  tone: DialogTone
}

/** 调用点只需要给这三格，按钮文字与颜色有默认值。 */
export type DialogAskInput = Pick<DialogRequest, 'kind' | 'title' | 'body'> &
  Partial<Pick<DialogRequest, 'subject' | 'confirmLabel' | 'cancelLabel' | 'tone'>>

export const useDialogStore = defineStore('dialog', () => {
  const pending = ref<DialogRequest | null>(null)

  /** 渲染用：把一次请求和它的 resolve 绑在一起，弹窗只管点按钮。 */
  let resolver: ((ok: boolean) => void) | null = null

  const isOpen = computed(() => pending.value !== null)

  function ask(input: DialogAskInput): Promise<boolean> {
    if (pending.value !== null) {
      // 已经有一个弹窗在等用户：不叠第二个，也不让后来的请求偷偷代替前一个。
      return Promise.resolve(false)
    }
    pending.value = {
      kind: input.kind,
      title: input.title,
      body: input.body,
      subject: input.subject,
      confirmLabel: input.confirmLabel ?? (input.kind === 'confirm' ? '确认' : '知道了'),
      cancelLabel: input.cancelLabel ?? '取消',
      tone: input.tone ?? 'default',
    }
    return new Promise<boolean>((resolve) => {
      resolver = resolve
    })
  }

  /** 确认类：`true` = 用户点了确认，`false` = 取消 / 关掉 / Esc。 */
  function askConfirm(
    title: string,
    body: string,
    options: {
      subject?: string
      confirmLabel?: string
      cancelLabel?: string
      tone?: DialogTone
    } = {},
  ): Promise<boolean> {
    return ask({
      kind: 'confirm',
      title,
      body,
      subject: options.subject,
      confirmLabel: options.confirmLabel,
      // 取消按钮的文字必须能改：三个调用点的「取消」是三件不同的事
      // （先不删 / 先不切换 / 先不生成），全写「取消」就把三种不同的停手压成一句。
      cancelLabel: options.cancelLabel,
      tone: options.tone,
    })
  }

  /** 提示类：只有一个「知道了」，返回 `Promise<void>`（调用方不等它做别的事）。 */
  function askNotice(title: string, body: string): Promise<void> {
    return ask({ kind: 'notice', title, body }).then(() => undefined)
  }

  function settle(ok: boolean): void {
    pending.value = null
    const current = resolver
    resolver = null
    current?.(ok)
  }

  /** 登出 / 路由强拆时兜底：不让一个没人应答的 Promise 悬着。 */
  function close(): void {
    settle(false)
  }

  return { pending, isOpen, ask, askConfirm, askNotice, settle, close }
})
