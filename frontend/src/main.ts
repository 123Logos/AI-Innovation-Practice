import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import { initTheme } from './stores/theme'
import './styles.css'

// 必须在 mount 之前：首屏就要是正确的配色，否则会先画默认色再跳变。
initTheme()

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
