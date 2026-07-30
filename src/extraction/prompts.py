"""Extraction prompt templates.

The model is used for *structured extraction*, not generation: it fills a fixed
schema from explicitly stated facts in one email. The prompts below are written
for a small (3B) local model, so they favour short, concrete rules and one
worked example over lengthy prose.

Two failure modes dominate with small models, and the prompt targets both:

1. **Inventing commitments** in emails that contain none — countered by an
   explicit "return an empty list" instruction and a no-commitment example.
2. **Paraphrasing the evidence** — countered by demanding a verbatim quote,
   which the pipeline then verifies against the email text.
"""
from __future__ import annotations

from datetime import datetime, timedelta

SYSTEM_PROMPT = """\
You extract commitments and deadlines from a single email. You output JSON only.

You are given one email that was sent TO the user. Find every commitment that is
EXPLICITLY stated in it. Do not guess, infer, or invent anything.

The four commitment types:

- "deadline_on_you": the USER must do something, usually by a date.
  Example: "Please submit the report by 15 August."
- "deadline_from_others": the SENDER promised to do something for the user.
  Example: "I'll send you the design files tomorrow."
- "question_pending": the sender asked the user a direct question that needs a
  reply. Example: "Can you confirm whether Tuesday works?"
- "meeting": a specific meeting time is proposed or confirmed.
  Example: "Let's meet Monday at 3pm."

Rules:
1. Only extract things stated in the email text. If the email has no commitment,
   return {"commitments": []}. Most emails have none — that is normal and correct.
2. "evidence_quote" MUST be copied word-for-word from the email body. Never
   paraphrase, shorten, or write your own sentence.
3. "deadline" must be ISO 8601: "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS".
   Use the email's date to resolve relative wording like "Friday" or "tomorrow".
   If no date is stated, use "".
4. "subject" is a short plain description of the task, in your own words.
5. "confidence" is a number from 0.0 to 1.0. If you are not confident something
   is a real commitment, do not include it at all.
6. Ignore newsletters, marketing, notifications, and automated mail — they
   contain no personal commitments.
7. The following are NEVER commitments. Do not extract them:
   - Polite closings: "Let me know if you have questions", "Feel free to reach
     out", "Hope this helps", "Thanks", "Best regards".
   - Text saying no action is needed: "No action needed", "FYI", "Just letting
     you know", "for your reference".
   - Marketing calls to action: "Click here", "Subscribe now", "Learn more".
   A real question_pending asks about something specific the sender needs an
   answer to — not a generic offer to help.
"""

# One positive and one negative example. The negative example matters most: it
# teaches the model that "no commitments" is an acceptable answer.
FEW_SHOT = """\
Example email:
---
From: Dr. Alice Chen <alice@university.edu>
Date: Tuesday, 22 July 2025
Subject: Project report
Body:
Hi, please submit your final project report by 15th August. Also, can you tell
me whether you are attending the seminar? I will email the reading list to you
on Thursday.
---
Correct output:
{"commitments": [
  {"type": "deadline_on_you", "subject": "Submit final project report",
   "deadline": "2025-08-15", "counterparty": "Dr. Alice Chen",
   "direction": "outgoing",
   "evidence_quote": "please submit your final project report by 15th August",
   "confidence": 0.95},
  {"type": "question_pending", "subject": "Confirm seminar attendance",
   "deadline": "", "counterparty": "Dr. Alice Chen", "direction": "outgoing",
   "evidence_quote": "can you tell me whether you are attending the seminar",
   "confidence": 0.9},
  {"type": "deadline_from_others", "subject": "Alice sends the reading list",
   "deadline": "2025-07-24", "counterparty": "Dr. Alice Chen",
   "direction": "incoming",
   "evidence_quote": "I will email the reading list to you on Thursday",
   "confidence": 0.85}
]}

Example email with nothing to extract:
---
From: Tech Weekly <news@techweekly.com>
Date: Monday, 21 July 2025
Subject: Your weekly digest
Body:
Here are this week's top stories. Thanks for reading!
---
Correct output:
{"commitments": []}

Another email with nothing to extract (note the polite closing is NOT a question):
---
From: Sam Patel <sam@company.com>
Date: Monday, 21 July 2025
Subject: FYI
Body:
Just letting you know the office will be repainted this month. No action needed
from your side. Let me know if you have any questions.
---
Correct output:
{"commitments": []}
"""


def _format_received(received_at: datetime | None) -> str:
    if received_at is None:
        return "unknown"
    # Weekday name included so the model can resolve "Friday" correctly.
    return received_at.strftime("%A, %d %B %Y")


def build_date_reference(received_at: datetime | None) -> str:
    """Pre-compute upcoming dates so the model looks them up instead of counting.

    Small models are unreliable at date arithmetic — during evaluation the model
    resolved "Friday" and "next Monday" to the wrong dates. Supplying an explicit
    table turns the calculation into a lookup, which it handles well.
    """
    if received_at is None:
        return ""

    lines = [f"  today ({received_at:%A}) = {received_at:%Y-%m-%d}"]
    for offset in range(1, 8):
        day = received_at + timedelta(days=offset)
        label = f"{day:%A}"
        if offset == 1:
            label = f"tomorrow ({label})"
        lines.append(f"  {label} = {day:%Y-%m-%d}")

    next_week = received_at + timedelta(days=7)
    end_of_month = _end_of_month(received_at)
    lines.append(f"  next week = week of {next_week:%Y-%m-%d}")
    lines.append(f"  end of this month = {end_of_month:%Y-%m-%d}")

    return (
        "Date reference — resolve relative dates using ONLY this table:\n"
        + "\n".join(lines)
    )


def _end_of_month(value: datetime) -> datetime:
    if value.month == 12:
        first_next = value.replace(year=value.year + 1, month=1, day=1)
    else:
        first_next = value.replace(month=value.month + 1, day=1)
    return first_next - timedelta(days=1)


def build_extraction_prompt(
    subject: str | None,
    sender_name: str | None,
    sender_email: str | None,
    body_text: str,
    received_at: datetime | None = None,
    user_email: str | None = None,
    max_body_chars: int = 6000,
) -> str:
    """Assemble the user-turn prompt for one email."""
    body = (body_text or "").strip()
    if len(body) > max_body_chars:
        body = body[:max_body_chars] + "\n[...truncated...]"

    sender = " ".join(filter(None, [sender_name, f"<{sender_email}>" if sender_email else None]))
    recipient = user_email or "the user"
    date_reference = build_date_reference(received_at)

    return f"""{FEW_SHOT}
Now extract from this real email.

The user (the recipient) is: {recipient}
---
From: {sender or "unknown"}
Date: {_format_received(received_at)}
Subject: {subject or "(no subject)"}
Body:
{body or "(empty body)"}
---

{date_reference}

Return JSON only, in this exact shape:
{{"commitments": [{{"type": ..., "subject": ..., "deadline": ..., "counterparty": ..., "direction": ..., "evidence_quote": ..., "confidence": ...}}]}}

If this email contains no commitment, deadline, question, or meeting, return:
{{"commitments": []}}
"""


def build_retry_prompt(previous_response: str, errors: list[str]) -> str:
    """Build the single corrective retry described in PROJECT_PLAN.md §12 step 3."""
    problems = "\n".join(f"- {error}" for error in errors[:5])
    truncated = (previous_response or "").strip()[:1500]
    return f"""Your previous response was rejected by the validator.

Your response was:
{truncated}

Problems found:
{problems}

Fix these problems and return corrected JSON only. Remember:
- "type" must be one of: deadline_on_you, deadline_from_others, question_pending, meeting
- "deadline" must be "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS", or "" if none is stated
- "confidence" must be a number between 0.0 and 1.0
- "evidence_quote" must be copied word-for-word from the email
- If there is genuinely nothing to extract, return {{"commitments": []}}
"""
