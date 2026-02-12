<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProfileStore } from '../stores/profile'
import { useJobStore } from '../stores/job'
import HealthPanel from '../components/health/HealthPanel.vue'
import RunPanel from '../components/jobs/RunPanel.vue'
import ProgressBar from '../components/jobs/ProgressBar.vue'

const { t } = useI18n()
const profileStore = useProfileStore()
const jobStore = useJobStore()

const stats = computed(() => {
  const s = jobStore.jobStatus
  if (!s || s.status !== 'completed' || !s.stats) return null
  return s.stats
})
</script>

<template>
  <h1 style="font-size: 20px; margin-bottom: 20px;">{{ t('dashboard.title') }}</h1>

  <div v-if="!profileStore.activeProfileName" class="card text-center text-muted">
    {{ t('dashboard.noProfile') }}
  </div>

  <template v-else>
    <RunPanel />
    <ProgressBar />

    <!-- Run Summary Stats -->
    <div v-if="stats" class="card run-summary">
      <h3>{{ t('stats.title') }}</h3>

      <div class="stats-grid">
        <div class="stat-item">
          <div class="stat-value">{{ stats.total_emails }}</div>
          <div class="stat-label">{{ t('stats.emails') }}</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">{{ stats.thread_count }}</div>
          <div class="stat-label">{{ t('stats.threads') }}</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">{{ stats.unique_senders }}</div>
          <div class="stat-label">{{ t('stats.senders') }}</div>
        </div>
        <div class="stat-item" v-if="stats.draft_stats.generated">
          <div class="stat-value">{{ stats.draft_stats.generated }}</div>
          <div class="stat-label">{{ t('stats.drafts') }}</div>
        </div>
        <div class="stat-item" v-if="stats.triage_stats.processed">
          <div class="stat-value">{{ stats.triage_stats.processed }}</div>
          <div class="stat-label">{{ t('stats.triaged') }}</div>
        </div>
      </div>

      <!-- Category alerts -->
      <div class="stats-alerts" v-if="stats.categories.PHISHING || stats.categories.SPAM || stats.categories.FYI">
        <div v-if="stats.categories.PHISHING" class="stat-alert stat-alert-danger">
          {{ stats.categories.PHISHING }}x {{ t('stats.phishing') }}
        </div>
        <div v-if="stats.categories.SPAM" class="stat-alert stat-alert-warning">
          {{ stats.categories.SPAM }}x {{ t('stats.spam') }}
        </div>
        <div v-if="stats.categories.FYI" class="stat-alert stat-alert-muted">
          {{ stats.categories.FYI }}x {{ t('stats.fyi') }}
        </div>
        <div v-if="stats.categories.ACTIONABLE" class="stat-alert stat-alert-info">
          {{ stats.categories.ACTIONABLE }}x {{ t('stats.actionable') }}
        </div>
      </div>
    </div>

    <HealthPanel />
  </template>
</template>
