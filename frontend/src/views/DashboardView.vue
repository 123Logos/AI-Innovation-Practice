<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const activeModule = ref('overview')

const modules = [
  { key: 'overview', label: '成长总览', icon: 'House' },
  { key: 'profile', label: '能力画像', icon: 'User' },
  { key: 'jobs', label: '岗位洞察', icon: 'Briefcase' },
  { key: 'resume', label: 'AI 简历', icon: 'Document' },
  { key: 'interview', label: '模拟面试', icon: 'ChatDotRound' },
]

function showComingSoon(label: string) {
  activeModule.value = label
  ElMessage.info(`${label}模块正在搭建中`)
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">职</div>
        <div>
          <strong>职途 AI</strong>
          <span>职业成长平台</span>
        </div>
      </div>
      <nav class="nav-list" aria-label="主导航">
        <button
          v-for="item in modules"
          :key="item.key"
          class="nav-item"
          :class="{ active: activeModule === item.key }"
          type="button"
          @click="showComingSoon(item.label)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="sidebar-footer">AI 建议仅供参考，请结合老师和面试官意见。</div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div>
          <p class="eyebrow">学生工作台</p>
          <h1>开启你的职业成长之旅</h1>
        </div>
        <el-button type="primary" plain @click="showComingSoon('完善档案')">
          完善我的档案
        </el-button>
      </header>

      <section class="hero-card">
        <div>
          <span class="tag">AI 成长助手</span>
          <h2>从目标岗位出发，积累可验证的能力证据</h2>
          <p>完成能力画像后，平台会帮你拆解岗位要求、发现差距并生成下一步行动。</p>
          <el-button type="primary" @click="showComingSoon('设置目标岗位')">
            设置目标岗位
          </el-button>
        </div>
        <div class="hero-illustration" aria-hidden="true">↗</div>
      </section>

      <div class="section-heading">
        <div>
          <p class="eyebrow">成长概览</p>
          <h2>今天，从一件小事开始</h2>
        </div>
        <span class="muted">数据模块接入后将自动更新</span>
      </div>

      <section class="stat-grid">
        <article class="stat-card">
          <span>档案完成度</span>
          <strong>0%</strong>
          <el-progress :percentage="0" :show-text="false" />
          <small>补充教育经历和技能</small>
        </article>
        <article class="stat-card">
          <span>已验证技能</span>
          <strong>0</strong>
          <small>通过项目、测评或老师确认</small>
        </article>
        <article class="stat-card">
          <span>目标岗位</span>
          <strong>未设置</strong>
          <small>选择一个方向开始探索</small>
        </article>
      </section>

      <section class="empty-card">
        <div class="empty-icon">✦</div>
        <div>
          <h3>你的成长路径还没有开始</h3>
          <p>先设置目标岗位，我们会为你准备技能差距和可执行的成长建议。</p>
        </div>
        <el-button type="primary" @click="showComingSoon('探索岗位')">探索岗位</el-button>
      </section>
    </main>
  </div>
</template>
