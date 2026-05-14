"""Pre-scrape form validation.

Checks performed (in order):
  1. Form is closed / not accepting responses
  2. Form requires a GSuite / organisational account
  3. User already submitted the form (detected from confirmation/error page)
  4. Form contains file-upload questions (non-fatal warning)
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page


@dataclass
class ValidationResult:
    ok: bool
    error_type: Optional[str] = None   # closed | org_required | already_submitted
    error_message: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    has_file_upload: bool = False


# Text patterns that indicate the form is closed
_CLOSED_PATTERNS = [
    "no longer accepting responses",
    "this form is closed",
    "form is not accepting responses",
    "responses are closed",
]

# Text patterns that indicate an organisational account is required
_ORG_PATTERNS = [
    "you need to be signed in to an organisation",
    "you need to use an account within this organization",
    "sign in to a google account in the same organization",
    "this form can only be filled in by users in the organization",
    "your account is not from an allowed domain",
    "only people in the organization can fill",
    # Intentionally specific – "google workspace" alone appears in page footers
    "requires a google workspace account",
    "only google workspace accounts",
    "must use a google workspace",
]

# Text patterns for already-submitted
_ALREADY_SUBMITTED_PATTERNS = [
    "you've already responded",
    "you already submitted",
    "already filled out",
    "limit one response per person",
    "you've already filled in this form",
]


async def validate_form(page: Page, url: str) -> ValidationResult:
    """Navigate to the form URL and run all pre-flight checks."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=30_000)
    except Exception as exc:
        return ValidationResult(ok=False, error_type="network",
                                error_message=f"Could not load form: {exc}")

    page_text = (await page.inner_text("body")).lower()

    # 1. Closed?
    for pattern in _CLOSED_PATTERNS:
        if pattern in page_text:
            return ValidationResult(
                ok=False,
                error_type="closed",
                error_message="This form is no longer accepting responses.",
            )

    # 2. Org/GSuite account required?
    for pattern in _ORG_PATTERNS:
        if pattern in page_text:
            # Try to detect the required domain
            domain_match = re.search(r"@([\w.-]+\.\w+)", page_text)
            domain_hint = f" (requires @{domain_match.group(1)})" if domain_match else ""
            return ValidationResult(
                ok=False,
                error_type="org_required",
                error_message=(
                    f"This form requires a Google Workspace / organisational account{domain_hint}. "
                    "Your personal Gmail may not be accepted."
                ),
            )

    # 3. Already submitted?
    for pattern in _ALREADY_SUBMITTED_PATTERNS:
        if pattern in page_text:
            return ValidationResult(
                ok=False,
                error_type="already_submitted",
                error_message="You have already submitted a response to this form.",
            )

    # 4. File-upload questions? (non-fatal – we skip those questions)
    has_file_upload = bool(await page.locator('input[type="file"]').count())

    warnings: list[str] = []
    if has_file_upload:
        warnings.append(
            "This form has file-upload question(s). "
            "Those questions will be skipped – please submit files manually."
        )

    return ValidationResult(ok=True, warnings=warnings, has_file_upload=has_file_upload)
