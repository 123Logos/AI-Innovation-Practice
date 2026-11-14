/**
 * 语音两条（B11b / FR-07，`ADR-030` §2.4）。
 *
 * 这两次调用**由浏览器直连 DeepSeek**，带用户自己的 Key（BYOK），不经过我们的后端：
 * 音频因此一次都不上传（`ADR-030` §2.1 不变量 A）——「转写完毕即删、不入库、不存盘」
 * 是网络路径上的事实，不是纪律要求。
 *
 * 出网前的两道闸都在 `client.ts` 的 `deepseekHttp` 里：路径白名单（只有这两条）与
 * Key 存在性。本文件只负责**契约**：请求形状、响应形状、以及「什么算失败」。
 */

import { deepseekHttp } from './client'

/** ASR 的硬上限（`ADR-030` §2.2）：超过就没有必要再发，发了也一定失败。 */
export const ASR_MAX_SECONDS = 120

/** 单段音频体积上限 8 MiB，同样是服务端硬约束。 */
export const ASR_MAX_BYTES = 8 * 1024 * 1024

/** 固定的合成音色：这一批不做音色选择（`ADR-030` §2.3）。 */
export const TTS_VOICE_ID = 'stella'

/** 一个合法 WAV 至少 44 字节（RIFF 头）；比这还短一定是半截响应。 */
export const TTS_MIN_WAV_BYTES = 44

export interface AsrResult {
  /** 转写文本（已去首尾空白）。空串表示「没听到人说话」，由调用方当失败处理。 */
  text: string
  /** 服务端报的音频时长（毫秒）；拿不到时是 0。 */
  durationMs: number
}

/**
 * 把一段录音转成文本。
 *
 * 失败一律抛异常，由调用方降级成手动输入——**绝不返回空串或占位文本冒充识别成功**
 * （那会让用户以为「AI 听懂了一句他其实没说的话」，`ADR-030` §2.1 不变量 D）。
 */
export async function transcribeAudio(audio: Blob, filename = 'answer.wav'): Promise<AsrResult> {
  const form = new FormData()
  form.append('file', audio, filename)
  // 不要手写 `Content-Type`：multipart 的 boundary 由浏览器生成，写死会少一段 boundary。
  const response = await deepseekHttp.post<{ text?: string; duration_ms?: number }>(
    '/v1/tools/asr',
    form,
  )
  const payload = response.data ?? {}
  return {
    text: (payload.text ?? '').trim(),
    durationMs: Number(payload.duration_ms ?? 0),
  }
}

/**
 * 把面试官那一段合成成 WAV。
 *
 * 半截响应按失败处理：`ADR-030` §2.3 记着「已经开始返回后合成失败，响应会中途断开」，
 * 这种响应体通常是 0 字节或连 RIFF 头都不完整——播出来只有一声爆音。
 */
export async function synthesizeSpeech(text: string): Promise<Blob> {
  const response = await deepseekHttp.post(
    '/v1/tools/tts',
    {
      text,
      voice_id: TTS_VOICE_ID,
      format: 'wav',
      // 面试官文案里偶尔有 `**强调**`，念出来会把星号读成「星号星号」。
      filter_markdown: true,
    },
    { responseType: 'blob' },
  )
  const blob = response.data as Blob
  if (!blob || blob.size < TTS_MIN_WAV_BYTES) {
    throw new Error('合成结果不完整，这一轮只显示文字。')
  }
  return blob
}
