"""Dynamic Google Form scraper using Playwright.

Extracts all visible questions including conditional ones that only appear
after earlier questions are answered. Returns a list of question dicts.

Question dict schema:
  {
    "id"      : "q_0",        # sequential string key
    "text"    : "...",        # question label (required marker stripped)
    "type"    : "mcq" | "checkbox" | "dropdown" | "short_text" |
                "paragraph" | "date" | "time" | "linear_scale" | "file_upload",
    "options" : [...],        # list of option strings (empty for text types)
    "required": True | False,
    "entry_id": None,         # not needed for Playwright-based filling
  }
"""

import asyncio
import re
from typing import Optional

from playwright.async_api import Page

# ── selectors ─────────────────────────────────────────────────────────────────
# Google Forms uses Material Design components whose class names change
# between form versions. We try a ranked list and use the first that works.

_QUESTION_CONTAINERS = [
    "div.freebirdFormviewerViewItemsItemItem",
    "div[data-item-id]",
    "div.Qr7Oae",
]

_QUESTION_TITLE = [
    "span.M7eMe",
    ".freebirdFormviewerViewItemsItemItemTitle span",
    "[role='heading'] span",
    "div.freebirdFormviewerViewItemsItemItemTitle",
]

_REQUIRED_MARKER = [
    "span.vnumgf",
    ".freebirdFormviewerViewItemsItemItemRequiredAsterisk",
]


async def _page_text_of(page: Page, selector: str, parent=None) -> Optional[str]:
    root = parent if parent else page
    try:
        el = root.locator(selector).first
        if await el.count() == 0:
            return None
        return (await el.inner_text()).strip()
    except Exception:
        return None


async def _detect_form_meta(page: Page) -> tuple[str, str]:
    """Return (form_title, form_type)."""
    title = ""
    for sel in ["div.freebirdFormviewerViewHeaderHeader h1",
                ".freebirdFormviewerViewHeaderTitle",
                "h1"]:
        t = await _page_text_of(page, sel)
        if t:
            title = t.strip()
            break

    title_lower = title.lower()
    if any(w in title_lower for w in ["quiz", "test", "exam", "assessment"]):
        form_type = "quiz"
    elif any(w in title_lower for w in ["feedback", "review", "rating", "evaluation"]):
        form_type = "feedback"
    else:
        form_type = "survey"

    return title, form_type


async def _get_question_containers(page: Page):
    for sel in _QUESTION_CONTAINERS:
        locator = page.locator(sel)
        count = await locator.count()
        if count > 0:
            return locator, count
    return None, 0


async def _classify_question(container) -> tuple[str, list[str]]:
    """Return (type_string, options_list)."""
    # File upload
    if await container.locator('input[type="file"]').count() > 0:
        return "file_upload", []

    # Textarea → paragraph
    if await container.locator("textarea").count() > 0:
        return "paragraph", []

    # Radio group
    radio_count = await container.locator('[role="radio"], input[type="radio"]').count()
    if radio_count > 0:
        options = await _collect_option_texts(
            container, '[role="radio"], input[type="radio"]'
        )
        # Linear scale: options are numbers/very short labels in a row
        if options and all(re.match(r"^\d+$", o.strip()) for o in options):
            return "linear_scale", options
        return "mcq", options

    # Checkbox group
    checkbox_count = await container.locator('[role="checkbox"], input[type="checkbox"]').count()
    if checkbox_count > 0:
        options = await _collect_option_texts(
            container, '[role="checkbox"], input[type="checkbox"]'
        )
        return "checkbox", options

    # Native <select> (rare but present in some forms)
    if await container.locator("select").count() > 0:
        options = []
        opt_els = container.locator("select option")
        for i in range(await opt_els.count()):
            text = (await opt_els.nth(i).inner_text()).strip()
            if text and text not in ("", "Choose"):
                options.append(text)
        return "dropdown", options

    # Custom dropdown (Material listbox / quantumWizMenuPaperselectEl)
    dropdown_triggers = container.locator(
        '.quantumWizMenuPaperselectEl, [role="listbox"], [jsname="VZaVx"]'
    )
    if await dropdown_triggers.count() > 0:
        # _extract_dropdown_options needs the page; will be patched at call site
        return "dropdown", []  # options extracted during scrape_form pass

    # Date input
    date_inputs = container.locator('input[type="date"], .freebirdFormviewerViewItemsDateTextInputs')
    if await date_inputs.count() > 0:
        return "date", []

    # Time input
    if await container.locator('input[type="time"]').count() > 0:
        return "time", []

    # Default: short text
    if await container.locator('input[type="text"], input[jsname="YPqjbf"], .whsOnd').count() > 0:
        return "short_text", []

    return "short_text", []


async def _collect_option_texts(container, selector: str) -> list[str]:
    """Get visible label text for each radio/checkbox option."""
    options: list[str] = []
    items = container.locator(selector)
    count = await items.count()
    for i in range(count):
        item = items.nth(i)
        # Try the parent label / aria-label
        label = await item.get_attribute("aria-label") or ""
        if not label:
            # Walk to nearest sibling span / label
            parent = item.locator("xpath=../..")
            label = await _page_text_of(None, "span, label, div.ulDsOb", parent=parent) or ""
        if label.strip():
            options.append(label.strip())
    return options


async def _extract_dropdown_options(page: Page, container) -> list[str]:
    """Click the dropdown trigger, collect all options, then close."""
    options: list[str] = []
    trigger = container.locator(
        '.quantumWizMenuPaperselectEl, [jsname="VZaVx"], div[role="combobox"]'
    ).first
    try:
        await trigger.click()
        await asyncio.sleep(0.5)
        # Options are appended to the document body, not inside the container
        opt_locator = page.locator('[role="option"], .quantumWizMenuPaperselectOption')
        count = await opt_locator.count()
        for i in range(count):
            text = (await opt_locator.nth(i).inner_text()).strip()
            if text:
                options.append(text)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass
    return options


async def _get_question_title(container) -> tuple[str, bool]:
    """Return (question_text, is_required)."""
    title = ""
    for sel in _QUESTION_TITLE:
        t = await _page_text_of(None, sel, parent=container)
        if t:
            title = t
            break

    # Check required marker
    required = False
    for sel in _REQUIRED_MARKER:
        if await container.locator(sel).count() > 0:
            required = True
            break
    # Google sometimes puts * in the title text itself
    if title.endswith("*"):
        required = True
        title = title[:-1].strip()

    return title.strip(), required


# ── public API ────────────────────────────────────────────────────────────────

async def scrape_form(page: Page) -> tuple[str, str, list[dict]]:
    """
    Scrape all questions from the currently-loaded form page.

    Returns:
        (form_title, form_type, questions)
    """
    form_title, form_type = await _detect_form_meta(page)

    questions: list[dict] = []
    seen_texts: set[str] = set()

    MAX_PAGES = 50  # safety guard against infinite navigation loops

    # Handle multi-page forms: iterate pages via the Next button
    page_num = 0
    while page_num < MAX_PAGES:
        await asyncio.sleep(0.5)  # let conditional questions render
        containers_locator, count = await _get_question_containers(page)
        if count == 0:
            break

        for i in range(count):
            container = containers_locator.nth(i)
            title, required = await _get_question_title(container)
            if not title or title in seen_texts:
                continue
            seen_texts.add(title)

            q_type, options = await _classify_question(container)
            # For custom dropdowns the options list is empty until we expand it
            if q_type == "dropdown" and not options:
                options = await _extract_dropdown_options(page, container)
            # data-item-id is the numeric suffix used in the formResponse POST body
            # (field name becomes "entry.{data_item_id}")
            data_item_id = await containers_locator.nth(i).get_attribute("data-item-id")
            if not data_item_id:
                # Fallback: try to find it from FB_PUBLIC_LOAD_DATA_ by index
                data_item_id = None

            q_id = f"q_{len(questions)}"
            questions.append({
                "id": q_id,
                "text": title,
                "type": q_type,
                "options": options,
                "required": required,
                "entry_id": data_item_id,
                "page": page_num,
            })

        # Check for a "Next" / section button
        next_btn = page.locator(
            "div.freebirdFormviewerViewNavigationButtons "
            "div.freebirdFormviewerViewNavigationNextButton, "
            "div[jsname='OCpkoe'] div[jsname='P1ekSe']"
        )
        if await next_btn.count() > 0:
            await next_btn.first.click()
            page_num += 1
            await page.wait_for_load_state("networkidle", timeout=10_000)
        else:
            break

    return form_title, form_type, questions
