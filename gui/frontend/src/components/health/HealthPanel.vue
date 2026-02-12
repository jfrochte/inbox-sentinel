<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProfileStore } from '../../stores/profile'
import { useAppStore } from '../../stores/app'
import * as api from '../../api/client'

const { t } = useI18n()
const profileStore = useProfileStore()
const appStore = useAppStore()

const testing = ref(false)

async function testLlm() {
  const p = profileStore.activeProfile
  testing.value = true
  try {
    appStore.healthLlm = await api.checkLlm(p.ollama_url)
  } catch (e: any) {
    appStore.healthLlm = { ok: false, message: e.message, latency_ms: 0 }
  }
  testing.value = false
}

// Auto-test on mount if not yet tested
onMounted(() => {
  if (!appStore.healthLlm) {
    testLlm()
  }
})
</script>

<template>
  <div class="llm-status-bar">
    <span class="health-dot" :class="appStore.healthLlm ? (appStore.healthLlm.ok ? 'ok' : 'fail') : 'unknown'"></span>
    <span class="llm-status-label">{{ t('health.llm') }}</span>
    <span class="llm-status-text" v-if="testing">{{ t('health.checking') }}</span>
    <span class="llm-status-text" v-else-if="!appStore.healthLlm">{{ t('health.checking') }}</span>
    <span class="llm-status-text" v-else-if="appStore.healthLlm.ok">{{ t('health.ok') }} ({{ appStore.healthLlm.latency_ms }}ms)</span>
    <span class="llm-status-text text-danger" v-else>{{ appStore.healthLlm.message }}</span>
    <button class="btn btn-secondary btn-sm" @click="testLlm" :disabled="testing">{{ t('health.retest') }}</button>
  </div>
</template>
