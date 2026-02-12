const BASE = '/api'

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) {
    opts.body = JSON.stringify(body)
  }
  const resp = await fetch(`${BASE}${path}`, opts)
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`${resp.status}: ${text}`)
  }
  const ct = resp.headers.get('content-type') || ''
  if (ct.includes('text/html')) {
    return (await resp.text()) as unknown as T
  }
  return resp.json()
}

import type {
  ProfileData,
  Organization,
  JobStatus,
  HealthCheckResult,
  ContactSummary,
  ContactData,
} from './types'

// Profiles
export const getProfiles = () => request<string[]>('GET', '/profiles')
export const getProfile = (name: string) => request<ProfileData>('GET', `/profiles/${encodeURIComponent(name)}`)
export const putProfile = (name: string, data: ProfileData) => request<{ saved: string }>('PUT', `/profiles/${encodeURIComponent(name)}`, data)
export const deleteProfile = (name: string) => request<{ deleted: boolean }>('DELETE', `/profiles/${encodeURIComponent(name)}`)

// Config
export const getOrganizations = () => request<Organization[]>('GET', '/organizations')
export const getLlmModels = (url: string) => request<string[]>('GET', `/llm-models?url=${encodeURIComponent(url)}`)

// Jobs
export const startPipeline = (profile: string, password: string, fromDate: string, toDate: string) =>
  request<{ job_id: string }>('POST', '/jobs/run-default', { profile, password, from_date: fromDate, to_date: toDate })
export const startBuildContact = (profile: string, password: string, email: string) =>
  request<{ job_id: string }>('POST', '/jobs/build-contact', { profile, password, email })
export const startBuildContacts = (profile: string, password: string) =>
  request<{ job_id: string }>('POST', '/jobs/build-contacts', { profile, password })
export const getJobStatus = (id: string) => request<JobStatus>('GET', `/jobs/${id}`)
export const getJobReport = (id: string) => request<string>('GET', `/jobs/${id}/report`)

// Health
export const checkImap = (server: string, port: number, username: string, password: string) =>
  request<HealthCheckResult>('POST', '/health/imap', { server, port, username, password })
export const checkSmtp = (server: string, port: number, username: string, password: string, ssl: boolean) =>
  request<HealthCheckResult>('POST', '/health/smtp', { server, port, username, password, ssl })
export const checkLlm = (ollama_url: string) =>
  request<HealthCheckResult>('POST', '/health/llm', { ollama_url })

// Contacts
export const getContacts = () => request<ContactSummary[]>('GET', '/contacts')
export const getContact = (email: string) => request<ContactData>('GET', `/contacts/${encodeURIComponent(email)}`)
export const putContact = (email: string, data: ContactData) => request<{ saved: boolean }>('PUT', `/contacts/${encodeURIComponent(email)}`, data)
export const deleteContact = (email: string) => request<{ deleted: boolean }>('DELETE', `/contacts/${encodeURIComponent(email)}`)
export const autoUpdateContact = (email: string, profile: string, password: string) =>
  request<ContactData>('POST', `/contacts/${encodeURIComponent(email)}/auto-update`, { profile, password })
