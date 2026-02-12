export interface ProfileData {
  imap_server: string
  imap_port: number
  smtp_server: string
  smtp_port: number
  smtp_ssl: boolean
  organization: string
  username: string
  from_email: string
  recipient_email: string
  name: string
  roles: string
  mailbox: string
  skip_own_sent: boolean
  use_sentdate: boolean
  ollama_url: string
  model: string
  language: string
  auto_triage: boolean
  auto_draft: boolean
  drafts_folder: string
  signature_file: string
  auto_contacts_lazy: boolean
  sent_folder: string
}

export interface Organization {
  key: string
  label: string
  imap_server: string
  imap_port: number
  smtp_server: string
  smtp_port: number
  smtp_ssl: boolean
}

export interface PipelineStats {
  total_emails: number
  thread_count: number
  unique_senders: number
  categories: Record<string, number>
  draft_stats: Record<string, number>
  triage_stats: Record<string, number>
}

export interface JobStatus {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  phase: string
  current: number
  total: number
  error: string
  total_emails: number
  started_at: number
  stats: PipelineStats | null
}

export interface HealthCheckResult {
  ok: boolean
  message: string
  latency_ms: number
  models?: string[]
}

export interface ContactSummary {
  email: string
  fn: string
  org: string
  title: string
}

export interface ContactData {
  FN: string
  N: Record<string, string>
  NICKNAME: string
  EMAIL: string
  TEL: string[]
  ADR: string
  ORG: string
  TITLE: string
  ROLE: string
  URL: string
  NOTE: string
  BDAY: string
  CATEGORIES: string
  TZ: string
}

export function emptyProfile(): ProfileData {
  return {
    imap_server: '', imap_port: 993,
    smtp_server: '', smtp_port: 587, smtp_ssl: false,
    organization: '', username: '', from_email: '',
    recipient_email: '', name: '', roles: '',
    mailbox: 'INBOX', skip_own_sent: true, use_sentdate: true,
    ollama_url: 'http://localhost:11434/api/generate',
    model: 'gpt-os-20b',
    language: 'en', auto_triage: true, auto_draft: false,
    drafts_folder: 'Drafts', signature_file: '',
    auto_contacts_lazy: false, sent_folder: '',
  }
}
