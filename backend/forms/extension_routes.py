"""Extension API — called directly by the Chrome extension.

Accepts pre-scraped questions (from the browser DOM) and returns:
  - personal_questions : questions requiring user-supplied answers
  - ai_answers         : LLM council answers for the rest
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth.utils import get_current_user
from ..models import User
from ..llm.council import run_council
from .personal_info import detect_personal_questions, split_questions

router = APIRouter(prefix="/api/extension", tags=["extension"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ExtensionQuestion(BaseModel):
    id: str
    text: str
    type: str
    options: list[str] = []
    required: bool = False
    entry_id: Optional[str] = None
    image_url: Optional[str] = None


class AnalyzeRequest(BaseModel):
    questions: list[ExtensionQuestion]
    form_url: str
    form_title: str = ""
    form_type: str = "general"


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze_form(
    body: AnalyzeRequest,
    current_user: User = Depends(get_current_user),
):
    questions = [q.model_dump() for q in body.questions]

    # Detect personal questions (keyword-based, no LLM needed)
    personal_qs = detect_personal_questions(questions)
    personal_ids = {q["id"] for q in personal_qs}
    _, non_personal_qs = split_questions(questions, personal_ids)

    # Run the LLM council only on non-personal questions
    council_results = await run_council(
        non_personal_qs,
        body.form_type,
        body.form_title,
    )

    ai_answers = {
        q_id: result["answer"]
        for q_id, result in council_results.items()
    }

    return {
        "personal_questions": personal_qs,
        "ai_answers": ai_answers,
        "council_details": {
            q_id: {
                "confidence": r.get("confidence", 0),
                "models_agreed": r.get("models_agreed", 0),
                "tiebreak": r.get("tiebreak", False),
            }
            for q_id, r in council_results.items()
        },
    }
