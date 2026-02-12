<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useProfileStore } from '../stores/profile'
import { useAppStore } from '../stores/app'
import ProfileForm from '../components/profile/ProfileForm.vue'
import type { ProfileData } from '../api/types'

const { t } = useI18n()
const profileStore = useProfileStore()
const appStore = useAppStore()

async function selectProfile(name: string) {
  await profileStore.loadProfile(name)
  appStore.setLanguage(profileStore.activeProfile.language)
}

async function onSave(name: string, data: ProfileData) {
  await profileStore.saveProfile(name, data)
  appStore.setLanguage(data.language)
}

async function onDelete(name: string) {
  await profileStore.removeProfile(name)
}
</script>

<template>
  <h1 style="font-size: 20px; margin-bottom: 20px;">{{ t('profile.title') }}</h1>

  <div style="display: grid; grid-template-columns: 200px 1fr; gap: 16px;">
    <!-- Profile list -->
    <div class="card" style="padding: 0;">
      <ul class="profile-list">
        <li
          v-for="p in profileStore.profiles"
          :key="p"
          :class="{ active: p === profileStore.activeProfileName }"
          @click="selectProfile(p)"
        >
          {{ p }}
        </li>
      </ul>
      <div style="padding: 12px;">
        <button class="btn btn-secondary" style="width: 100%;" @click="profileStore.newProfile()">
          + {{ t('profile.newProfile') }}
        </button>
      </div>
    </div>

    <!-- Profile form -->
    <ProfileForm
      :modelValue="profileStore.activeProfile"
      :profileName="profileStore.activeProfileName"
      @save="onSave"
      @delete="onDelete"
    />
  </div>
</template>
