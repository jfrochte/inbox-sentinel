import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import ProfileView from './views/ProfileView.vue'
import ContactsView from './views/ContactsView.vue'
import ReportView from './views/ReportView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/profiles', name: 'profiles', component: ProfileView },
    { path: '/contacts', name: 'contacts', component: ContactsView },
    { path: '/report', name: 'report', component: ReportView },
  ],
})

export default router
