<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProfileStore } from '../../stores/profile'
import { useAppStore } from '../../stores/app'
import type { ContactData } from '../../api/types'
import * as api from '../../api/client'

const { t } = useI18n()
const profileStore = useProfileStore()
const appStore = useAppStore()

const props = defineProps<{
  modelValue: ContactData
  email: string
}>()

const emit = defineEmits<{
  save: [email: string, data: ContactData]
  delete: [email: string]
  close: []
}>()

const form = ref<ContactData>({ ...props.modelValue })
const telString = ref((props.modelValue.TEL || []).join(', '))

// Auto-update state
const updating = ref(false)
const updateError = ref('')
const proposed = ref<ContactData | null>(null)

// Fields that can be auto-updated (exclude FN and EMAIL which define the contact)
const diffFields = ['ORG', 'TITLE', 'ROLE', 'CATEGORIES', 'URL', 'NOTE', 'NICKNAME', 'ADR', 'BDAY', 'TZ'] as const

const changedFields = computed(() => {
  if (!proposed.value) return []
  return diffFields.filter(key => {
    const cur = (form.value as any)[key] || ''
    const prop = (proposed.value as any)[key] || ''
    return cur !== prop
  })
})

watch(() => props.modelValue, (v) => {
  form.value = { ...v }
  telString.value = (v.TEL || []).join(', ')
  proposed.value = null
  updateError.value = ''
}, { deep: true })

function save() {
  form.value.TEL = telString.value.split(',').map(s => s.trim()).filter(Boolean)
  emit('save', props.email, form.value)
}

function del() {
  if (confirm(t('contacts.confirmDelete', { email: props.email }))) {
    emit('delete', props.email)
  }
}

async function autoUpdate() {
  if (!profileStore.activeProfileName || !appStore.sessionPassword) return
  updating.value = true
  updateError.value = ''
  proposed.value = null
  try {
    const result = await api.autoUpdateContact(props.email, profileStore.activeProfileName, appStore.sessionPassword)
    proposed.value = result
  } catch (e: any) {
    updateError.value = e.message || t('contacts.autoUpdateFailed')
  }
  updating.value = false
}

function acceptField(key: string) {
  if (!proposed.value) return
  ;(form.value as any)[key] = (proposed.value as any)[key]
  // Also update TEL string if TEL was accepted
  if (key === 'TEL') {
    telString.value = (form.value.TEL || []).join(', ')
  }
}

function dismissProposed() {
  proposed.value = null
}
</script>

<template>
  <div class="card">
    <h3>{{ email }}</h3>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('contacts.name') }} (FN)</label>
        <input v-model="form.FN" />
      </div>
      <div class="form-group">
        <label>{{ t('contacts.org') }} (ORG)</label>
        <input v-model="form.ORG" />
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('contacts.jobTitle') }} (TITLE)</label>
        <input v-model="form.TITLE" />
      </div>
      <div class="form-group">
        <label>ROLE</label>
        <input v-model="form.ROLE" />
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>CATEGORIES</label>
        <input v-model="form.CATEGORIES" />
      </div>
      <div class="form-group">
        <label>URL</label>
        <input v-model="form.URL" />
      </div>
    </div>
    <div class="form-group">
      <label>TEL (comma-separated)</label>
      <input v-model="telString" />
    </div>
    <div class="form-group">
      <label>NOTE</label>
      <textarea v-model="form.NOTE" rows="6"></textarea>
    </div>
    <div class="btn-group">
      <button class="btn btn-primary" @click="save">{{ t('contacts.save') }}</button>
      <button class="btn btn-danger" @click="del">{{ t('contacts.delete') }}</button>
      <button class="btn btn-secondary" @click="emit('close')">{{ t('contacts.close') }}</button>
      <button
        class="btn btn-secondary"
        @click="autoUpdate"
        :disabled="updating || !profileStore.activeProfileName || !appStore.sessionPassword"
      >
        {{ updating ? t('contacts.autoUpdateRunning') : t('contacts.autoUpdate') }}
      </button>
    </div>

    <!-- Auto-update error -->
    <div v-if="updateError" class="text-danger mt-4">{{ updateError }}</div>

    <!-- Auto-update diff panel -->
    <div v-if="proposed" class="diff-panel mt-4">
      <div class="diff-header">
        <h3>{{ t('contacts.proposedChanges') }}</h3>
        <button class="btn btn-secondary btn-sm" @click="dismissProposed">&times;</button>
      </div>

      <div v-if="changedFields.length === 0" class="text-muted" style="padding: 8px 0;">
        {{ t('contacts.noChanges') }}
      </div>

      <div v-for="key in changedFields" :key="key" class="diff-row">
        <div class="diff-field-name">{{ key }}</div>
        <div class="diff-values">
          <div class="diff-current">
            <span class="diff-label">{{ t('contacts.fieldCurrent') }}</span>
            <div class="diff-value">{{ (form as any)[key] || '—' }}</div>
          </div>
          <div class="diff-arrow">→</div>
          <div class="diff-proposed">
            <span class="diff-label">{{ t('contacts.fieldProposed') }}</span>
            <div class="diff-value">{{ (proposed as any)[key] || '—' }}</div>
          </div>
          <button class="btn btn-primary btn-sm" @click="acceptField(key)">{{ t('contacts.acceptField') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
