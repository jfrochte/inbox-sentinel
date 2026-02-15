<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ProfileData, Organization } from '../../api/types'
import * as api from '../../api/client'

const { t } = useI18n()

const props = defineProps<{
  modelValue: ProfileData
  profileName: string
}>()

const emit = defineEmits<{
  'update:modelValue': [data: ProfileData]
  'save': [name: string, data: ProfileData]
  'delete': [name: string]
}>()

const form = ref<ProfileData>({ ...props.modelValue })
const editName = ref(props.profileName)
const orgs = ref<Organization[]>([])
const models = ref<string[]>([])
const fetchingModels = ref(false)

watch(() => props.modelValue, (v) => { form.value = { ...v } }, { deep: true })
watch(() => props.profileName, (v) => { editName.value = v })

onMounted(async () => {
  orgs.value = await api.getOrganizations()
})

function applyOrg(key: string) {
  const org = orgs.value.find(o => o.key === key)
  if (org) {
    form.value.organization = org.key
    form.value.imap_server = org.imap_server
    form.value.imap_port = org.imap_port
    form.value.smtp_server = org.smtp_server
    form.value.smtp_port = org.smtp_port
    form.value.smtp_ssl = org.smtp_ssl
  } else {
    form.value.organization = ''
  }
}

async function fetchModels() {
  fetchingModels.value = true
  try {
    models.value = await api.getLlmModels(form.value.ollama_url)
  } catch { /* ignore */ }
  fetchingModels.value = false
}

function save() {
  if (!editName.value.trim()) return
  emit('save', editName.value.trim(), form.value)
}

function del() {
  if (editName.value && confirm(t('profile.confirmDelete', { name: editName.value }))) {
    emit('delete', editName.value)
  }
}
</script>

<template>
  <div class="card">
    <div class="form-group">
      <label>{{ t('profile.name') }}</label>
      <input v-model="editName" :placeholder="t('profile.name')" />
    </div>

    <!-- Server section -->
    <div class="section-title">{{ t('profile.sectionServer') }}</div>
    <div class="form-group">
      <label>{{ t('profile.organization') }}</label>
      <select :value="form.organization" @change="applyOrg(($event.target as HTMLSelectElement).value)">
        <option value="">{{ t('profile.customServer') }}</option>
        <option v-for="org in orgs" :key="org.key" :value="org.key">{{ org.label }}</option>
      </select>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('profile.imapServer') }}</label>
        <input v-model="form.imap_server" />
      </div>
      <div class="form-group">
        <label>{{ t('profile.imapPort') }}</label>
        <input type="number" v-model.number="form.imap_port" />
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('profile.smtpServer') }}</label>
        <input v-model="form.smtp_server" />
      </div>
      <div class="form-group">
        <label>{{ t('profile.smtpPort') }}</label>
        <input type="number" v-model.number="form.smtp_port" />
      </div>
    </div>
    <div class="checkbox-group">
      <input type="checkbox" id="smtp_ssl" v-model="form.smtp_ssl" />
      <label for="smtp_ssl">{{ t('profile.smtpSsl') }}</label>
    </div>

    <!-- Account section -->
    <div class="section-title">{{ t('profile.sectionAccount') }}</div>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('profile.username') }} <span class="info-badge" :title="t('profile.usernameHint')">i</span></label>
        <input v-model="form.username" />
      </div>
      <div class="form-group">
        <label>{{ t('profile.fromEmail') }} <span class="info-badge" :title="t('profile.fromEmailHint')">i</span></label>
        <input v-model="form.from_email" type="email" />
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('profile.recipientEmail') }} <span class="info-badge" :title="t('profile.recipientEmailHint')">i</span></label>
        <input v-model="form.recipient_email" type="email" />
      </div>
      <div class="form-group">
        <label>{{ t('profile.displayName') }} <span class="info-badge" :title="t('profile.displayNameHint')">i</span></label>
        <input v-model="form.name" />
      </div>
    </div>
    <div class="form-group">
      <label>{{ t('profile.roles') }} <span class="info-badge" :title="t('profile.rolesHint')">i</span></label>
      <input v-model="form.roles" />
    </div>

    <!-- Mailbox section -->
    <div class="section-title">{{ t('profile.sectionMailbox') }}</div>
    <div class="form-group">
      <label>{{ t('profile.mailbox') }}</label>
      <input v-model="form.mailbox" />
    </div>
    <div class="checkbox-group">
      <input type="checkbox" id="skip_own" v-model="form.skip_own_sent" />
      <label for="skip_own">
        {{ t('profile.skipOwnSent') }}
        <span class="info-badge" :title="t('profile.skipOwnSentHint')">i</span>
      </label>
    </div>
    <div class="checkbox-group">
      <input type="checkbox" id="use_sentdate" v-model="form.use_sentdate" />
      <label for="use_sentdate">
        {{ t('profile.useSentdate') }}
        <span class="info-badge" :title="t('profile.useSentdateHint')">i</span>
      </label>
    </div>

    <!-- LLM section -->
    <div class="section-title">{{ t('profile.sectionLlm') }}</div>
    <div class="form-group">
      <label>{{ t('profile.ollamaUrl') }}</label>
      <input v-model="form.ollama_url" />
    </div>
    <div class="form-group">
      <label>{{ t('profile.model') }}</label>
      <div style="display: flex; gap: 8px;">
        <select v-if="models.length" v-model="form.model" style="flex: 1;">
          <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
        </select>
        <input v-else v-model="form.model" style="flex: 1;" />
        <button class="btn btn-secondary" @click="fetchModels" :disabled="fetchingModels">
          {{ t('profile.fetchModels') }}
        </button>
      </div>
    </div>

    <!-- Features section -->
    <div class="section-title">{{ t('profile.sectionFeatures') }}</div>
    <div class="checkbox-group">
      <input type="checkbox" id="auto_triage" v-model="form.auto_triage" />
      <label for="auto_triage">{{ t('profile.autoTriage') }}</label>
    </div>

    <!-- Auto-Draft sub-section -->
    <div class="checkbox-group">
      <input type="checkbox" id="auto_draft" v-model="form.auto_draft" />
      <label for="auto_draft">{{ t('profile.autoDraft') }}</label>
    </div>
    <div v-if="form.auto_draft" class="form-row" style="margin-left: 26px;">
      <div class="form-group">
        <label>{{ t('profile.draftsFolder') }}</label>
        <input v-model="form.drafts_folder" />
      </div>
      <div class="form-group">
        <label>{{ t('profile.signatureFile') }}</label>
        <input v-model="form.signature_file" />
      </div>
    </div>

    <!-- Auto-Contacts sub-section -->
    <div class="checkbox-group">
      <input type="checkbox" id="auto_contacts" v-model="form.auto_contacts_lazy" />
      <label for="auto_contacts">{{ t('profile.autoContactsLazy') }}</label>
    </div>
    <div v-if="form.auto_contacts_lazy" class="form-group" style="margin-left: 26px;">
      <label>{{ t('profile.sentFolder') }}</label>
      <input v-model="form.sent_folder" />
    </div>

    <!-- Language -->
    <div class="section-title">{{ t('app.language') }}</div>
    <div class="form-group">
      <select v-model="form.language">
        <option value="en">English</option>
        <option value="de">Deutsch</option>
      </select>
    </div>

    <!-- Actions -->
    <div class="btn-group">
      <button class="btn btn-primary" @click="save">{{ t('profile.save') }}</button>
      <button class="btn btn-danger" @click="del" v-if="profileName">{{ t('profile.delete') }}</button>
    </div>
  </div>
</template>
