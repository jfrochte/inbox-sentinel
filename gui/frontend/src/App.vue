<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useProfileStore } from './stores/profile'
import { useAppStore } from './stores/app'

const { t } = useI18n()
const profileStore = useProfileStore()
const appStore = useAppStore()

async function onProfileChange(e: Event) {
  const name = (e.target as HTMLSelectElement).value
  if (name) {
    await profileStore.loadProfile(name)
    // Sync language from profile
    appStore.setLanguage(profileStore.activeProfile.language)
  }
}

function switchLang(lang: string) {
  appStore.setLanguage(lang)
}

onMounted(async () => {
  await profileStore.fetchProfiles()
  // Auto-load first profile if available
  if (profileStore.profiles.length > 0 && profileStore.profiles[0]) {
    await profileStore.loadProfile(profileStore.profiles[0])
    appStore.setLanguage(profileStore.activeProfile.language)
  }
})
</script>

<template>
  <div class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="brand-logo-slot">
          <svg class="brand-logo" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect width="32" height="32" rx="6" fill="#2563eb"/>
            <path d="M6 12L16 7L26 12V20L16 25L6 20V12Z" stroke="white" stroke-width="1.5" fill="none"/>
            <path d="M6 12L16 17L26 12" stroke="white" stroke-width="1.5"/>
            <path d="M16 17V25" stroke="white" stroke-width="1.5"/>
          </svg>
        </div>
        <span>{{ t('app.title') }}</span>
      </div>

      <div class="profile-selector">
        <select :value="profileStore.activeProfileName" @change="onProfileChange">
          <option value="" disabled>{{ t('dashboard.selectProfile') }}</option>
          <option v-for="p in profileStore.profiles" :key="p" :value="p">{{ p }}</option>
        </select>
      </div>

      <nav>
        <RouterLink to="/">{{ t('nav.start') }}</RouterLink>
        <RouterLink to="/profiles">{{ t('nav.profiles') }}</RouterLink>
        <RouterLink to="/contacts">{{ t('nav.contacts') }}</RouterLink>
        <RouterLink to="/report">{{ t('nav.report') }}</RouterLink>
      </nav>

      <div class="sidebar-footer">
        <div class="lang-switch">
          <button :class="{ active: appStore.language === 'en' }" @click="switchLang('en')">EN</button>
          <button :class="{ active: appStore.language === 'de' }" @click="switchLang('de')">DE</button>
        </div>
      </div>
    </aside>

    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>
