/**
 * 语音作答那一条链路的视图级用例（B11b / `ADR-030`）。
 *
 * 与 `composables/__tests__/useVoiceRecorder.spec.ts` 分工：那边测「录音器本身」，
 * 这边测**用户在页面上能看到的四种结果**：
 *
 * 1. 转写成功 → 文本进输入框（**不自动发送**，用户还有机会改错字），发送时带 `input_mode: "voice"`；
 * 2. 识别失败 → 明确提示「请手动输入」，这一轮退回文字（不假装识别成功）；
 * 3. 合成失败 → 只少了声音，面试官的文字照常上屏，且**不弹红字**；
 * 4. 浏览器不支持录音 → 明说，不留一个点了没反应的按钮。
 *
 * 只 mock 两处外部能力（转写 / 播放），其余全走真实代码：真实的录音器（配一只假
 * `MediaRecorder`）、真实的 `interviewsApi`（配一只假 axios adapter）。这样
 * 「按钮 → 录音 → 转写 → 提交」这条链上任何一环断掉都会红。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { authHttp, http } from '@/api/client'
import { playAudioBlob } from '@/utils/audioPlayback'
import { synthesizeSpeech, transcribeAudio } from '@/api/voice'
import InterviewDetailView from '@/views/InterviewDetailView.vue'

vi.mock('@/api/voice', () => ({
  ASR_MAX_SECONDS: 120,
  ASR_MAX_BYTES: 8 * 1024 * 1024,
  TTS_VOICE_ID: 'stella',
  TTS_MIN_WAV_BYTES: 44,
  transcribeAudio: vi.fn(),
  synthesizeSpeech: vi.fn(),
}))

vi.mock('@/utils/audioPlayback', () => ({
  playAudioBlob: vi.fn(),
  isAudioPlaybackSupported: () => true,
  AUDIO_UNSUPPORTED_MESSAGE: '这个浏览器不能播放音频，这一轮只显示文字。',
}))

const SESSION_ID = '33333333-3333-3333-3333-333333333333'

const SESSION = {
  id: SESSION_ID,
  user_id: 'user-1',
  job_id: null,
  status: 'active',
  target_role: '前端开发',
  created_at: '2026-11-14T10:00:00+08:00',
  updated_at: '2026-11-14T10:05:00+08:00',
  ended_at: null,
}

const OPENING = {
  id: 'turn-1',
  session_id: SESSION_ID,
  sequence: 1,
  speaker: 'interviewer',
  content: '先做个自我介绍吧。',
  input_mode: 'text',
  ai_degrade_reason: null,
  created_at: '2026-11-14T10:00:00+08:00',
}

let sentBodies: Array<Record<string, unknown>> = []
let savedAdapters: Array<[typeof http, typeof http.defaults.adapter]> = []
let trackStop: ReturnType<typeof vi.fn>

class FakeMediaRecorder {
  state: 'inactive' | 'recording' = 'inactive'
  mimeType = 'audio/wav'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null

  start(): void {
    this.state = 'recording'
  }

  stop(): void {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['x'.repeat(2048)], { type: 'audio/wav' }) })
    this.onstop?.()
  }
}

function envelope(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse {
  return { data, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''

  if (method === 'get' && url === `/interviews/${SESSION_ID}`) {
    return Promise.resolve(
      envelope(config, {
        data: { session: SESSION, turns: [OPENING] },
        meta: { request_id: 'r' },
      }),
    )
  }
  if (method === 'post' && url === `/interviews/${SESSION_ID}/messages`) {
    const body = JSON.parse(String(config.data ?? '{}')) as Record<string, unknown>
    sentBodies.push(body)
    return Promise.resolve(
      envelope(config, {
        data: {
          candidate_turn: {
            ...OPENING,
            id: 'turn-2',
            sequence: 2,
            speaker: 'candidate',
            content: String(body.content ?? ''),
            input_mode: body.input_mode ?? 'text',
          },
          interviewer_turn: {
            ...OPENING,
            id: 'turn-3',
            sequence: 3,
            content: '说说你在那个项目里负责哪一块。',
          },
          ai_degrade_reason: null,
        },
        meta: { request_id: 'r' },
      }),
    )
  }
  return Promise.reject(new Error(`未桩住的请求：${method.toUpperCase()} ${url}`))
}

function installRecorderSupport(): void {
  trackStop = vi.fn()
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: trackStop }] }) },
  })
}

async function mountView() {
  const blank = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'interview-list', component: blank },
      { path: '/interviews/:id', name: 'interview-detail', component: blank },
    ],
  })
  await router.push(`/interviews/${SESSION_ID}`)
  await router.isReady()

  const wrapper = mount(InterviewDetailView, {
    global: { plugins: [createPinia(), router] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  sentBodies = []
  savedAdapters = [
    [http, http.defaults.adapter],
    [authHttp, authHttp.defaults.adapter],
  ]
  http.defaults.adapter = fakeAdapter
  authHttp.defaults.adapter = fakeAdapter
  vi.mocked(transcribeAudio).mockReset()
  vi.mocked(synthesizeSpeech).mockReset()
  vi.mocked(playAudioBlob).mockReset()
  vi.mocked(playAudioBlob).mockResolvedValue(undefined)
})

afterEach(() => {
  for (const [instance, adapter] of savedAdapters) instance.defaults.adapter = adapter
  savedAdapters = []
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'mediaDevices')
})

describe('语音作答', () => {
  it('转写成功：文本进输入框、提示可确认，发送时带 input_mode="voice"', async () => {
    installRecorderSupport()
    vi.mocked(transcribeAudio).mockResolvedValue({ text: '我负责前端页面的搭建。', durationMs: 3200 })
    vi.mocked(synthesizeSpeech).mockResolvedValue(new Blob(['x'.repeat(100)]))
    const wrapper = await mountView()

    await wrapper.get('.ghost-button').trigger('click')
    await flushPromises()
    // 录音中：显示秒数，并把「转成文字」换成主按钮。
    expect(wrapper.text()).toContain('正在录音')
    await wrapper.get('.primary-button').trigger('click')
    await flushPromises()

    expect(transcribeAudio).toHaveBeenCalledTimes(1)
    expect((wrapper.get('#answer').element as HTMLTextAreaElement).value).toBe('我负责前端页面的搭建。')
    expect(wrapper.text()).toContain('已转成文字')

    // 用户点发送：这一轮必须标成 voice。
    await wrapper.get('.answer-form').trigger('submit')
    await flushPromises()
    expect(sentBodies).toHaveLength(1)
    expect(sentBodies[0]).toMatchObject({ content: '我负责前端页面的搭建。', input_mode: 'voice' })
  })

  it('识别失败：明确提示「请手动输入」，这一轮退回文字', async () => {
    installRecorderSupport()
    vi.mocked(transcribeAudio).mockRejectedValue(new Error('upstream'))
    vi.mocked(synthesizeSpeech).mockResolvedValue(new Blob(['x'.repeat(100)]))
    const wrapper = await mountView()

    await wrapper.get('.ghost-button').trigger('click')
    await flushPromises()
    await wrapper.get('.primary-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('语音识别失败，请手动输入。')
    expect((wrapper.get('#answer').element as HTMLTextAreaElement).value).toBe('')

    // 手动补一句再发：不能还带着 voice 标记（那是假话）。
    await wrapper.get('#answer').setValue('我还是打字吧。')
    await wrapper.get('.answer-form').trigger('submit')
    await flushPromises()
    expect(sentBodies[0]).toMatchObject({ content: '我还是打字吧。', input_mode: 'text' })
  })

  it('合成失败：面试官的文字照常上屏，且不弹红字', async () => {
    installRecorderSupport()
    vi.mocked(transcribeAudio).mockResolvedValue({ text: '好的。', durationMs: 800 })
    vi.mocked(synthesizeSpeech).mockRejectedValue(new Error('upstream'))
    const wrapper = await mountView()

    await wrapper.get('.ghost-button').trigger('click')
    await flushPromises()
    await wrapper.get('.primary-button').trigger('click')
    await flushPromises()
    await wrapper.get('.answer-form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('说说你在那个项目里负责哪一块。')
    // 静默降级：只少了声音，不该出现错误提示。
    expect(wrapper.find('.feedback--error').exists()).toBe(false)
    expect(playAudioBlob).not.toHaveBeenCalled()
  })

  it('浏览器不支持录音：明说，不留一个点了没反应的按钮', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('这个浏览器不支持录音，请用文字作答。')
    expect(wrapper.find('.ghost-button').exists()).toBe(false)
  })

  it('语音作答的轮次带角标，打字的不带', async () => {
    installRecorderSupport()
    vi.mocked(transcribeAudio).mockResolvedValue({ text: '我负责前端。', durationMs: 900 })
    vi.mocked(synthesizeSpeech).mockResolvedValue(new Blob(['x'.repeat(100)]))
    const wrapper = await mountView()

    await wrapper.get('.ghost-button').trigger('click')
    await flushPromises()
    await wrapper.get('.primary-button').trigger('click')
    await flushPromises()
    await wrapper.get('.answer-form').trigger('submit')
    await flushPromises()

    const tags = wrapper.findAll('.bubble-tag')
    expect(tags).toHaveLength(1)
    expect(tags[0]?.text()).toBe('语音作答')
  })
})
