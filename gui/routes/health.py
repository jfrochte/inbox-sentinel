"""Health check routes."""

from fastapi import APIRouter

from gui.models import ImapCheckRequest, SmtpCheckRequest, LlmCheckRequest, HealthCheckResponse
from gui.service import check_imap, check_smtp, check_llm

router = APIRouter(prefix="/health", tags=["health"])


@router.post("/imap", response_model=HealthCheckResponse)
def test_imap(req: ImapCheckRequest):
    return check_imap(req.server, req.port, req.username, req.password)


@router.post("/smtp", response_model=HealthCheckResponse)
def test_smtp(req: SmtpCheckRequest):
    return check_smtp(req.server, req.port, req.username, req.password, req.ssl)


@router.post("/llm", response_model=HealthCheckResponse)
def test_llm(req: LlmCheckRequest):
    return check_llm(req.ollama_url)
