import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ContactSummary, ContactData } from '../api/types'
import * as api from '../api/client'

export const useContactsStore = defineStore('contacts', () => {
  const contacts = ref<ContactSummary[]>([])
  const searchQuery = ref('')
  const activeContact = ref<ContactData | null>(null)
  const activeEmail = ref('')
  const loading = ref(false)

  const filtered = computed(() => {
    const q = searchQuery.value.toLowerCase()
    if (!q) return contacts.value
    return contacts.value.filter(
      c => c.email.toLowerCase().includes(q)
        || c.fn.toLowerCase().includes(q)
        || c.org.toLowerCase().includes(q),
    )
  })

  async function fetchContacts() {
    loading.value = true
    try {
      contacts.value = await api.getContacts()
    } finally {
      loading.value = false
    }
  }

  async function loadContact(email: string) {
    loading.value = true
    try {
      activeContact.value = await api.getContact(email)
      activeEmail.value = email
    } finally {
      loading.value = false
    }
  }

  async function saveContact(email: string, data: ContactData) {
    await api.putContact(email, data)
    await fetchContacts()
  }

  async function removeContact(email: string) {
    await api.deleteContact(email)
    if (activeEmail.value === email) {
      activeContact.value = null
      activeEmail.value = ''
    }
    await fetchContacts()
  }

  return {
    contacts, searchQuery, filtered, activeContact, activeEmail, loading,
    fetchContacts, loadContact, saveContact, removeContact,
  }
})
