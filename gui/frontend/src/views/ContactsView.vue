<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useContactsStore } from '../stores/contacts'
import { useProfileStore } from '../stores/profile'
import { useJobStore } from '../stores/job'
import { useAppStore } from '../stores/app'
import ContactList from '../components/contacts/ContactList.vue'
import ContactForm from '../components/contacts/ContactForm.vue'
import ProgressBar from '../components/jobs/ProgressBar.vue'

const { t } = useI18n()
const store = useContactsStore()
const profileStore = useProfileStore()
const jobStore = useJobStore()
const appStore = useAppStore()

const editing = ref(false)
const buildEmail = ref('')

onMounted(() => {
  store.fetchContacts()
})

async function selectContact(email: string) {
  await store.loadContact(email)
  editing.value = true
}

async function onSave(email: string, data: any) {
  await store.saveContact(email, data)
  editing.value = false
}

async function onDelete(email: string) {
  await store.removeContact(email)
  editing.value = false
}

function closeForm() {
  editing.value = false
}

async function buildSingle() {
  if (!profileStore.activeProfileName || !appStore.sessionPassword || !buildEmail.value) return
  await jobStore.startBuildContact(profileStore.activeProfileName, appStore.sessionPassword, buildEmail.value)
}

async function buildTop() {
  if (!profileStore.activeProfileName || !appStore.sessionPassword) return
  await jobStore.startBuildContacts(profileStore.activeProfileName, appStore.sessionPassword)
}
</script>

<template>
  <h1 style="font-size: 20px; margin-bottom: 20px;">{{ t('contacts.title') }}</h1>

  <!-- Build tools -->
  <div class="card">
    <h3>{{ t('contacts.buildSingle') }}</h3>
    <div class="form-row">
      <div class="form-group">
        <label>{{ t('contacts.email') }}</label>
        <input v-model="buildEmail" type="email" />
      </div>
      <div class="form-group">
        <label>{{ t('dashboard.password') }}</label>
        <input v-model="appStore.sessionPassword" type="password" />
      </div>
    </div>
    <div class="btn-group">
      <button class="btn btn-primary" @click="buildSingle" :disabled="!buildEmail || !appStore.sessionPassword || jobStore.polling">
        {{ t('contacts.buildSingle') }}
      </button>
      <button class="btn btn-secondary" @click="buildTop" :disabled="!appStore.sessionPassword || jobStore.polling">
        {{ t('contacts.buildTop') }}
      </button>
    </div>
    <ProgressBar />
  </div>

  <!-- Contact form (when editing) -->
  <ContactForm
    v-if="editing && store.activeContact"
    :modelValue="store.activeContact"
    :email="store.activeEmail"
    @save="onSave"
    @delete="onDelete"
    @close="closeForm"
  />

  <!-- Contact list -->
  <ContactList @select="selectContact" />
</template>
