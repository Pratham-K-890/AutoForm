"""LLM Council – three models vote on each non-personal form question.

Models used:
  • Groq  → llama-3.3-70b-versatile  (voter 1 – primary + tiebreaker)
  • Groq  → llama-3.1-8b-instant     (voter 2 – fast secondary)
  • Gemini→ gemini-2.0-flash          (voter 3)

Note: mixtral-8x7b-32768 was removed from Groq in 2025 – replaced above.

Voting rules:
  • MCQ / dropdown  : majority vote; tie-break → llama-3.3-70b answer
  • Checkbox        : include an option if ≥2 models chose it
  • Text (short/long): pick the longest, most-detailed response
  • If a model times out or is rate-limited: use the remaining two
"""

import asyncio
import json
import re
from typing import Any

from groq import AsyncGroq
import google.generativeai as genai

from ..config import settings

# ── initialise clients lazily ─────────────────────────────────────────────────

_groq_client: AsyncGroq | None = None


def _groq() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    return _groq_client


def _gemini_model():
    genai.configure(api_key=settings.GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-2.0-flash")


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(question: dict, form_type: str, form_title: str) -> str:
    q_type = question["type"]
    options_block = ""
    if question.get("options"):
        opts = "\n".join(f"  - {o}" for o in question["options"])
        options_block = f"\nAvailable options:\n{opts}"

    q_text = question["text"] or "[The question is shown as an image attached to this message — analyze it carefully]"
    image_note = "\n[An image is attached. Read it carefully to determine the answer.]" if question.get("image_url") else ""

    return f"""You are accurately filling a Google Form on behalf of a user.

Form title : {form_title}
Form type  : {form_type}
Question   : {q_text}{image_note}
Input type : {q_type}{options_block}

Rules:
- For MCQ or dropdown  : pick EXACTLY ONE option from the list (copy it verbatim).
- For checkbox         : pick ONE OR MORE options from the list (as a JSON array).
- For short_text / paragraph: write a concise, appropriate answer (plain string).
- For date             : return ISO format YYYY-MM-DD.
- For time             : return HH:MM (24h).
- For linear_scale     : return a single number from the scale range.
- NEVER invent an option that is not in the list for MCQ/checkbox/dropdown.
- If no option is clearly correct, pick the most neutral/reasonable one.

Return ONLY valid JSON with this structure (no markdown fences):
{{"answer": <string or array>, "confidence": <0-1 float>, "reasoning": "<one sentence>"}}"""


# ── vision helpers ────────────────────────────────────────────────────────────

async def _fetch_image_bytes(url: str) -> tuple[bytes, str]:
    """Fetch image from URL, return (bytes, mime_type). Runs in a thread."""
    import urllib.request

    def _sync():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return r.read(), mime

    return await asyncio.to_thread(_sync)


async def _call_gemini_vision(prompt: str, image_bytes: bytes, mime_type: str) -> dict | None:
    """Call Gemini with an inline image."""
    try:
        import google.ai.generativelanguage as glm

        model = _gemini_model()
        full_prompt = prompt + "\n\nIMPORTANT: Return ONLY the JSON object, no markdown."

        image_part = glm.Part(
            inline_data=glm.Blob(mime_type=mime_type, data=image_bytes)
        )
        resp = await asyncio.wait_for(
            model.generate_content_async([full_prompt, image_part]),
            timeout=30,
        )
        text = resp.text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


# ── individual model callers ──────────────────────────────────────────────────

async def _call_groq(model_id: str, prompt: str) -> dict | None:
    try:
        resp = await asyncio.wait_for(
            _groq().chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=512,
            ),
            timeout=30,
        )
        raw = resp.choices[0].message.content
        return json.loads(raw)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


async def _call_gemini(prompt: str) -> dict | None:
    try:
        model = _gemini_model()
        full_prompt = prompt + "\n\nIMPORTANT: Return ONLY the JSON object, no markdown."
        resp = await asyncio.wait_for(
            model.generate_content_async(full_prompt),
            timeout=30,
        )
        text = resp.text.strip()
        # Strip any accidental markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


# ── vote tallying ─────────────────────────────────────────────────────────────

def _tally(responses: list[dict | None], question: dict) -> dict:
    """Aggregate responses from up to 3 models into a single final answer."""
    valid = [r for r in responses if r and "answer" in r]
    if not valid:
        # All models failed – return empty string so the form can still be attempted
        return {"answer": "", "confidence": 0.0, "models_agreed": 0}

    q_type = question["type"]
    options = question.get("options", [])

    if q_type in ("mcq", "dropdown", "linear_scale"):
        # Majority vote
        from collections import Counter
        votes = Counter()
        for r in valid:
            ans = str(r["answer"]).strip()
            # Validate that the answer is one of the declared options
            if options:
                match = next((o for o in options if o.lower() == ans.lower()), None)
                ans = match if match else (options[0] if options else ans)
            votes[ans] += 1
        winner, count = votes.most_common(1)[0]
        return {"answer": winner, "confidence": count / len(valid), "models_agreed": count}

    elif q_type == "checkbox":
        # Include option if majority (≥ ceil(n/2)) of valid models chose it
        from math import ceil
        threshold = ceil(len(valid) / 2)
        from collections import Counter
        option_votes: Counter = Counter()
        for r in valid:
            chosen = r["answer"]
            if isinstance(chosen, str):
                chosen = [chosen]
            for item in chosen:
                item = item.strip()
                match = next((o for o in options if o.lower() == item.lower()), item)
                option_votes[match] += 1
        final = [opt for opt, cnt in option_votes.items() if cnt >= threshold]
        if not final and options:
            final = [options[0]]
        return {"answer": final, "confidence": max(option_votes.values(), default=0) / len(valid),
                "models_agreed": len(valid)}

    else:
        # Text: pick the longest response from valid answers
        best = max(valid, key=lambda r: len(str(r.get("answer", ""))))
        return {"answer": best["answer"],
                "confidence": best.get("confidence", 0.8),
                "models_agreed": len(valid)}


# ── public API ────────────────────────────────────────────────────────────────

async def run_council(
    questions: list[dict],
    form_type: str,
    form_title: str,
) -> dict[str, dict]:
    """
    Run the LLM council on a list of questions.

    Returns a dict keyed by question id:
      { "q_0": {"answer": ..., "confidence": ..., "models_agreed": ...,
                 "model_responses": [...]}, ... }
    """
    results: dict[str, dict] = {}

    for q in questions:
        q_id = q["id"]
        prompt = _build_prompt(q, form_type, form_title)

        if q.get("image_url"):
            # Vision question — only Gemini supports images; LLaMA models are text-only
            try:
                image_bytes, mime_type = await _fetch_image_bytes(q["image_url"])
                gemini_resp = await _call_gemini_vision(prompt, image_bytes, mime_type)
            except Exception:
                gemini_resp = None

            tally = _tally([gemini_resp], q)
            tally["model_responses"] = {"gemini-2.0-flash-vision": gemini_resp}

        else:
            # Text question — fire all three models concurrently
            llama_task   = asyncio.create_task(_call_groq("llama-3.3-70b-versatile", prompt))
            llama8b_task = asyncio.create_task(_call_groq("llama-3.1-8b-instant", prompt))
            gemini_task  = asyncio.create_task(_call_gemini(prompt))

            responses = await asyncio.gather(llama_task, llama8b_task, gemini_task,
                                             return_exceptions=False)
            llama_resp, llama8b_resp, gemini_resp = responses

            tally = _tally([llama_resp, llama8b_resp, gemini_resp], q)

            if tally["models_agreed"] == 1 and llama_resp and "answer" in llama_resp:
                tally["answer"] = llama_resp["answer"]
                tally["tiebreak"] = True

            # Validate MCQ / dropdown answer is in options
            if q.get("options") and q["type"] in ("mcq", "dropdown"):
                answer = tally["answer"]
                if not any(o.lower() == str(answer).lower() for o in q["options"]):
                    retry_prompt = prompt + f'\n\nYou MUST pick one of: {q["options"]}'
                    retry = await _call_groq("llama-3.3-70b-versatile", retry_prompt)
                    if retry and retry.get("answer") in q["options"]:
                        tally["answer"] = retry["answer"]

            tally["model_responses"] = {
                "llama-3.3-70b": llama_resp,
                "llama-3.1-8b":  llama8b_resp,
                "gemini-2.0-flash": gemini_resp,
            }

        results[q_id] = tally
        await asyncio.sleep(0.3)

    return results
