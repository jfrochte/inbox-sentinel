"""Job execution routes (pipeline, contact build)."""

import threading
from datetime import date

from fastapi import APIRouter, HTTPException

from email_report.config import load_profile
from email_report.utils import load_prompt_file
from email_report.llm_profiles import load_llm_profiles
from email_report.i18n import set_language
from email_report.main import run_pipeline, build_single_contact, build_top_contacts
from gui.models import RunPipelineRequest, BuildContactRequest, BuildContactsRequest, JobStatusResponse, PipelineStats
from gui.progress import JobStore

router = APIRouter(prefix="/jobs", tags=["jobs"])
job_store = JobStore()


def _run_pipeline_thread(job_id: str, profile_name: str, password: str,
                         days_back: int, from_date: str, to_date: str):
    """Background thread for pipeline execution."""
    try:
        cfg = load_profile(profile_name)
        cfg.days_back = days_back

        # Parse explicit date range if provided
        start_date = None
        end_date = None
        if from_date and to_date:
            start_date = date.fromisoformat(from_date)
            end_date = date.fromisoformat(to_date)

        def progress_cb(phase, current, total, total_emails=None):
            job_store.update_progress(job_id, phase, current, total)
            if total_emails is not None:
                job = job_store.get_job(job_id)
                if job:
                    job.total_emails = total_emails

        job_store.update_progress(job_id, "starting", 0, 0)
        result = run_pipeline(cfg, password, progress_cb=progress_cb,
                              start_date=start_date, end_date=end_date)

        # Ensure total_emails is set from result as well
        job = job_store.get_job(job_id)
        if job:
            job.total_emails = result.get("total_emails", 0)

        job_store.complete_job(job_id, result.get("html", ""), result)
    except Exception as e:
        job_store.fail_job(job_id, str(e))


def _build_contact_thread(job_id: str, profile_name: str, password: str, email_addr: str):
    """Background thread for single contact build."""
    try:
        cfg = load_profile(profile_name)
        cfg.password = password
        set_language(cfg.language)
        profiles = load_llm_profiles()
        prompt_base = load_prompt_file("contact_prompt.txt")
        job_store.update_progress(job_id, "building_contact", 0, 1)
        result = build_single_contact(cfg, email_addr, prompt_base, llm_profile=profiles["extraction"])
        job_store.complete_job(job_id, "", result)
    except Exception as e:
        job_store.fail_job(job_id, str(e))


def _build_contacts_thread(job_id: str, profile_name: str, password: str):
    """Background thread for batch contact build."""
    try:
        cfg = load_profile(profile_name)
        cfg.password = password
        set_language(cfg.language)
        profiles = load_llm_profiles()
        prompt_base = load_prompt_file("contact_prompt.txt")

        def progress_cb(phase, current, total):
            job_store.update_progress(job_id, phase, current, total)

        job_store.update_progress(job_id, "collecting", 0, 0)
        result = build_top_contacts(cfg, prompt_base, llm_profile=profiles["extraction"], progress_cb=progress_cb)
        job_store.complete_job(job_id, "", result)
    except Exception as e:
        job_store.fail_job(job_id, str(e))


@router.post("/run-default")
def start_pipeline(req: RunPipelineRequest) -> dict:
    try:
        load_profile(req.profile)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))

    job_id = job_store.create_job()
    t = threading.Thread(
        target=_run_pipeline_thread,
        args=(job_id, req.profile, req.password, req.days_back,
              req.from_date, req.to_date),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@router.post("/build-contact")
def start_build_contact(req: BuildContactRequest) -> dict:
    try:
        load_profile(req.profile)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))

    job_id = job_store.create_job()
    t = threading.Thread(
        target=_build_contact_thread,
        args=(job_id, req.profile, req.password, req.email),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@router.post("/build-contacts")
def start_build_contacts(req: BuildContactsRequest) -> dict:
    try:
        load_profile(req.profile)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))

    job_id = job_store.create_job()
    t = threading.Thread(
        target=_build_contacts_thread,
        args=(job_id, req.profile, req.password),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    # Build stats from result when job is completed
    stats = None
    if job.status.value == "completed" and job.result:
        r = job.result
        stats = PipelineStats(
            total_emails=r.get("total_emails", 0),
            thread_count=r.get("thread_count", 0),
            unique_senders=r.get("unique_senders", 0),
            categories=r.get("categories", {}),
            draft_stats=r.get("draft_stats", {}),
            triage_stats=r.get("triage_stats", {}),
        )

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status.value,
        phase=job.phase,
        current=job.current,
        total=job.total,
        error=job.error,
        total_emails=job.total_emails,
        started_at=job.started_at,
        stats=stats,
    )


@router.get("/{job_id}/report")
def get_job_report(job_id: str):
    """Returns the HTML report for a completed job."""
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status.value != "completed":
        raise HTTPException(409, f"Job is {job.status.value}")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=job.result_html)
