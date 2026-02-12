"""Config-related routes (organizations, LLM models)."""

from fastapi import APIRouter, Query

from gui.service import list_organizations, fetch_llm_models

router = APIRouter(tags=["config"])


@router.get("/organizations")
def get_organizations() -> list[dict]:
    return list_organizations()


@router.get("/llm-models")
def get_llm_models(url: str = Query(..., description="Ollama API URL")) -> list[str]:
    return fetch_llm_models(url)
