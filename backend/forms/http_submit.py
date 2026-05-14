"""Direct HTTP form submission for Google Forms.

Submits by POSTing to the formResponse endpoint — no browser automation,
no Google sign-in required for public forms.  For forms that require
sign-in, includes the OAuth access token as a Bearer header.
"""

import random
import re
from typing import Any

import httpx


def _form_response_url(form_url: str) -> str | None:
    """Convert any Google Forms view URL to the formResponse POST endpoint."""
    # Normalise: extract the form ID path segment
    m = re.search(r"/forms/d/e/([^/?#]+)", form_url)
    if m:
        return f"https://docs.google.com/forms/d/e/{m.group(1)}/formResponse"
    m = re.search(r"/forms/d/([^/?#]+)", form_url)
    if m:
        # Older URL style — need the /e/ variant; try to derive it
        form_id = m.group(1)
        return f"https://docs.google.com/forms/d/e/{form_id}/formResponse"
    return None


def _build_post_body(questions: list[dict], answers: dict[str, Any]) -> list[tuple[str, str]]:
    """Build the list of (name, value) tuples for the form POST body."""
    data: list[tuple[str, str]] = []
    page_nums: set[int] = set()

    for q in questions:
        q_id = q["id"]
        entry_id = q.get("entry_id")
        if not entry_id or q_id not in answers:
            continue

        answer = answers[q_id]
        key = f"entry.{entry_id}"
        page_nums.add(q.get("page", 0))
        q_type = q.get("type", "short_text")

        if q_type == "checkbox":
            items = answer if isinstance(answer, list) else [answer]
            for item in items:
                data.append((key, str(item)))
        elif q_type == "date":
            # Expected format: YYYY-MM-DD
            try:
                yyyy, mm, dd = str(answer).split("-")
                data.append((f"{key}_year", yyyy))
                data.append((f"{key}_month", mm))
                data.append((f"{key}_day", dd))
            except Exception:
                data.append((key, str(answer)))
        elif q_type == "time":
            # Expected format: HH:MM
            try:
                parts = str(answer).split(":")
                data.append((f"{key}_hour", parts[0]))
                data.append((f"{key}_minute", parts[1]))
            except Exception:
                data.append((key, str(answer)))
        else:
            data.append((key, str(answer)))

    # Required Google Forms metadata fields
    max_page = max(page_nums) if page_nums else 0
    page_history = ",".join(str(i) for i in range(max_page + 1))
    data.append(("fvv", "1"))
    data.append(("pageHistory", page_history))
    # fbzx is a random tracking token Google includes; any large integer works
    data.append(("fbzx", str(random.randint(-9_000_000_000_000_000_000, 9_000_000_000_000_000_000))))

    return data


_CONFIRM_PHRASES = [
    "your response has been recorded",
    "thanks for submitting",
    "thank you",
    "submitted",
    "recorded",
    "successfully submitted",
    "your response has been saved",
    "responses are not being accepted",
]


def _is_success(url: str, body: str) -> bool:
    if "formresponse" in url.lower():
        return True
    return any(ph in body.lower() for ph in _CONFIRM_PHRASES)


async def http_submit_form(
    form_url: str,
    questions: list[dict],
    answers: dict[str, Any],
    access_token: str | None = None,
) -> dict:
    """Submit a Google Form via direct HTTP POST.

    Works without a browser.  For public forms no credentials are needed.
    For sign-in-required forms the access_token is sent as a Bearer header
    (Google may or may not honour it for formResponse, but it is worth trying).

    Returns { "success": bool, "message": str }.
    """
    submit_url = _form_response_url(form_url)
    if not submit_url:
        return {"success": False, "message": "Could not determine form submission URL."}

    post_data = _build_post_body(questions, answers)
    if not post_data:
        return {"success": False, "message": "No entry IDs found — cannot submit via HTTP."}

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": form_url,
        "Origin": "https://docs.google.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.post(submit_url, data=post_data, headers=headers)

        success = _is_success(str(resp.url), resp.text)
        if success:
            return {"success": True, "message": "Form submitted successfully!"}

        # If we got a 401/403, the form requires sign-in and the token didn't help
        if resp.status_code in (401, 403):
            return {
                "success": False,
                "message": (
                    "This form requires Google sign-in and the submission was rejected. "
                    "The form owner has restricted responses to signed-in users only."
                ),
            }

        return {
            "success": False,
            "message": f"Submission may have failed (HTTP {resp.status_code}). "
                       "The form confirmation page was not detected.",
        }
    except Exception as exc:
        return {"success": False, "message": f"HTTP submission error: {exc}"}
