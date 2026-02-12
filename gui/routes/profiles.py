"""Profile CRUD routes."""

from fastapi import APIRouter, HTTPException

from email_report.config import Config, list_profiles, load_profile, save_profile, delete_profile
from gui.models import ProfileData

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("")
def get_profiles() -> list[str]:
    return list_profiles()


@router.get("/{name}")
def get_profile(name: str) -> ProfileData:
    try:
        cfg = load_profile(name)
    except FileNotFoundError:
        raise HTTPException(404, f"Profile '{name}' not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ProfileData(**cfg.to_profile_dict())


@router.put("/{name}")
def put_profile(name: str, data: ProfileData) -> dict:
    try:
        cfg = Config.from_profile_dict(data.model_dump())
        path = save_profile(name, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"saved": path}


@router.delete("/{name}")
def del_profile(name: str) -> dict:
    try:
        deleted = delete_profile(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not deleted:
        raise HTTPException(404, f"Profile '{name}' not found")
    return {"deleted": True}
