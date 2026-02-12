import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { JobStatus } from '../api/types'
import * as api from '../api/client'

export const useJobStore = defineStore('job', () => {
  const activeJobId = ref('')
  const jobStatus = ref<JobStatus | null>(null)
  const reportHtml = ref('')
  const polling = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function startPipeline(profile: string, password: string, fromDate: string, toDate: string) {
    const { job_id } = await api.startPipeline(profile, password, fromDate, toDate)
    activeJobId.value = job_id
    reportHtml.value = ''
    startPolling()
  }

  async function startBuildContact(profile: string, password: string, email: string) {
    const { job_id } = await api.startBuildContact(profile, password, email)
    activeJobId.value = job_id
    startPolling()
  }

  async function startBuildContacts(profile: string, password: string) {
    const { job_id } = await api.startBuildContacts(profile, password)
    activeJobId.value = job_id
    startPolling()
  }

  function startPolling() {
    stopPolling()
    polling.value = true
    pollTimer = setInterval(pollJob, 1000)
  }

  function stopPolling() {
    polling.value = false
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function pollJob() {
    if (!activeJobId.value) return
    try {
      const status = await api.getJobStatus(activeJobId.value)
      jobStatus.value = status
      if (status.status === 'completed' || status.status === 'failed') {
        stopPolling()
        if (status.status === 'completed') {
          try {
            reportHtml.value = await api.getJobReport(activeJobId.value)
          } catch {
            // Report may not be available for contact-build jobs
          }
        }
      }
    } catch {
      stopPolling()
    }
  }

  return {
    activeJobId, jobStatus, reportHtml, polling,
    startPipeline, startBuildContact, startBuildContacts, stopPolling,
  }
})
