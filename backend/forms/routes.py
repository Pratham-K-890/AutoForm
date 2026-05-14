"""Form-submission API routes.

Endpoints:
  POST /api/forms/submit                     – start a new form job
  GET  /api/forms/status/{submission_id}     – poll for status + results
  POST /api/forms/personal-info/{id}         – user provides personal answers
  GET  /api/forms/submissions                – list past submissions
  DELETE /api/forms/submissions/{id}         – delete a submission
"""

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.utils import get_current_user
from ..database import get_db
from ..models import ErrorLog, FormSubmission, GoogleToken, User
from ..queue_manager import queue_manager
from .personal_info import detect_personal_questions, split_questions
from .scraper import scrape_form
from .validator import validate_form
from .autofill import fill_and_submit
from .http_submit import http_submit_form
from ..llm.council import run_council

router = APIRouter(prefix="/api/forms", tags=["forms"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SubmitFormRequest(BaseModel):
    form_url: str


class PersonalInfoRequest(BaseModel):
    answers: dict[str, str]


# ── helpers ───────────────────────────────────────────────────────────────────

def _update_status(db: Session, submission: FormSubmission, status: str) -> None:
    submission.status = status
    submission.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


def _log_error(
    db: Session,
    error_type: str,
    message: str,
    user_id: Optional[int] = None,
    submission: Optional[FormSubmission] = None,
) -> None:
    log = ErrorLog(
        user_id=user_id,
        submission_id=submission.id if submission else None,
        error_type=error_type,
        error_message=message,
        stack_trace=traceback.format_exc(),
        form_url=submission.form_url if submission else None,
    )
    db.add(log)
    db.commit()


async def _get_credentials_async(user_id: int):
    """Async-safe: loads and (if needed) refreshes the Google OAuth credentials.

    Opens its own short-lived DB session so the token refresh HTTP call can
    run in a thread without sharing the main session across threads (SQLite /
    SQLAlchemy sessions are not thread-safe).
    """
    from ..database import SessionLocal
    from ..google_auth.routes import get_credentials

    def _sync_load():
        db = SessionLocal()
        try:
            token_row = db.query(GoogleToken).filter(GoogleToken.user_id == user_id).first()
            if not token_row:
                return None
            # get_credentials may call creds.refresh() — a blocking HTTP call.
            # Running inside asyncio.to_thread keeps the event loop free.
            return get_credentials(token_row, db=db)
        except Exception:
            return None
        finally:
            db.close()

    return await asyncio.to_thread(_sync_load)


# ── background processor ──────────────────────────────────────────────────────

async def _process_submission(submission_id: int) -> None:
    """Full form-filling pipeline, runs as a background task."""
    from ..database import SessionLocal

    db: Session = SessionLocal()

    # Guard: submission must exist
    submission = db.get(FormSubmission, submission_id)
    if submission is None:
        db.close()
        return

    user_id = submission.user_id
    acquired = False  # track whether the semaphore was actually acquired

    try:
        await queue_manager.acquire(user_id)
        acquired = True
        credentials = await _get_credentials_async(user_id)

        # ── 1. Validate ───────────────────────────────────────────────────────
        _update_status(db, submission, "validating")

        from .autofill import _inject_google_session, _user_data_dir
        udd = _user_data_dir(user_id)
        _common_args = ["--no-sandbox", "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage", "--disable-gpu",
                        "--disable-blink-features=AutomationControlled"]
        _ctx_kwargs = dict(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        async with async_playwright() as pw:
            if udd:
                try:
                    context = await pw.chromium.launch_persistent_context(
                        udd, channel="chrome", headless=True, args=_common_args, **_ctx_kwargs
                    )
                except Exception:
                    context = await pw.chromium.launch_persistent_context(
                        udd, headless=True, args=_common_args, **_ctx_kwargs
                    )
                browser = None
            else:
                browser = await pw.chromium.launch(headless=True, args=_common_args)
                context = await browser.new_context(**_ctx_kwargs)

            if credentials:
                await _inject_google_session(context, credentials)

            page = await context.new_page()

            validation = await validate_form(page, submission.form_url)
            if not validation.ok:
                submission.error_message = validation.error_message
                submission.status = "failed"
                db.commit()
                _log_error(db, validation.error_type or "validation",
                           validation.error_message, user_id, submission)
                await page.close()
                await context.close()
                if browser:
                    await browser.close()
                return

            if validation.has_file_upload:
                submission.has_file_upload = True
                db.commit()

            # ── 2. Scrape ─────────────────────────────────────────────────────
            _update_status(db, submission, "scraping")
            form_title, form_type, questions = await scrape_form(page)
            await page.close()
            await context.close()
            if browser:
                await browser.close()

        # Filter out file-upload questions
        questions = [q for q in questions if q["type"] != "file_upload"]

        if not questions:
            msg = (
                "No answerable questions found on this form. "
                "The form may be empty, use an unsupported layout, or require a login."
            )
            submission.error_message = msg
            _update_status(db, submission, "failed")
            _log_error(db, "no_questions", msg, user_id, submission)
            return

        submission.questions_json = json.dumps(questions)
        submission.form_type = form_type
        db.commit()

        # ── 3. Detect personal info ───────────────────────────────────────────
        personal_qs = detect_personal_questions(questions)
        personal_ids = {q["id"] for q in personal_qs}
        _, non_personal_qs = split_questions(questions, personal_ids)

        if personal_qs:
            submission.personal_info_questions_json = json.dumps(personal_qs)
            _update_status(db, submission, "personal_info_needed")

            # Wait for the user to provide their details (up to 10 minutes)
            event = queue_manager.create_personal_info_event(submission_id)
            try:
                await asyncio.wait_for(event.wait(), timeout=600)
            except asyncio.TimeoutError:
                submission.error_message = "Timed out waiting for personal info."
                _update_status(db, submission, "failed")
                return
            finally:
                queue_manager.cleanup_event(submission_id)

            db.refresh(submission)

        # ── 4. LLM council ────────────────────────────────────────────────────
        _update_status(db, submission, "llm_voting")
        council_results = await run_council(non_personal_qs, form_type, form_title)

        # Build the final answers dict
        answers: dict[str, Any] = {}

        # User-provided personal info
        if submission.user_personal_info_json:
            user_info = json.loads(submission.user_personal_info_json)
            answers.update(user_info)

        # LLM-voted answers
        for q_id, result in council_results.items():
            answers[q_id] = result["answer"]

        submission.answers_json = json.dumps(answers)
        submission.result_json = json.dumps(
            {q_id: {
                "question": next(
                    (q["text"] for q in questions if q["id"] == q_id), q_id
                ),
                "answer": res["answer"],
                "confidence": res.get("confidence", 0),
                "models_agreed": res.get("models_agreed", 0),
                "tiebreak": res.get("tiebreak", False),
             }
             for q_id, res in council_results.items()}
        )
        db.commit()

        # ── 5. Submit ─────────────────────────────────────────────────────────
        _update_status(db, submission, "filling")

        # Check whether we have entry_ids (needed for HTTP submission)
        has_entry_ids = any(q.get("entry_id") for q in questions)

        # Primary: direct HTTP POST — fast, no browser, no sign-in needed for
        # public forms.  Falls back to Playwright if entry IDs are missing.
        if has_entry_ids:
            access_token = None
            if credentials:
                try:
                    access_token = credentials.token
                except Exception:
                    pass
            fill_result = await http_submit_form(
                submission.form_url, questions, answers, access_token
            )
        else:
            # No entry IDs scraped — fall back to Playwright browser filling
            fill_result = await fill_and_submit(
                submission.form_url, questions, answers, credentials,
                user_id=user_id,
            )

        if fill_result["success"]:
            _update_status(db, submission, "completed")
        else:
            submission.error_message = fill_result["message"]
            _update_status(db, submission, "failed")
            _log_error(db, "fill_failed", fill_result["message"], user_id, submission)

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        submission.error_message = msg
        _update_status(db, submission, "failed")
        _log_error(db, "unexpected", msg, user_id, submission)
    finally:
        if acquired:
            queue_manager.release(user_id)
        db.close()


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.post("/submit", status_code=202)
async def submit_form(
    body: SubmitFormRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from urllib.parse import urlparse
    parsed = urlparse(body.form_url)
    if not (parsed.scheme == "https"
            and parsed.netloc == "docs.google.com"
            and parsed.path.startswith("/forms/")):
        raise HTTPException(status_code=400, detail="Only Google Forms URLs are supported.")

    submission = FormSubmission(user_id=current_user.id, form_url=body.form_url)
    db.add(submission)
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(_process_submission, submission.id)
    return {"submission_id": submission.id, "status": "pending"}


@router.get("/status/{submission_id}")
def get_status(
    submission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission = db.get(FormSubmission, submission_id)
    if not submission or submission.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Submission not found")

    response: dict = {
        "id": submission.id,
        "form_url": submission.form_url,
        "status": submission.status,
        "form_type": submission.form_type,
        "has_file_upload": submission.has_file_upload,
        "error_message": submission.error_message,
        "created_at": submission.created_at.isoformat(),
        "updated_at": submission.updated_at.isoformat() if submission.updated_at else None,
    }

    if submission.status == "personal_info_needed" and submission.personal_info_questions_json:
        response["personal_info_questions"] = json.loads(submission.personal_info_questions_json)

    if submission.result_json:
        response["results"] = json.loads(submission.result_json)

    if submission.answers_json:
        response["answers"] = json.loads(submission.answers_json)

    return response


@router.post("/personal-info/{submission_id}")
def submit_personal_info(
    submission_id: int,
    body: PersonalInfoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission = db.get(FormSubmission, submission_id)
    if not submission or submission.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status != "personal_info_needed":
        raise HTTPException(status_code=409, detail="Submission is not waiting for personal info")

    submission.user_personal_info_json = json.dumps(body.answers)
    db.commit()

    # Wake up the waiting background task
    queue_manager.signal_personal_info_ready(submission_id)
    return {"success": True}


@router.get("/submissions")
def list_submissions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 20,
    offset: int = 0,
):
    limit = min(limit, 100)  # hard cap — prevents accidental large queries
    rows = (
        db.query(FormSubmission)
        .filter(FormSubmission.user_id == current_user.id)
        .order_by(FormSubmission.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "form_url": r.form_url,
            "status": r.status,
            "form_type": r.form_type,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.delete("/submissions/{submission_id}")
def delete_submission(
    submission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission = db.get(FormSubmission, submission_id)
    if not submission or submission.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    db.delete(submission)
    db.commit()
    return {"success": True}
