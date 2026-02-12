<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useContactsStore } from '../../stores/contacts'

const { t } = useI18n()
const store = useContactsStore()

const emit = defineEmits<{ select: [email: string] }>()
</script>

<template>
  <div class="card">
    <h3>{{ t('contacts.title') }}</h3>
    <div class="form-group">
      <input :placeholder="t('contacts.search')" v-model="store.searchQuery" />
    </div>
    <table v-if="store.filtered.length">
      <thead>
        <tr>
          <th>{{ t('contacts.email') }}</th>
          <th>{{ t('contacts.name') }}</th>
          <th>{{ t('contacts.org') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="c in store.filtered" :key="c.email">
          <td>{{ c.email }}</td>
          <td>{{ c.fn }}</td>
          <td>{{ c.org }}</td>
          <td>
            <button class="btn btn-secondary" @click="emit('select', c.email)">
              {{ t('contacts.edit') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="text-muted text-center mt-4">{{ t('contacts.noContacts') }}</p>
  </div>
</template>
