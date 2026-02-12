import { defineStore } from 'pinia'
import { ref } from 'vue'
import i18n from '../i18n'
import type { HealthCheckResult } from '../api/types'

export const useAppStore = defineStore('app', () => {
  const language = ref('en')

  // Session-level state (persists across view navigation, not across page reload)
  const sessionPassword = ref('')
  const sessionFromDate = ref('')
  const sessionToDate = ref('')

  // Health check results (persist across navigation)
  const healthImap = ref<HealthCheckResult | null>(null)
  const healthSmtp = ref<HealthCheckResult | null>(null)
  const healthLlm = ref<HealthCheckResult | null>(null)

  function setLanguage(lang: string) {
    language.value = lang
    i18n.global.locale.value = lang as 'en' | 'de'
  }

  function initDateDefaults() {
    if (!sessionFromDate.value) {
      const yesterday = new Date()
      yesterday.setDate(yesterday.getDate() - 1)
      sessionFromDate.value = yesterday.toISOString().slice(0, 10)
    }
    if (!sessionToDate.value) {
      sessionToDate.value = new Date().toISOString().slice(0, 10)
    }
  }

  return {
    language, setLanguage,
    sessionPassword, sessionFromDate, sessionToDate, initDateDefaults,
    healthImap, healthSmtp, healthLlm,
  }
})
