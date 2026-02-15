"""
models.py -- Pydantic request/response models for the GUI API.
"""

from pydantic import BaseModel


# ============================================================
# Profile
# ============================================================
class ProfileData(BaseModel):
    imap_server: str = ""
    imap_port: int = 993
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_ssl: bool = False
    organization: str = ""
    username: str = ""
    from_email: str = ""
    recipient_email: str = ""
    name: str = ""
    roles: str = ""
    mailbox: str = "INBOX"
    skip_own_sent: bool = True
    use_sentdate: bool = True
    ollama_url: str = "http://localhost:11434/api/generate"
    model: str = "gpt-os-20b"
    language: str = "en"
    auto_triage: bool = True
    auto_draft: bool = False
    drafts_folder: str = "Drafts"
    signature_file: str = ""
    auto_contacts_lazy: bool = False
    sent_folder: str = ""


# ============================================================
# Jobs
# ============================================================
class RunPipelineRequest(BaseModel):
    profile: str
    password: str
    days_back: int = 0
    from_date: str = ""
    to_date: str = ""


class BuildContactRequest(BaseModel):
    profile: str
    password: str
    email: str


class BuildContactsRequest(BaseModel):
    profile: str
    password: str


class PipelineStats(BaseModel):
    total_emails: int = 0
    thread_count: int = 0
    unique_senders: int = 0
    categories: dict[str, int] = {}
    draft_stats: dict[str, int] = {}
    triage_stats: dict[str, int] = {}


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    phase: str = ""
    current: int = 0
    total: int = 0
    error: str = ""
    total_emails: int = 0
    started_at: float = 0.0
    stats: PipelineStats | None = None


# ============================================================
# Health
# ============================================================
class ImapCheckRequest(BaseModel):
    server: str
    port: int = 993
    username: str
    password: str


class SmtpCheckRequest(BaseModel):
    server: str
    port: int = 587
    username: str
    password: str
    ssl: bool = False


class LlmCheckRequest(BaseModel):
    ollama_url: str = "http://localhost:11434/api/generate"
    model: str = ""


class HealthCheckResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int = 0
    models: list[str] | None = None


# ============================================================
# Contact
# ============================================================
class ContactSummary(BaseModel):
    email: str
    fn: str = ""
    org: str = ""
    title: str = ""


class ContactData(BaseModel):
    FN: str = ""
    N: dict = {}
    NICKNAME: str = ""
    EMAIL: str = ""
    TEL: list[str] = []
    ADR: str = ""
    ORG: str = ""
    TITLE: str = ""
    ROLE: str = ""
    URL: str = ""
    NOTE: str = ""
    BDAY: str = ""
    CATEGORIES: str = ""
    TZ: str = ""


class ContactAutoUpdateRequest(BaseModel):
    profile: str
    password: str
