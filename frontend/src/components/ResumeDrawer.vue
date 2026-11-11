<script setup lang="ts">
/**
 * 简历抽屉（批次 F3 第三片，清单第 5 条的移动端形态）。
 *
 * 外壳来自 `DrawerPanel`（Teleport / 遮罩 / 贴右满高），内容来自 `ResumeList`——
 * 与宽屏左栏里那份是**同一个组件**，所以排序、切换、改名、删除、回收站没有第二套实现。
 * 本文件只做一件事：把列表的两个意图（做完该收起 / 要新建）转给外框。
 *
 * 「新建简历」的弹窗不在这里开：它归 `AppLayout` 持有。理由是抽屉收起时本组件会被卸载，
 * 弹窗若长在列表里，用户点进输入框那一下会被基座判成「点外面」，抽屉连同弹窗一起消失。
 */
import DrawerPanel from '@/components/DrawerPanel.vue'
import ResumeList from '@/components/ResumeList.vue'

const emit = defineEmits<{ close: []; create: [] }>()
</script>

<template>
  <DrawerPanel panel-id="resume-drawer" label="简历列表" @close="emit('close')">
    <ResumeList @close="emit('close')" @create="emit('create')" />
  </DrawerPanel>
</template>
