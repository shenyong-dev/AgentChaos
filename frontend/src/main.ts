import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import EnvironmentPage from './pages/EnvironmentPage.vue'
import EvidencePage from './pages/EvidencePage.vue'
import CasesPage from './pages/CasesPage.vue'
import JudgingPage from './pages/JudgingPage.vue'
import SemanticPage from './pages/SemanticPage.vue'
import WorkspacePage from './pages/WorkspacePage.vue'
import ReportsPage from './pages/ReportsPage.vue'
// Include Dialog's Overlay dependency so its fixed positioning and stacking apply on every route.
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/select/style/css'
import './styles.css'
import './evidence.css'
import './workbench.css'
import './workspace.css'
import './navigation.css'
import './reports.css'
import './design-system.css'
import { pageTitle } from './navigation'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/workspace' },
    { path: '/workspace', component: WorkspacePage },
    { path: '/targets', component: WorkspacePage },
    { path: '/library', component: WorkspacePage },
    { path: '/workbench', component: WorkspacePage },
    { path: '/reports', component: ReportsPage },
    { path: '/environment', component: EnvironmentPage },
    { path: '/runs/:runId?', component: EvidencePage },
    { path: '/cases/:batchId?', component: CasesPage },
    { path: '/judging/:batchId?', component: JudgingPage },
    { path: '/semantic/:executionId?', component: SemanticPage },
    { path: '/:pathMatch(.*)*', redirect: '/environment' },
  ],
})
router.afterEach(to => { document.title = `${pageTitle(to.path)} · AgentChaos` })

createApp(App).use(createPinia()).use(router).mount('#app')
