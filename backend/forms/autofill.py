"""Playwright-based form auto-filler.

Uses stealth mode to avoid Google bot detection.
Fills each question with the answer chosen by the LLM council (or the user
for personal-info questions), then submits the form and verifies success.
"""

import asyncio
import random
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Page, BrowserContext

from ..config import settings

# Browser session profiles are stored here, one subdirectory per user_id.
# Using a persistent context means Google session cookies survive between runs.
_SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "browser_sessions"


# ── Google session injection ──────────────────────────────────────────────────

async def _inject_google_session(context: BrowserContext, credentials) -> None:
    """Ensure the OAuth access token is fresh.

    The actual browser sign-in is handled via the persistent context profile
    (one-time manual setup via /api/google/setup-session).  This function only
    refreshes the access token so it stays valid for other API calls.
    """
    try:
        from google.auth.transport.requests import Request as GRequest
        if credentials.refresh_token:
            await asyncio.to_thread(credentials.refresh, GRequest())
    except Exception:
        pass


# ── human-like delay helper ───────────────────────────────────────────────────

async def _human_delay(min_ms: int = 500, max_ms: int = 1000) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


# ── question fillers ──────────────────────────────────────────────────────────

async def _js_set_value(locator, text: str) -> None:
    """Set value via JS, bypassing Playwright's enabled/editable checks."""
    await locator.evaluate(
        """(el, v) => {
            el.removeAttribute('disabled');
            el.setAttribute('aria-disabled', 'false');
            el.value = v;
            ['input','change','keyup'].forEach(t =>
                el.dispatchEvent(new Event(t, {bubbles: true}))
            );
        }""",
        str(text),
    )


async def _fill_short_text(page: Page, container, text: str) -> None:
    inp = container.locator(
        'input[type="text"], input[jsname="YPqjbf"], .whsOnd.zHQkBf'
    ).first
    await _js_set_value(inp, text)
    await _human_delay(300, 600)


async def _fill_paragraph(page: Page, container, text: str) -> None:
    ta = container.locator("textarea").first
    await _js_set_value(ta, text)
    await _human_delay(300, 700)


async def _is_disabled(element) -> bool:
    return (await element.get_attribute("disabled") is not None or
            (await element.get_attribute("aria-disabled") or "").lower() == "true")


async def _fill_mcq(page: Page, container, answer: str) -> None:
    """Click the radio option whose label matches `answer`."""
    radios = container.locator('[role="radio"], input[type="radio"]')
    count = await radios.count()
    first_enabled = None
    for i in range(count):
        radio = radios.nth(i)
        if first_enabled is None:
            first_enabled = radio
        label = (await radio.get_attribute("aria-label") or "").strip()
        if not label:
            parent = radio.locator("xpath=../..")
            label = (await parent.inner_text()).strip()
        if label.lower() == answer.strip().lower():
            await radio.click(force=True)
            await _human_delay()
            return
    # Fallback: click the first option
    if first_enabled is not None:
        await first_enabled.click(force=True)
        await _human_delay()


async def _fill_checkbox(page: Page, container, answers: list[str]) -> None:
    """Check the boxes whose labels are in `answers`."""
    checkboxes = container.locator('[role="checkbox"], input[type="checkbox"]')
    count = await checkboxes.count()
    answer_lower = {a.strip().lower() for a in (answers if isinstance(answers, list) else [answers])}
    for i in range(count):
        cb = checkboxes.nth(i)
        label = (await cb.get_attribute("aria-label") or "").strip()
        if not label:
            parent = cb.locator("xpath=../..")
            label = (await parent.inner_text()).strip()
        if label.lower() in answer_lower:
            await cb.click(force=True)
            await _human_delay(200, 500)


async def _fill_dropdown(page: Page, container, answer: str) -> None:
    """Handle both native <select> and Google's custom Material dropdown."""
    # Native select
    native = container.locator("select")
    if await native.count() > 0:
        await native.select_option(label=answer)
        await _human_delay()
        return

    # Custom Material dropdown
    trigger = container.locator(
        '.quantumWizMenuPaperselectEl, [jsname="VZaVx"], div[role="combobox"]'
    ).first
    await trigger.click()
    await asyncio.sleep(0.5)

    # Always use the iteration approach — avoids CSS selector injection
    # if the answer string contains special characters like quotes.
    all_opts = page.locator('[role="option"], .quantumWizMenuPaperselectOption')
    c = await all_opts.count()
    matched = False
    for i in range(c):
        t = (await all_opts.nth(i).inner_text()).strip()
        if t.lower() == answer.strip().lower():
            await all_opts.nth(i).click()
            await _human_delay()
            matched = True
            break
    if not matched:
        await page.keyboard.press("Escape")


async def _fill_date(page: Page, container, answer: str) -> None:
    """Fill a date field. `answer` should be YYYY-MM-DD."""
    # Try native date input
    date_inp = container.locator('input[type="date"]')
    if await date_inp.count() > 0:
        await date_inp.fill(answer)
        await _human_delay()
        return
    # Google's multi-part date inputs (MM / DD / YYYY)
    parts = answer.split("-")  # [YYYY, MM, DD]
    if len(parts) == 3:
        yyyy, mm, dd = parts
        inputs = container.locator("input")
        count = await inputs.count()
        if count >= 3:
            await inputs.nth(0).fill(mm)
            await inputs.nth(1).fill(dd)
            await inputs.nth(2).fill(yyyy)
            await _human_delay()


async def _fill_time(page: Page, container, answer: str) -> None:
    """Fill a time field. `answer` should be HH:MM (24h)."""
    time_inp = container.locator('input[type="time"]')
    if await time_inp.count() > 0:
        await time_inp.fill(answer)
        await _human_delay()
        return
    # Multi-part inputs
    parts = answer.split(":")
    if len(parts) >= 2:
        inputs = container.locator("input")
        if await inputs.count() >= 2:
            await inputs.nth(0).fill(parts[0])
            await inputs.nth(1).fill(parts[1])
            await _human_delay()


async def _fill_linear_scale(page: Page, container, answer: str) -> None:
    """Click the radio that matches the numeric answer."""
    await _fill_mcq(page, container, answer)


# ── dispatcher ────────────────────────────────────────────────────────────────

async def _fill_question(page: Page, container, question: dict, answer: Any) -> None:
    q_type = question["type"]
    if q_type == "file_upload":
        return  # skip – user must handle manually
    elif q_type == "short_text":
        await _fill_short_text(page, container, str(answer))
    elif q_type == "paragraph":
        await _fill_paragraph(page, container, str(answer))
    elif q_type == "mcq":
        await _fill_mcq(page, container, str(answer))
    elif q_type == "checkbox":
        await _fill_checkbox(page, container, answer)
    elif q_type == "dropdown":
        await _fill_dropdown(page, container, str(answer))
    elif q_type == "date":
        await _fill_date(page, container, str(answer))
    elif q_type == "time":
        await _fill_time(page, container, str(answer))
    elif q_type == "linear_scale":
        await _fill_linear_scale(page, container, str(answer))


# ── question-container locator ────────────────────────────────────────────────

async def _get_containers(page: Page):
    for sel in [
        "div.freebirdFormviewerViewItemsItemItem",
        "div[data-item-id]",
        "div.Qr7Oae",
    ]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            return loc
    return None


# ── persistent-context helper ─────────────────────────────────────────────────

def _user_data_dir(user_id: int | None) -> str | None:
    """Return the persistent browser profile directory for this user, or None."""
    if user_id is None:
        return None
    d = _SESSIONS_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ── one-time browser sign-in setup ───────────────────────────────────────────

async def setup_google_browser_session(user_id: int, google_email: str | None = None) -> dict:
    """Open a headed Chrome browser with the user's persistent profile so they
    can sign into Google manually.  Uses real Chrome (channel='chrome') so
    Google's sign-in page doesn't block it as 'unsecure browser'.
    Blocks until sign-in is detected (up to 3 min) or times out."""
    import re as _re

    udd = _user_data_dir(user_id)
    if not udd:
        return {"success": False, "message": "Could not create browser profile directory."}

    async with async_playwright() as pw:
        launch_kwargs = dict(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        # Use real Chrome if installed — avoids Google's "unsecure browser" block
        try:
            context = await pw.chromium.launch_persistent_context(
                udd, channel="chrome", **launch_kwargs
            )
        except Exception:
            # Fallback to bundled Chromium with stealth
            context = await pw.chromium.launch_persistent_context(udd, **launch_kwargs)

        page = await context.new_page()

        try:
            await page.goto(
                "https://accounts.google.com/signin/v2/identifier",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            if google_email:
                try:
                    await asyncio.sleep(1)
                    email_inp = page.locator('input[type="email"]')
                    if await email_inp.count() > 0:
                        await email_inp.fill(google_email)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(1.5)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            await page.wait_for_url(
                _re.compile(r"myaccount\.google\.com|accounts\.google\.com/b/\d"),
                timeout=180_000,
            )
            await asyncio.sleep(2)
            result = {"success": True, "message": "Google browser session set up successfully!"}
        except Exception:
            result = {"success": False, "message": "Sign-in timed out or was not completed."}
        finally:
            try:
                await context.close()
            except Exception:
                pass

        return result


# ── main public function ──────────────────────────────────────────────────────

async def fill_and_submit(
    form_url: str,
    questions: list[dict],
    answers: dict[str, Any],
    credentials=None,
    user_id: int | None = None,
) -> dict:
    """
    Open the form in a stealth Playwright browser, fill every question,
    and click Submit.

    Uses a persistent browser context per user so Google session cookies
    are preserved between runs (avoids needing to re-authenticate each time).

    Returns a dict:
      { "success": bool, "message": str }
    """
    udd = _user_data_dir(user_id)

    common_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]
    context_kwargs = dict(
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
            # Persistent context: cookies & localStorage survive between runs.
            # Try real Chrome first (avoids Google bot-detection on session reuse).
            try:
                context = await pw.chromium.launch_persistent_context(
                    udd,
                    channel="chrome",
                    headless=False,
                    args=common_args,
                    **context_kwargs,
                )
            except Exception:
                context = await pw.chromium.launch_persistent_context(
                    udd,
                    headless=False,
                    args=common_args,
                    **context_kwargs,
                )
            browser = None
        else:
            browser = await pw.chromium.launch(headless=False, args=common_args)
            context = await browser.new_context(**context_kwargs)

        # Inject / refresh Google session before opening the form page
        if credentials:
            await _inject_google_session(context, credentials)

        page = await context.new_page()

        try:
            await page.goto(form_url, wait_until="networkidle", timeout=30_000)
            await _human_delay(800, 1500)

            # Group questions by page number
            page_groups: dict[int, list[dict]] = {}
            for q in questions:
                pg = q.get("page", 0)
                page_groups.setdefault(pg, []).append(q)

            max_page = max(page_groups.keys()) if page_groups else 0

            for page_num in sorted(page_groups.keys()):
                page_qs = page_groups[page_num]
                containers_loc = await _get_containers(page)

                if containers_loc is None:
                    break

                container_count = await containers_loc.count()
                filled_titles: set[str] = set()

                for q in page_qs:
                    if q["id"] not in answers:
                        continue
                    answer = answers[q["id"]]
                    # Find the matching container by title text
                    for ci in range(container_count):
                        container = containers_loc.nth(ci)
                        title_text = ""
                        for sel in [
                            "span.M7eMe",
                            ".freebirdFormviewerViewItemsItemItemTitle span",
                            "div.freebirdFormviewerViewItemsItemItemTitle",
                        ]:
                            t = await container.locator(sel).first.inner_text() if await container.locator(sel).count() > 0 else ""
                            if t.strip():
                                title_text = t.strip().rstrip("*").strip()
                                break
                        if title_text == q["text"] and title_text not in filled_titles:
                            filled_titles.add(title_text)
                            await _fill_question(page, container, q, answer)
                            # Wait briefly for any conditional questions to appear
                            await asyncio.sleep(0.7)
                            break

                # Click "Next" if on a multi-page form
                next_btn = page.locator(
                    "div.freebirdFormviewerViewNavigationButtons "
                    "div.freebirdFormviewerViewNavigationNextButton"
                )
                if await next_btn.count() > 0 and page_num < max_page:
                    await next_btn.first.click()
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                    await _human_delay(800, 1200)

            # ── Submit ────────────────────────────────────────────────────────
            submit_btn = page.locator(
                "div.freebirdFormviewerViewNavigationSubmitButton, "
                "[jsname='M2UYVd']"
            )
            if await submit_btn.count() == 0:
                return {"success": False, "message": "Submit button not found"}

            await submit_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await _human_delay(1000, 2000)

            # ── Verify confirmation ───────────────────────────────────────────
            confirmation_phrases = [
                "your response has been recorded",
                "thanks for submitting",
                "form submitted",
                "thank you",
                "response recorded",
                "successfully submitted",
                "your response has been saved",
                "submitted",
                "recorded",
                "responses are not being accepted",  # closed but was open
            ]

            def _is_success(url: str, body: str) -> bool:
                # URL changes from /viewform to /formResponse on success
                if "formresponse" in url.lower():
                    return True
                return any(ph in body for ph in confirmation_phrases)

            page_text = (await page.inner_text("body")).lower()
            success = _is_success(page.url, page_text)

            if not success:
                # Retry once
                if await submit_btn.count() > 0:
                    await submit_btn.first.click()
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                    await _human_delay(1000, 2000)
                    page_text = (await page.inner_text("body")).lower()
                    success = _is_success(page.url, page_text)

            return {
                "success": success,
                "message": "Form submitted successfully!" if success
                           else "Submission may have failed – confirmation page not detected.",
            }

        except Exception as exc:
            return {"success": False, "message": f"Playwright error: {exc}"}
        finally:
            await page.close()
            await context.close()
            if browser:
                await browser.close()
