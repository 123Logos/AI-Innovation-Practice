<script setup lang="ts">
/**
 * 版本历史与两版对比（批次 B8 剩余，`ADR-026` §2.4 / §2.6）。
 *
 * 这个弹窗只做两件事：**看历史**、**比两版**。按岗位定制不在这里——
 * 它是「看着某条岗位时」的动件，入口在 `JobDetailView`（`ADR-026` §2.5）。
 *
 * 三条口径写死在模板里：
 *
 * 1. **链头单独标出来**（「当前简历」）。用户点「生成」之后拿到的是链头，
 *    定制版只进历史、不会变成链头——不标出来，他会以为最新那行就是当前简历。
 * 2. `comparable === false` 时显示 `note` 且**不渲染空差异**：把「比不了」
 *    画成「两版一样」是本模块最坏的失败形态。
 * 3. `order_changed` 的区块要在标题上说一句「顺序有调整」：
 *    按岗位定制的差异**只**出现在这里，不说出来，那个功能看起来就像什么都没做。
 */
import { computed, ref, watch } from 'vue'

import type { ResumeDiffSection, ResumeVersionSummary } from '@/api/types'
import { useResumeStore } from '@/stores/resume'
import { formatDateTime } from '@/utils/datetime'

const props = defineProps<{ modelValue: boolean }>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
}>()

const store = useResumeStore()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const fromId = ref('')
const toId = ref('')
const loadError = ref('')
const compareError = ref('')

const hasEnoughVersions = computed(() => store.versions.length >= 2)

/**
 * 打开时拉一次历史，并把默认对比设成「上一版 → 最新一版」。
 *
 * 默认值不是随便挑的：用户点开历史的动机九成是「我刚才那一版改了什么」，
 * 而「最新」与「上一版」正好回答这个问题。只有一版时两者都指向它，
 * 对比按钮因此被禁用（拿一版和自己比没有意义）。
 */
watch(
  visible,
  async (open) => {
    if (!open) return
    loadError.value = ''
    compareError.value = ''
    try {
      await store.loadVersions()
    } catch (error) {
      loadError.value = error instanceof Error && error.message ? error.message : '版本历史读取失败，请稍后重试。'
      return
    }
    const [latest, previous] = store.versions
    toId.value = latest?.resume_id ?? ''
    fromId.value = previous?.resume_id ?? latest?.resume_id ?? ''
  },
  { immediate: true },
)

async function compare(): Promise<void> {
  compareError.value = ''
  if (!fromId.value || !toId.value) return
  try {
    await store.compareVersions(fromId.value, toId.value)
  } catch (error) {
    compareError.value = error instanceof Error && error.message ? error.message : '对比失败，请稍后重试。'
  }
}

function versionLabel(version: ResumeVersionSummary): string {
  const head = version.is_head ? '（当前简历）' : ''
  return `v${version.version_no} · ${version.title}${head}`
}

function originLabel(version: ResumeVersionSummary): string {
  return version.origin === 'customized' ? '按岗位定制' : '模板生成'
}

const CHANGE_LABELS: Record<ResumeDiffSection['change'], string> = {
  added: '新增了整块',
  removed: '整块被删除',
  changed: '有变化',
  unchanged: '没有变化',
}

function itemLabel(kind: 'added' | 'removed' | 'changed'): string {
  if (kind === 'added') return '新增'
  if (kind === 'removed') return '删除'
  return '修改'
}
</script>

<template>
  <el-dialog v-model="visible" title="版本历史" class="ui-dialog versions-dialog">
    <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>

    <p v-else-if="!store.versionsLoading && store.versions.length === 0" class="empty">
      还没有生成过简历。点「生成简历」之后，每一版都会出现在这里。
    </p>

    <ul v-else class="version-list">
      <li v-for="version in store.versions" :key="version.resume_id" class="version-row">
        <div class="version-row__main">
          <span class="version-row__no">v{{ version.version_no }}</span>
          <span class="version-row__title">{{ version.title }}</span>
          <span v-if="version.is_head" class="badge badge--head">当前简历</span>
          <span class="badge" :class="version.origin === 'customized' ? 'badge--custom' : 'badge--generated'">
            {{ originLabel(version) }}
          </span>
        </div>
        <span class="version-row__time">{{ formatDateTime(version.created_at) }}</span>
      </li>
    </ul>

    <template v-if="hasEnoughVersions">
      <div class="compare-controls">
        <label class="compare-field">
          <span>对比起点</span>
          <el-select v-model="fromId" class="compare-select" placeholder="选择版本">
            <el-option
              v-for="version in store.versions"
              :key="version.resume_id"
              :value="version.resume_id"
              :label="versionLabel(version)"
            />
          </el-select>
        </label>
        <label class="compare-field">
          <span>对比终点</span>
          <el-select v-model="toId" class="compare-select" placeholder="选择版本">
            <el-option
              v-for="version in store.versions"
              :key="version.resume_id"
              :value="version.resume_id"
              :label="versionLabel(version)"
            />
          </el-select>
        </label>
        <el-button
          type="primary"
          class="compare-button"
          :loading="store.diffing"
          :disabled="!fromId || !toId || fromId === toId"
          @click="compare"
        >
          对比这两版
        </el-button>
      </div>
      <p v-if="fromId && toId && fromId === toId" class="hint">起点与终点是同一版，换一个再比。</p>
      <p v-if="compareError" class="feedback feedback--error" role="alert">{{ compareError }}</p>

      <section v-if="store.versionDiff" class="diff" data-testid="version-diff">
        <p v-if="!store.versionDiff.comparable" class="diff-note" role="status">
          {{ store.versionDiff.note }}
        </p>
        <template v-else>
          <p class="diff-summary">
            <span>新增 {{ store.versionDiff.summary.added }}</span>
            <span>删除 {{ store.versionDiff.summary.removed }}</span>
            <span>修改 {{ store.versionDiff.summary.changed }}</span>
            <span>涉及 {{ store.versionDiff.summary.sections_changed }} 块</span>
            <span v-if="store.versionDiff.summary.item_order_changed">条目顺序有调整</span>
          </p>
          <p
            v-if="store.versionDiff.summary.added === 0
              && store.versionDiff.summary.removed === 0
              && store.versionDiff.summary.changed === 0
              && store.versionDiff.summary.sections_changed === 0"
            class="diff-same"
          >
            这两版的内容完全一样。
          </p>

          <article
            v-for="section in store.versionDiff.sections"
            :key="section.key"
            class="diff-section"
          >
            <header class="diff-section__head">
              <h3>{{ section.label }}</h3>
              <span class="diff-section__change">{{ CHANGE_LABELS[section.change] }}</span>
              <span v-if="section.title_before !== section.title_after" class="diff-section__rename">
                标题改名：原「{{ section.title_before || '（无）' }}」，现「{{ section.title_after || '（无）' }}」
              </span>
              <span v-if="section.order_changed" class="diff-section__order">条目顺序有调整</span>
            </header>

            <ul v-if="section.items.length" class="diff-items">
              <li v-for="(item, index) in section.items" :key="`${section.key}-${index}`" class="diff-item">
                <span class="badge" :class="`badge--${item.kind}`">{{ itemLabel(item.kind) }}</span>
                <span class="diff-item__label">{{ item.label }}</span>
                <ul v-if="item.fields.length" class="diff-fields">
                  <li v-for="field in item.fields" :key="field.field" class="diff-field">
                    <span class="diff-field__name">{{ field.field }}</span>
                    <span class="diff-field__before">原：{{ field.before || '（无）' }}</span>
                    <span class="diff-field__after">现：{{ field.after || '（无）' }}</span>
                  </li>
                </ul>
              </li>
            </ul>
          </article>
        </template>
      </section>
    </template>

    <p v-else-if="!store.versionsLoading && store.versions.length === 1" class="hint">
      目前只有一版，再生成一次或按岗位定制一次之后就可以对比了。
    </p>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.feedback { margin: 0 0 12px; font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.empty { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }
.hint { margin: 8px 0 0; color: var(--ui-ink-muted); font-size: var(--text-sm); line-height: 1.5; }

.version-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.version-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 10px 12px;
}
.version-row__main { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.version-row__no { color: var(--ui-ink-muted); font-size: var(--text-sm); font-variant-numeric: tabular-nums; }
.version-row__title { color: var(--ui-ink); font-size: var(--text-md); font-weight: 600; }
.version-row__time { color: var(--ui-ink-faint); font-size: var(--text-xs); }

.badge {
  border-radius: var(--ui-radius);
  padding: 1px 6px;
  font-size: var(--text-xs);
  line-height: 1.6;
}
.badge--head { color: var(--theme-on-primary); background: var(--theme-primary); }
.badge--generated { color: var(--ui-ink-muted); background: var(--ui-surface-soft); }
.badge--custom { color: var(--theme-primary-deep); background: var(--theme-bg-light); }
.badge--added { color: var(--theme-primary-deep); background: var(--theme-bg-light); }
.badge--removed { color: var(--ui-danger); background: var(--ui-surface-soft); }
.badge--changed { color: var(--ui-warning); background: var(--ui-warning-soft); }

.compare-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: end;
  gap: 8px 12px;
  margin-top: 18px;
}
.compare-field { display: grid; gap: 4px; min-width: 0; }
.compare-field span { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.compare-select { width: 100%; }

.diff { margin-top: 16px; }
.diff-note {
  margin: 0;
  border: 1px solid var(--ui-warning-border);
  border-left: 4px solid var(--ui-warning-border);
  border-radius: var(--ui-radius);
  padding: 10px 12px;
  color: var(--ui-warning);
  background: var(--ui-warning-soft);
  font-size: var(--text-base);
  line-height: 1.5;
}
.diff-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 0 0 10px;
  color: var(--ui-ink-muted);
  font-size: var(--text-sm);
}
.diff-same { margin: 0 0 10px; color: var(--ui-ink); font-size: var(--text-base); }
.diff-section { border-top: 1px solid var(--ui-border); padding: 10px 0; }
.diff-section:first-of-type { border-top: 0; }
.diff-section__head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
.diff-section__head h3 { margin: 0; font-size: var(--text-md); font-weight: 600; }
.diff-section__change { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.diff-section__rename { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.diff-section__order { color: var(--theme-primary-deep); font-size: var(--text-sm); }

.diff-items { display: grid; gap: 6px; margin: 8px 0 0; padding: 0; list-style: none; }
.diff-item { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; }
.diff-item__label { color: var(--ui-ink); font-size: var(--text-base); }
.diff-fields { display: grid; flex: 1 1 100%; gap: 2px; margin: 0; padding: 0 0 0 8px; list-style: none; }
.diff-field {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 8px;
  color: var(--ui-ink-muted);
  font-size: var(--text-sm);
  line-height: 1.5;
}
.diff-field__name { color: var(--ui-ink); }
.diff-field__before { text-decoration: line-through; }
.diff-field__after { color: var(--theme-primary-deep); }
</style>
