import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import EnvironmentPage from './pages/EnvironmentPage.vue'
import './styles.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/environment' },
    { path: '/environment', component: EnvironmentPage },
    { path: '/:pathMatch(.*)*', redirect: '/environment' },
  ],
})

createApp(App).use(createPinia()).use(router).mount('#app')
