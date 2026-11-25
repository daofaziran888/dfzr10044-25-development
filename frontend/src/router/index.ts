import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import HandoverDetailView from '@/views/HandoverDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 来源交接详情比通用模块路径更具体，必须排在 `/:moduleKey` 之前
    {
      path: '/source_handover/:handoverId',
      name: 'source_handover_detail',
      component: HandoverDetailView,
      props: true,
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
