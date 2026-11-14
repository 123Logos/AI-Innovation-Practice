/**
 * 播放端（B11b / `ADR-030` §2.3）的单测。
 *
 * 唯一有分歧的地方是**什么时候撤销那个 Blob URL**：`play()` 一 resolve 就撤销会把还没
 * 读完的音频掐断（媒体元素是流式读 URL 的），永远不撤销则是内存泄漏。
 * 这里的判据是「播完（或出错）之后一定撤销」，并顺带钉住「播放失败会抛出去」——
 * 视图层靠这个异常做静默降级。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AUDIO_UNSUPPORTED_MESSAGE, playAudioBlob } from '@/utils/audioPlayback'

const OBJECT_URL = 'blob:fake-audio'

class FakeAudio {
  static last: FakeAudio | null = null

  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  ended = false
  played = false

  constructor(readonly src: string) {
    FakeAudio.last = this
  }

  async play(): Promise<void> {
    if (!this.src) throw new Error('no source')
    this.played = true
  }

  /** 模拟「播完了」。 */
  finish(): void {
    this.ended = true
    this.onended?.()
  }
}

let revoke: ReturnType<typeof vi.fn>

beforeEach(() => {
  revoke = vi.fn()
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('URL', { ...URL, createObjectURL: () => OBJECT_URL, revokeObjectURL: revoke })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('播放一段音频', () => {
  it('播完之后撤销 Blob URL（不撤销就是内存泄漏）', async () => {
    const blob = new Blob(['x'.repeat(1000)])
    const playing = playAudioBlob(blob)

    await Promise.resolve()
    await Promise.resolve()
    expect(FakeAudio.last?.src).toBe(OBJECT_URL)
    expect(revoke).not.toHaveBeenCalled()

    FakeAudio.last?.finish()
    await playing

    expect(revoke).toHaveBeenCalledWith(OBJECT_URL)
  })

  it('播放出错也撤销，并把异常抛给调用方（它负责静默降级）', async () => {
    FakeAudio.prototype.play = async function (): Promise<void> {
      throw new Error('decode failed')
    }

    await expect(playAudioBlob(new Blob(['x']))).rejects.toThrow('decode failed')
    expect(revoke).toHaveBeenCalledWith(OBJECT_URL)
  })

  it('浏览器不能播放时直接抛错，不创建 URL（也就不需要撤销）', async () => {
    vi.stubGlobal('Audio', undefined)

    await expect(playAudioBlob(new Blob(['x']))).rejects.toThrow(AUDIO_UNSUPPORTED_MESSAGE)
    expect(revoke).not.toHaveBeenCalled()
  })
})
