import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ProfileData } from '../api/types'
import { emptyProfile } from '../api/types'
import * as api from '../api/client'

export const useProfileStore = defineStore('profile', () => {
  const profiles = ref<string[]>([])
  const activeProfileName = ref('')
  const activeProfile = ref<ProfileData>(emptyProfile())
  const loading = ref(false)

  async function fetchProfiles() {
    profiles.value = await api.getProfiles()
  }

  async function loadProfile(name: string) {
    loading.value = true
    try {
      activeProfile.value = await api.getProfile(name)
      activeProfileName.value = name
    } finally {
      loading.value = false
    }
  }

  async function saveProfile(name: string, data: ProfileData) {
    await api.putProfile(name, data)
    activeProfileName.value = name
    activeProfile.value = data
    await fetchProfiles()
  }

  async function removeProfile(name: string) {
    await api.deleteProfile(name)
    if (activeProfileName.value === name) {
      activeProfileName.value = ''
      activeProfile.value = emptyProfile()
    }
    await fetchProfiles()
  }

  function newProfile() {
    activeProfileName.value = ''
    activeProfile.value = emptyProfile()
  }

  return {
    profiles, activeProfileName, activeProfile, loading,
    fetchProfiles, loadProfile, saveProfile, removeProfile, newProfile,
  }
})
