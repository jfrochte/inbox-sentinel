<script setup lang="ts">
import { computed, ref, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useJobStore } from '../../stores/job'

const { t } = useI18n()
const jobStore = useJobStore()

const now = ref(Date.now() / 1000)
let clockTimer: ReturnType<typeof setInterval> | null = null

// Update the clock every second for elapsed time
clockTimer = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
onUnmounted(() => { if (clockTimer) clearInterval(clockTimer) })

const percent = computed(() => {
  const s = jobStore.jobStatus
  if (!s || !s.total) return 0
  return Math.round((s.current / s.total) * 100)
})

const phaseLabel = computed(() => {
  const s = jobStore.jobStatus
  if (!s) return ''
  const key = `progress.${s.phase || s.status}`
  const label = t(key)
  if (s.total > 0) {
    return `${label} (${s.current}/${s.total})`
  }
  return label
})

const elapsed = computed(() => {
  const s = jobStore.jobStatus
  if (!s || !s.started_at) return ''
  const secs = Math.max(0, Math.floor(now.value - s.started_at))
  const min = Math.floor(secs / 60)
  const sec = secs % 60
  return `${t('progress.elapsed')}: ${min}:${sec.toString().padStart(2, '0')}`
})

const emailInfo = computed(() => {
  const s = jobStore.jobStatus
  if (!s || !s.total_emails) return ''
  // Show email → thread info once we know total_emails
  const threads = s.total > 0 ? s.total : '...'
  return t('progress.emailCount', { emails: s.total_emails, threads })
})
</script>

<template>
  <div class="progress-bar-container" v-if="jobStore.jobStatus">
    <div class="progress-bar-track">
      <div class="progress-bar-fill" :style="{ width: percent + '%' }"></div>
    </div>
    <div class="progress-meta">
      <span class="progress-label">{{ phaseLabel }}</span>
      <span v-if="elapsed" class="progress-elapsed">{{ elapsed }}</span>
    </div>
    <div v-if="emailInfo" class="progress-email-info">{{ emailInfo }}</div>
    <div v-if="jobStore.jobStatus.status === 'failed'" class="text-danger mt-4">
      {{ jobStore.jobStatus.error }}
    </div>
    <div v-if="jobStore.jobStatus.status === 'completed'" class="badge badge-success mt-4">
      {{ t('progress.completed') }}
    </div>
  </div>
</template>
