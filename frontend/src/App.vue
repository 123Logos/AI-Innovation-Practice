<script setup lang="ts">
import { RouterView } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
</script>

<template>
  <RouterView />
  <!--
    全站唯一的确认/提示弹窗挂在这里（批次 F1b）。之所以是这一处而不是六个视图各摆一个：
    六处调用点里有一处在 `stores/resume.ts` 的动作内部，那里根本没有模板可渲染。
    批次 F2 做共享 layout 时，这一格会跟着搬进 layout，形态与判据不变。
  -->
  <ConfirmDialog />
</template>
