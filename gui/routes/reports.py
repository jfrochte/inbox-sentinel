"""Report preview route."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from gui.routes.jobs import job_store

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{job_id}")
def get_report(job_id: str):
    """Returns the HTML report for a completed job."""
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status.value != "completed":
        raise HTTPException(409, f"Job is {job.status.value}")
    return HTMLResponse(content=job.result_html)
