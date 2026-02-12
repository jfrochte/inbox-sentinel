<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useJobStore } from '../stores/job'

const { t } = useI18n()
const jobStore = useJobStore()

const filter = ref('all')
const iframeRef = ref<HTMLIFrameElement | null>(null)
const filterEmpty = ref(false)

const hasReport = computed(() => !!jobStore.reportHtml)

function writeReport(html: string) {
  if (!iframeRef.value || !html) return
  const doc = iframeRef.value.contentDocument
  if (doc) {
    doc.open()
    doc.write(html)
    doc.close()
    applyFilter()
  }
}

// Write HTML content into iframe when report changes
watch(() => jobStore.reportHtml, (html) => {
  if (html) {
    nextTick(() => writeReport(html))
  }
})

// Also write on mount if report already exists (navigating to this view after completion)
onMounted(() => {
  if (jobStore.reportHtml) {
    nextTick(() => writeReport(jobStore.reportHtml))
  }
})

function applyFilter() {
  if (!iframeRef.value) return
  const doc = iframeRef.value.contentDocument
  if (!doc) return

  // Remove any existing filter style
  const existingStyle = doc.getElementById('sentinel-filter')
  if (existingStyle) existingStyle.remove()

  filterEmpty.value = false

  if (filter.value === 'all') return

  // Priority/category filter: hide cards that don't match
  let css = ''
  if (filter.value === 'spam') {
    css = `.email-card:not([data-category="SPAM"]) { display: none !important; }`
  } else if (filter.value === 'danger') {
    css = `.email-card:not([data-category="PHISHING"]) { display: none !important; }`
  } else if (filter.value === 'high') {
    css = `.email-card:not([data-priority="1"]):not([data-priority="2"]) { display: none !important; }`
  } else if (filter.value === 'normal') {
    css = `.email-card:not([data-priority="3"]) { display: none !important; }`
  } else if (filter.value === 'low') {
    css = `.email-card[data-category="SPAM"], .email-card[data-category="PHISHING"],
           .email-card[data-priority="1"], .email-card[data-priority="2"],
           .email-card[data-priority="3"] { display: none !important; }`
  }

  if (css) {
    const style = doc.createElement('style')
    style.id = 'sentinel-filter'
    style.textContent = css
    doc.head.appendChild(style)

    // Check if any cards are still visible
    nextTick(() => {
      const cards = doc.querySelectorAll('.email-card')
      let anyVisible = false
      cards.forEach(card => {
        const el = card as HTMLElement
        if (el.style.display !== 'none' && getComputedStyle(el).display !== 'none') {
          anyVisible = true
        }
      })
      filterEmpty.value = !anyVisible
    })
  }
}

watch(filter, applyFilter)
</script>

<template>
  <h1 style="font-size: 20px; margin-bottom: 20px;">{{ t('report.title') }}</h1>

  <div v-if="!hasReport" class="card text-center text-muted">
    {{ t('report.noReport') }}
  </div>

  <template v-else>
    <div class="filter-group">
      <button class="filter-btn" :class="{ active: filter === 'all' }" @click="filter = 'all'">
        {{ t('report.filterAll') }}
      </button>
      <button class="filter-btn filter-spam" :class="{ active: filter === 'spam' }" @click="filter = 'spam'">
        {{ t('report.filterSpam') }}
      </button>
      <button class="filter-btn filter-danger" :class="{ active: filter === 'danger' }" @click="filter = 'danger'">
        {{ t('report.filterDanger') }}
      </button>
      <button class="filter-btn" :class="{ active: filter === 'high' }" @click="filter = 'high'">
        {{ t('report.filterHigh') }}
      </button>
      <button class="filter-btn" :class="{ active: filter === 'normal' }" @click="filter = 'normal'">
        {{ t('report.filterNormal') }}
      </button>
      <button class="filter-btn" :class="{ active: filter === 'low' }" @click="filter = 'low'">
        {{ t('report.filterLow') }}
      </button>
    </div>

    <div v-if="filterEmpty" class="card text-center text-muted">
      {{ t('report.filterEmpty') }}
    </div>

    <iframe ref="iframeRef" class="report-frame" :class="{ hidden: filterEmpty }" sandbox="allow-same-origin"></iframe>
  </template>
</template>
