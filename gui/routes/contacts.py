"""Contact CRUD routes."""

from fastapi import APIRouter, HTTPException

from gui.models import ContactSummary, ContactData, ContactAutoUpdateRequest
from gui.service import list_contacts, get_contact, update_contact, delete_contact, preview_contact_update

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactSummary])
def get_contacts():
    return list_contacts()


@router.get("/{email}")
def get_contact_detail(email: str):
    data = get_contact(email)
    if data is None:
        raise HTTPException(404, f"Contact '{email}' not found")
    return data


@router.put("/{email}")
def put_contact(email: str, data: ContactData):
    update_contact(email, data.model_dump())
    return {"saved": True}


@router.delete("/{email}")
def del_contact(email: str):
    if not delete_contact(email):
        raise HTTPException(404, f"Contact '{email}' not found")
    return {"deleted": True}


@router.post("/{email}/auto-update")
def auto_update_contact(email: str, req: ContactAutoUpdateRequest):
    """Rebuilds a contact via IMAP + LLM and returns the proposed data without saving."""
    try:
        proposed = preview_contact_update(email, req.profile, req.password)
        if proposed is None:
            raise HTTPException(404, f"Could not build contact for '{email}'")
        return proposed
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
