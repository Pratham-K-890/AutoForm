"""Personal-information question detector.

Strategy:
  1. Fast keyword scan (no API calls)
  2. Any question that matches a keyword category is flagged as personal.
  3. The category name is stored so the frontend can show an appropriate label.
"""

from typing import Optional

# ── keyword registry ──────────────────────────────────────────────────────────

_CATEGORIES: dict[str, list[str]] = {
    "name": [
        "your name", "full name", "first name", "last name", "surname",
        "student name", "participant name", "name of",
    ],
    "email": [
        "email", "e-mail", "mail id", "email address", "your mail",
    ],
    "phone": [
        "phone", "mobile", "cell", "contact number", "whatsapp",
        "telephone", "phone number",
    ],
    "id": [
        "usn", "roll number", "roll no", "student id", "reg no",
        "registration number", "employee id", "admission number",
        "enrollment number", "hall ticket", "application number",
    ],
    "institution": [
        "college", "university", "institution", "school name", "institute",
    ],
    "department": [
        "department", "dept", "branch", "stream", "discipline", "major",
        "specialization", "course",
    ],
    "year": [
        "year of study", "current year", "academic year", "study year",
        "semester", "year (", "year:",
    ],
    "dob": [
        "date of birth", "dob", "birthday", "birth date",
    ],
    "age": ["your age", "age (", "age:"],
    "gender": ["gender", "your sex", "biological sex"],
    "address": [
        "address", "postal address", "your location", "city", "state",
        "country", "pin code", "zip code", "district",
    ],
}


def detect_personal_questions(questions: list[dict]) -> list[dict]:
    """Return the subset of questions that contain personal-info fields.

    Each returned question dict has an extra key ``personal_category``
    set to the matched category name.
    """
    flagged: list[dict] = []

    for q in questions:
        text_lower = q["text"].lower()
        matched_category: Optional[str] = None

        for category, keywords in _CATEGORIES.items():
            if any(kw in text_lower for kw in keywords):
                matched_category = category
                break

        if matched_category:
            q_copy = dict(q)
            q_copy["personal_category"] = matched_category
            flagged.append(q_copy)

    return flagged


def split_questions(
    questions: list[dict],
    personal_ids: set[str],
) -> tuple[list[dict], list[dict]]:
    """Split the full question list into (personal, non_personal) lists."""
    personal = [q for q in questions if q["id"] in personal_ids]
    non_personal = [q for q in questions if q["id"] not in personal_ids]
    return personal, non_personal
