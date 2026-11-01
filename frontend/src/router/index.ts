import { createRouter, createWebHistory } from 'vue-router'

import ResumeEditorView from '@/views/ResumeEditorView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'resume-editor',
      component: ResumeEditorView,
    },
  ],
})
