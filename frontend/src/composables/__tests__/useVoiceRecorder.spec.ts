/**
 * 录音器（B11b / `ADR-030` §2.2）的单测。
 *
 * jsdom 没有 `MediaRecorder`，也没有麦克风，所以这里用两只替身把**真实逻辑**跑通：
 * 一只假 `MediaRecorder`（按真实事件顺序发 `dataavailable` → `stop`），
 * 一条假音轨（只用来断言「停录之后轨道有没有被停掉」）。
 *
 * 四条判据都是「看得见的行为」：
 * 1. 不支持的浏览器 → `supported=false` + 一句能执行的提示，而不是一个点了没反应的按钮；
 * 2. 正常一段 → 拿到 `Blob`，且音轨被 `stop()`（不停的话标签页上的录音红点会一直亮）；
 * 3. 到 120 秒 → 自动停并明说「请分段说」，**不是静默截断**；
 * 4. 超过 8 MiB → 当场丢弃并说明，不把注定失败的音频发出去。
 */

import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ASR_MAX_BYTES, ASR_MAX_SECONDS } from '@/api/voice'
import {
  VOICE_DENIED_MESSAGE,
  VOICE_TIMEOUT_MESSAGE,
  VOICE_TOO_BIG_MESSAGE,
  VOICE_UNSUPPORTED_MESSAGE,
  useVoiceRecorder,
} from '@/composables/useVoiceRecorder'

/** 这一段录音的字节数；每个用例按需改。 */
let payloadSize = 2048
const getPayload = (): Blob => new Blob(['x'.repeat(payloadSize)], { type: 'audio/wav' })

class FakeMediaRecorder {
  static last: FakeMediaRecorder | null = null

  state: 'inactive' | 'recording' = 'inactive'
  mimeType = 'audio/wav'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null

  constructor(readonly stream: unknown) {
    FakeMediaRecorder.last = this
  }

  start(): void {
    this.state = 'recording'
  }

  /** 真浏览器一定是「先发数据、再发 stop」，顺序反了组件就会拿到空 Blob。 */
  stop(): void {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.ondataavailable?.({ data: getPayload() })
    this.onstop?.()
  }
}

let trackStop: ReturnType<typeof vi.fn>
let getUserMedia: ReturnType<typeof vi.fn>

function installRecorderSupport(): void {
  trackStop = vi.fn()
  getUserMedia = vi.fn().mockResolvedValue({
    getTracks: () => [{ stop: trackStop }],
  })
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  })
}

function mountRecorder() {
  let api!: ReturnType<typeof useVoiceRecorder>
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useVoiceRecorder()
        return () => h('div')
      },
    }),
  )
  return { api, wrapper }
}

beforeEach(() => {
  payloadSize = 2048
  FakeMediaRecorder.last = null
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  // 把 mediaDevices 还原成 jsdom 的原始状态（它本来就没有这个属性）。
  Reflect.deleteProperty(navigator, 'mediaDevices')
})

describe('录音器', () => {
  it('浏览器不支持录音：标记为不支持，并给一句能执行的提示', async () => {
    const { api } = mountRecorder()

    expect(api.supported.value).toBe(false)
    const started = await api.start()

    expect(started).toBe(false)
    expect(api.message.value).toBe(VOICE_UNSUPPORTED_MESSAGE)
    expect(api.recording.value).toBe(false)
  })

  it('拒绝麦克风权限：不进入录音态，提示去设置里允许', async () => {
    installRecorderSupport()
    getUserMedia.mockRejectedValueOnce(new Error('denied'))
    const { api } = mountRecorder()

    const started = await api.start()

    expect(started).toBe(false)
    expect(api.message.value).toBe(VOICE_DENIED_MESSAGE)
    expect(api.recording.value).toBe(false)
  })

  it('录一段并停止：拿到音频，且音轨被停掉（否则红点一直亮）', async () => {
    installRecorderSupport()
    const { api } = mountRecorder()

    expect(await api.start()).toBe(true)
    expect(api.recording.value).toBe(true)
    const blob = await api.stop()

    expect(blob).toBeInstanceOf(Blob)
    expect(blob?.size).toBe(payloadSize)
    expect(api.recording.value).toBe(false)
    expect(trackStop).toHaveBeenCalledTimes(1)
  })

  it('到 120 秒自动停，并明说「请分段说」', async () => {
    installRecorderSupport()
    vi.useFakeTimers()
    const { api } = mountRecorder()

    await api.start()
    await vi.advanceTimersByTimeAsync((ASR_MAX_SECONDS + 1) * 1000)

    expect(api.recording.value).toBe(false)
    expect(api.message.value).toBe(VOICE_TIMEOUT_MESSAGE)
    expect(FakeMediaRecorder.last?.state).toBe('inactive')
  })

  it('超过 8 MiB：当场丢弃并说明，不返回 Blob', async () => {
    installRecorderSupport()
    payloadSize = ASR_MAX_BYTES + 1
    const { api } = mountRecorder()

    await api.start()
    const blob = await api.stop()

    expect(blob).toBeNull()
    expect(api.message.value).toBe(VOICE_TOO_BIG_MESSAGE)
  })

  it('取消：不产出音频，音轨同样被停掉', async () => {
    installRecorderSupport()
    const { api } = mountRecorder()

    await api.start()
    api.cancel()

    expect(api.recording.value).toBe(false)
    expect(trackStop).toHaveBeenCalledTimes(1)
    // 取消之后再点停止不该拿到半截音频。
    expect(await api.stop()).toBeNull()
  })
})
