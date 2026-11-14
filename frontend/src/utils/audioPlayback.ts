/**
 * 播放一段音频 Blob（B11b / `ADR-030` §2.3 的播放端）。
 *
 * 只做两件事：把 Blob 变成可播放的 URL、播完（或失败）就把 URL 撤销。
 *
 * **撤销时机**是这里唯一有分歧的地方：`play()` 一 resolve 就 `revokeObjectURL` 在部分
 * 浏览器上会把还没读完的音频掐断（媒体元素是流式读 URL 的）。所以撤销挂在
 * `ended` / `error` 上——即「这一段的生命周期结束」，那也是 `ADR-030` §2.1 里
 * 「音频用完即删」在前端这一侧的含义。
 */

export const AUDIO_UNSUPPORTED_MESSAGE = '这个浏览器不能播放音频，这一轮只显示文字。'

export function isAudioPlaybackSupported(): boolean {
  return typeof Audio !== 'undefined' && typeof URL.createObjectURL === 'function'
}

/**
 * 播放并等待结束；失败（含浏览器不支持播放）抛异常，由调用方静默降级。
 *
 * 这里的失败**不上屏**：`ADR-030` §2.1 不变量 D 说的是「TTS 失败 → 只是少了声音，
 * 文字回复照常」。为一句「没声」再弹一个红字，反而把用户的注意力从内容上拽走。
 */
export async function playAudioBlob(blob: Blob): Promise<void> {
  if (!isAudioPlaybackSupported()) throw new Error(AUDIO_UNSUPPORTED_MESSAGE)

  const url = URL.createObjectURL(blob)
  const audio = new Audio(url)

  try {
    await audio.play()
    await new Promise<void>((resolve) => {
      const finish = (): void => {
        audio.onended = null
        audio.onerror = null
        resolve()
      }
      audio.onended = finish
      audio.onerror = finish
      // 已经播完的极短音频不会再触发 ended，补一道保险。
      if (audio.ended) finish()
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}
