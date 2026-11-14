/**
 * 录音器（B11b / FR-07，`ADR-030` §2.2）。
 *
 * 只做一件事：把麦克风变成一段 `Blob`，并在**两个硬上限**处主动停下。
 *
 * 三条刻意的取舍：
 *
 * 1. **到 120 秒自动停，不静默截断**。裁掉超出部分是「你以为说了、其实没录上」，
 *    而用户下一句追问会立刻暴露这个谎言。到点就停、并明说「这一段太长了，请分段说」。
 * 2. **超体积直接丢弃**（8 MiB）。这类失败通常来自很长的无损录音，重新编码需要新依赖，
 *    不在这一批；能做的只有明确告诉用户「这段没录成，请说短一点」。
 * 3. **用完立刻释放麦克风与对象**：`stop()` 后轨道要 `stop()`，否则浏览器标签页上那个
 *    红点会一直亮着——用户会以为还在录，这是最容易让人失去信任的一种 bug。
 *
 * 不支持的浏览器（老 Safari / 部分内置 WebView）**直接返回 false**，由调用方把按钮
 * 换成「这个浏览器不支持录音，请用文字作答」。不做二次兼容：这一批要证明「能听见」，
 * 不是「什么环境都能录」。
 */

import { onBeforeUnmount, ref } from 'vue'

import { ASR_MAX_BYTES, ASR_MAX_SECONDS } from '@/api/voice'

export const VOICE_UNSUPPORTED_MESSAGE = '这个浏览器不支持录音，请用文字作答。'
export const VOICE_DENIED_MESSAGE = '没有拿到麦克风权限，请在浏览器设置里允许后重试。'
export const VOICE_TIMEOUT_MESSAGE = `单段最长 ${ASR_MAX_SECONDS} 秒，到点已自动停止。请分段说。`
export const VOICE_TOO_BIG_MESSAGE = '这段录音太长了，没能上传。请说短一点再试。'
export const VOICE_EMPTY_MESSAGE = '这一段没有录到声音，请重试或改用文字。'

/** 浏览器是否具备录音能力（判据只有这一处，组件不重复判断）。 */
export function isRecordingSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices?.getUserMedia === 'function' &&
    typeof MediaRecorder !== 'undefined'
  )
}

export function useVoiceRecorder() {
  const supported = ref(isRecordingSupported())
  const recording = ref(false)
  const elapsed = ref(0)
  /** 可见提示（权限 / 超时 / 超体积 / 空录音）；调用方原样上屏。 */
  const message = ref('')

  let recorder: MediaRecorder | null = null
  let stream: MediaStream | null = null
  let chunks: Blob[] = []
  let timer: ReturnType<typeof setInterval> | null = null
  let startedAt = 0
  let settle: ((blob: Blob | null) => void) | null = null

  function clearTimer(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  /** 停掉麦克风轨道：不做这一步，标签页上的录音指示会一直亮着。 */
  function releaseStream(): void {
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
  }

  async function start(): Promise<boolean> {
    message.value = ''
    if (!supported.value) {
      message.value = VOICE_UNSUPPORTED_MESSAGE
      return false
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      message.value = VOICE_DENIED_MESSAGE
      return false
    }

    chunks = []
    recorder = new MediaRecorder(stream)
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) chunks.push(event.data)
    }
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: recorder?.mimeType || 'audio/wav' })
      clearTimer()
      releaseStream()
      recording.value = false
      const result =
        blob.size === 0 ? null : blob.size > ASR_MAX_BYTES ? null : blob
      if (blob.size === 0) message.value = VOICE_EMPTY_MESSAGE
      else if (blob.size > ASR_MAX_BYTES) message.value = VOICE_TOO_BIG_MESSAGE
      settle?.(result)
      settle = null
    }

    recorder.start()
    recording.value = true
    elapsed.value = 0
    startedAt = Date.now()
    // 500ms 一跳：秒数看着不卡，且到点最多晚半秒停——比 1s 更不容易越过服务端硬上限。
    timer = setInterval(() => {
      elapsed.value = Math.floor((Date.now() - startedAt) / 1000)
      if (elapsed.value >= ASR_MAX_SECONDS && recording.value) {
        message.value = VOICE_TIMEOUT_MESSAGE
        void stop()
      }
    }, 500)
    return true
  }

  /** 停止并拿到音频；没在录 / 录废了都返回 `null`（提示已在 `message` 里）。 */
  function stop(): Promise<Blob | null> {
    if (!recorder || !recording.value) return Promise.resolve(null)
    recording.value = false
    clearTimer()
    return new Promise<Blob | null>((resolve) => {
      settle = resolve
      recorder?.stop()
    })
  }

  /** 放弃这一段（用户点了取消）：停轨道、清 chunk、不产出 blob。 */
  function cancel(): void {
    clearTimer()
    recording.value = false
    const active = recorder
    recorder = null
    chunks = []
    settle?.(null)
    settle = null
    if (active && active.state !== 'inactive') {
      active.onstop = null
      active.stop()
    }
    releaseStream()
  }

  onBeforeUnmount(cancel)

  return { supported, recording, elapsed, message, start, stop, cancel }
}
