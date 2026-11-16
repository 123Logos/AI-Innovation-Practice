<script setup lang="ts">
/**
 * 教师端学情看板（FR-08 / B10a，`ADR-029` §2.2）+ 下载清单（B10b）。
 *
 * 刻意做成**只读 + 两条下载动作**：这一屏不写库，聚合数字全来自后端；
 * 「查看数据 / 下载 / 导出」三个入口在后端是各自独立过 `require_organization`
 * 的（第 19 轮硬约束），前端不重复判一次「有没有组织」——真边界在服务端。
 *
 * 失败一律可见（守则 C1/C3）：读取失败、下载失败都落到 `role="alert"` 红字。
 * 下载的错误体是 blob，拦截器给的是通用 `ApiError`，这里只展示固定人话，
 * 不把响应体里的技术字串上屏。
 */
import { computed, onMounted, ref, watch } from 'vue'

import { teacherToolsApi } from '@/api'
import { isApiError } from '@/api/client'
import type {
  DownloadableResume,
  JobCoverageItem,
  TeacherDashboard,
} from '@/api/teacherTools'
import { BUCKET_LABELS, TAG_STATUS_LABELS } from '@/api/teacherTools'
import type { TaskDownloadResult } from '@/api/types'
import { TaskFailedError, usePollTask } from '@/composables/usePollTask'

const dashboard = ref<TeacherDashboard | null>(null)
const resumes = ref<DownloadableResume[]>([])
const selected = ref<string[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')

/**
 * 学生筛选（2026-11-15 第 25 轮，老板指令：下拉、不要输入框）。
 *
 * 选项**从已经拿到的那份清单里推导**（按名字去重 + 中文排序），不另发一次请求：
 * 清单本身就是「本组织可导出的人」，再造一个「学生列表」接口等于两份名单各说一套
 * （后端也会因此多一个入口要单独过组织闸）。
 */
const studentFilter = ref('')
const studentOptions = computed(() =>
  [...new Set(resumes.value.map((item) => item.full_name))].sort((a, b) =>
    a.localeCompare(b, 'zh-CN'),
  ),
)
/** 选「全部学生」时是整份清单，选了谁就只剩谁。 */
const visibleResumes = computed(() =>
  studentFilter.value
    ? resumes.value.filter((item) => item.full_name === studentFilter.value)
    : resumes.value,
)

/**
 * 换筛选就清空勾选：勾选是**为了这次打包**，而打包按钮只报一个「已选 N 份」，
 * 留着屏幕外看不见的勾选，用户按下按钮时根本数不清会打包谁。
 */
watch(studentFilter, () => {
  selected.value = []
})

const studentTotal = computed(() => dashboard.value?.student_total ?? 0)
const taggedTotal = computed(() => dashboard.value?.tagged_student_total ?? 0)
const selectedCount = computed(() => selected.value.length)

/**
 * 错误上屏：优先用**服务端那句人话**（`ADR-033` §2.6 的 `error.message` / `ApiError.message`）。
 *
 * 第 30 轮之前这里恒返回 `fallback`，因为下载是同步的、失败只有一句固定话。
 * 现在失败可能来自任务终态（`TaskFailedError`，例如「这批简历没有可下载的版本」），
 * 那句话本身就是能动的下一步——丢掉它，用户只能看到「打包失败」。
 */
function describeError(fallback: string, error?: unknown): string {
  if (error instanceof TaskFailedError || isApiError(error)) return error.message
  return fallback
}

/* ---------------------------- 打包任务（ADR-033） ---------------------------- */

/**
 * 打包是一个**后台任务**：点一下只拿到 `task_id`，进度与结果都要轮询。
 *
 * 三件事都不在这里发明：状态机（pending/running/complete/failed）、进度百分比、
 * 失败码——全部来自 `GET /tasks/{id}`。视图只负责画和下载。
 */
const pack = usePollTask()

/** 打包进度那句话：没在跑就不显示。 */
const packStageText = computed(() => {
  if (!pack.polling.value) return ''
  switch (pack.task.value?.stage) {
    case 'queued':
      return '已排队，马上开始打包…'
    case 'rendering':
      return '正在逐份生成 PDF…'
    case 'packaging':
      return '正在打包压缩文件…'
    default:
      return '正在准备打包…'
  }
})
const packProgress = computed(() => pack.task.value?.progress ?? 5)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [data, items] = await Promise.all([
      teacherToolsApi.getDashboard(),
      teacherToolsApi.listResumes(),
    ])
    dashboard.value = data
    resumes.value = items
    selected.value = []
    studentFilter.value = ''
  } catch (failure) {
    error.value = describeError('读取看板数据失败，请刷新页面重试。', failure)
  } finally {
    loading.value = false
  }
}

async function downloadSelected(): Promise<void> {
  if (!selected.value.length || busy.value) return
  busy.value = true
  error.value = ''
  try {
    // ① 入队（202）→ ② 轮询到终态 → ③ 一次性取产物。
    // 三步分开而不是合成一个「等等看」：第 ② 步失败时错误码要能原样给到用户
    // （例如「这批简历没有可下载的版本」），第 ③ 步失败才说明是网络/文件的问题。
    const queued = await teacherToolsApi.downloadBatch(selected.value)
    const status = await pack.start(queued.task_id)
    const result = status.result as TaskDownloadResult | null
    const blob = await teacherToolsApi.fetchTaskArchive(queued.task_id)
    teacherToolsApi.saveBlob(blob, result?.filename ?? '简历批量导出.zip')
  } catch (failure) {
    error.value = describeError('批量下载没能完成，请稍后再试。', failure)
  } finally {
    busy.value = false
  }
}

async function exportOne(resumeId: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const blob = await teacherToolsApi.exportResume(resumeId)
    teacherToolsApi.saveBlob(blob, '简历.pdf')
  } catch (failure) {
    error.value = describeError('这份简历没能导出，请稍后再试。', failure)
  } finally {
    busy.value = false
  }
}

function bucketRows(item: JobCoverageItem): Array<{ label: string; count: number }> {
  return (Object.keys(BUCKET_LABELS) as Array<keyof typeof BUCKET_LABELS>).map((key) => ({
    label: BUCKET_LABELS[key],
    count: item.buckets[key],
  }))
}

onMounted(load)
</script>

<template>
  <main class="dashboard-page">
    <div class="page-heading">
      <p class="eyebrow">教师端</p>
      <h1>学情看板</h1>
      <p class="intro">
        看的是整个班的技能分布与岗位要求覆盖情况，不是某一位同学的分数。
        标签来自标准化测评，覆盖率只数纸面上写没写这个技能。
      </p>
    </div>

    <p v-if="error" class="feedback feedback--error" role="alert">{{ error }}</p>
    <p v-if="loading" class="feedback">读取中…</p>

    <template v-if="dashboard">
      <section class="panel" aria-label="概况">
        <h2 class="panel-title">概况</h2>
        <p class="summary">
          本组织共 <strong>{{ studentTotal }}</strong> 名学生，其中
          <strong>{{ taggedTotal }}</strong> 名有测评标签。
        </p>
      </section>

      <section class="panel" aria-label="标签分布">
        <h2 class="panel-title">标签分布</h2>
        <p v-if="!dashboard.tag_distribution.length" class="feedback">
          还没有测评标签，等学生完成一次测评后这里会有数据。
        </p>
        <ul v-else class="tag-list">
          <li v-for="item in dashboard.tag_distribution" :key="`${item.tag}-${item.status}`" class="tag-item">
            <span class="tag-name">{{ item.tag }}</span>
            <span class="tag-status" :class="`tag-status--${item.status}`">
              {{ TAG_STATUS_LABELS[item.status] }}
            </span>
            <span class="tag-count">{{ item.student_count }} 人</span>
          </li>
        </ul>
      </section>

      <section class="panel" aria-label="岗位技能覆盖率">
        <h2 class="panel-title">岗位技能覆盖率</h2>
        <p v-if="!dashboard.match_overview.length" class="feedback">
          本组织还没有已发布的岗位。
        </p>
        <ul v-else class="job-list">
          <li v-for="item in dashboard.match_overview" :key="item.job_id" class="job-item">
            <div class="job-head">
              <p class="job-title">{{ item.title }}</p>
              <p class="job-average">平均覆盖 {{ item.avg_coverage }}%</p>
            </div>
            <p v-if="item.no_requirements" class="feedback">这条岗位还没有填写技能要求。</p>
            <p v-else class="job-meta">{{ item.required_skill_count }} 项要求 · {{ item.student_count }} 名学生</p>
            <ul class="bucket-list">
              <li v-for="row in bucketRows(item)" :key="row.label">
                {{ row.label }} <strong>{{ row.count }}</strong> 人
              </li>
            </ul>
          </li>
        </ul>
      </section>

      <section class="panel" aria-label="简历导出">
        <h2 class="panel-title">简历导出</h2>
        <p class="feedback">
          勾选后打包下载；单份导出会生成 PDF，分页与预览里的纸面一致。
        </p>
        <p v-if="!resumes.length" class="feedback">本组织暂时没有可导出的简历版本。</p>
        <template v-else>
          <div class="filter-row">
            <label class="filter-label" for="resume-student-filter">学生</label>
            <select id="resume-student-filter" v-model="studentFilter" class="filter-select">
              <option value="">全部学生</option>
              <option v-for="name in studentOptions" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>
          <p v-if="!visibleResumes.length" class="feedback">这个学生暂时没有可导出的简历。</p>
          <ul v-else class="resume-list">
            <li v-for="item in visibleResumes" :key="item.resume_id" class="resume-item">
              <label class="resume-check">
                <input v-model="selected" type="checkbox" :value="item.resume_id" />
                <span class="resume-name">{{ item.full_name }}</span>
                <span class="resume-title">{{ item.title }} · v{{ item.version_no }}</span>
              </label>
              <button class="ghost-button" type="button" :disabled="busy" @click="exportOne(item.resume_id)">
                导出 PDF
              </button>
            </li>
          </ul>
        </template>
        <button
          v-if="resumes.length"
          class="primary-button"
          type="button"
          :disabled="busy || !selectedCount"
          @click="downloadSelected"
        >
          {{ busy ? '正在准备…' : `打包下载（已选 ${selectedCount} 份）` }}
        </button>
        <!-- 进度：只在打包时出现；`aria-live` 让屏幕阅读器跟着念，不用用户去找。 -->
        <p v-if="busy && packStageText" class="feedback" role="status" aria-live="polite" data-testid="pack-progress">
          {{ packStageText }}（{{ packProgress }}%）
        </p>
      </section>
    </template>
  </main>
</template>

<style scoped>
.dashboard-page { width: min(900px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 62ch; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }

.panel { display: grid; gap: 12px; margin-bottom: 16px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 16px 18px; background: var(--ui-surface); }
.panel-title { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.summary { margin: 0; color: var(--ui-ink); font-size: var(--text-base); line-height: 1.6; }
.feedback { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }

.tag-list, .job-list, .resume-list, .bucket-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
/*
 * 导出清单封顶（老板 2026-11-15 第 25 轮：列表高 400px、超出滚动）：
 * 一个班几十个人时这一栏会把「打包下载」按钮与页面底部一起推下去，
 * 用户勾到一半就看不到自己在勾什么。`padding-right` 是给滚动条留位，
 * 不然最后一行右侧按钮会被滚动条压住半个。
 */
.resume-list { max-height: 400px; overflow-y: auto; padding-right: 6px; }

/* 学生筛选：标签 + 下拉同一行，与上方表单控件同一档高度（34px）。 */
.filter-row { display: flex; align-items: center; gap: 10px; }
.filter-label { color: var(--ui-ink-muted); font-size: var(--text-sm); white-space: nowrap; }
.filter-select { flex: 0 1 220px; min-width: 0; height: 34px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-base); }
.filter-select:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.tag-item, .job-item, .resume-item { display: flex; align-items: center; gap: 10px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 8px 12px; background: var(--ui-surface-soft); }
.tag-name, .job-title, .resume-name { color: var(--ui-ink); font-size: var(--text-md); }
.tag-status { border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 1px 8px; font-size: var(--text-xs); }
.tag-status--verified { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); }
.tag-status--pending { color: var(--ui-ink-muted); }
.tag-status--improving { border-color: var(--ui-warning); color: var(--ui-warning); }
.tag-count, .resume-title, .job-meta { margin-left: auto; color: var(--ui-ink-faint); font-size: var(--text-xs); }
.job-item { display: grid; }
.job-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.job-title { margin: 0; font-weight: 650; }
.job-average { margin: 0; color: var(--theme-primary-strong); font-size: var(--text-sm); }
.job-meta { margin: 0; }
.bucket-list { display: flex; flex-wrap: wrap; gap: 12px; font-size: var(--text-xs); color: var(--ui-ink-muted); }
.resume-check { display: flex; align-items: center; gap: 8px; flex: 1 1 auto; min-width: 0; cursor: pointer; }
.resume-title { white-space: nowrap; }

.primary-button { justify-self: start; height: 34px; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 0 16px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-sm); }
.primary-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.primary-button:disabled { opacity: .6; cursor: progress; }
.ghost-button { height: 30px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 12px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-xs); }
.ghost-button:disabled { opacity: .6; cursor: progress; }

@media (max-width: 760px) {
  .dashboard-page { padding: 20px 16px 48px; }
  .resume-item { flex-wrap: wrap; }
}
@media print {
  .dashboard-page { display: none; }
}
</style>
