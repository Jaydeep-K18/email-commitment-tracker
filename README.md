# Email Commitment Tracker

Finds the deadlines buried in your email and puts them on your calendar — using an AI model
that runs entirely on your own machine.

You agree to things in email and then forget them. Not the meetings, which get invites, but
the sentences: *"could you send me the Q3 report by Friday?"* Those live in a thread you
stop scrolling, and the only record is a message you have already read.

This reads mail from senders you care about, extracts the commitments, and publishes them
as calendar events — to Google Calendar, or to any calendar app that reads `.ics`.

```
"Could you send me the Q3 report by Friday 5pm?"
                    │
                    ▼
        ┌───────────────────────┐
        │  local model (Ollama) │   ← never leaves your machine
        └───────────────────────┘
                    │
                    ▼
   Send Q3 report · Fri 15 Aug, 17:00 · from Priya Nair
```

<!--
  SCREENSHOT SLOT — drop a dashboard PNG into .github/screenshots/ and
  uncomment the line below:
  ![The dashboard](.github/screenshots/dashboard.png)
-->

## Your email is never sent to an AI service

This is the whole reason the project is built the way it is.

Extraction runs against [Ollama](https://ollama.com) on `localhost`. No email content
reaches OpenAI, Anthropic, Google, or any other API — the only thing that ever leaves the
machine is the finished commitment (a title, a date, the sentence it came from) and only
if you connect a calendar you asked it to write to.

That constraint is what makes the project interesting to build. A 3B model running on a CPU
is not a frontier model, and most of the engineering below exists to make an unreliable
extractor produce trustworthy output.

## How it works

Five layers, each one deciding how much of its input deserves to reach the next:

| Layer | What it does | Where |
|---|---|---|
| **Collect** | Fetches mail over IMAP, or the Gmail API | [`src/collection/`](src/collection/) |
| **Filter** | Sorts senders into CRITICAL / IMPORTANT / MONITOR / SKIP | [`src/filtering/vip_filter.py`](src/filtering/vip_filter.py) |
| **Extract** | Prompts the local model, validates and grounds what comes back | [`src/extraction/pipeline.py`](src/extraction/pipeline.py) |
| **Decide** | Applies tier policy: what actually belongs on a calendar | [`src/sync/sync_engine.py`](src/sync/sync_engine.py) |
| **Publish** | Writes `.ics`, pushes to Google Calendar, serves a feed | [`src/sync/`](src/sync/), [`src/server/`](src/server/) |

The filter is a cost control, not a nicety: unknown senders default to `SKIP` and **never
reach the model at all**. On the author's mailbox that meant 6 of 120 emails were worth
running inference on.

Around those sit a [Streamlit dashboard](dashboard/) with a review queue and a commitment
graph, an [APScheduler loop](src/collection/scheduler.py) that runs the cycle unattended, a
[system-tray desktop app](desktop/), and a [Gmail side panel](extension/) that offers
*Add to calendar* on the message you are reading.

## The parts that fought back

Most of the interesting work was not the happy path.

**Google Calendar cannot subscribe to a `127.0.0.1` feed.** The app serves a perfectly good
`.ics` at `http://127.0.0.1:8765/calendar.ics`, and Outlook, Apple Calendar and Thunderbird
all consume it happily. Google does not: it fetches subscription URLs *from Google's
servers*, which have no route to a loopback address on your laptop. No amount of leaving the
app running fixes it. That dead end is why the project grew an OAuth flow and writes events
through the Calendar API — the only route that reaches a phone.

**The model cannot do date arithmetic.** Asked to resolve "by Friday" it produced confident,
wrong dates. Rather than fight it, the prompt now injects a lookup table of the next seven
days with their weekday names, turning arithmetic into reading. That fixed every date error
in the evaluation set.

**The model quoted its own prompt back as evidence.** Every commitment carries an
`evidence_quote` — the sentence it came from — so a user can see *why* something was
captured. The model was caught copying a sentence out of the prompt's own few-shot example
and attaching it to an unrelated newsletter. So the pipeline now verifies that the quote
genuinely occurs in the email body, degrading in stages (exact → punctuation-insensitive →
80% word overlap) because strict matching threw away real commitments over a trailing full
stop. This check is load-bearing, not cosmetic.

**Politeness looks exactly like a commitment.** "Let me know if you have any questions" was
being extracted as a pending question. Instructing the model not to do this did not work; a
code-level denylist did.

**Timezone-less events are valid iCalendar and invalid Google.** Timed events were sent as
naive local times, which the `.ics` spec accepts as floating time. Google rejects them
outright. The `.ics` path had worked for weeks, so the bug only surfaced on the first real
API sync — and the unit test covering it had asserted the *absence* of a timezone, encoding
the bug rather than catching it.

**`127.0.0.1` is not a security boundary.** The Gmail extension talks to a local API, and it
is tempting to treat loopback as private. It is not — any page your browser has open can
issue `fetch("http://127.0.0.1:8765/...")` in the background. Every `/api/*` route therefore
requires a token compared with `compare_digest`. The `.ics` feed stays open deliberately,
because a calendar app subscribing to a URL cannot send a custom header.

## Running it

Requires **Python 3.12** and **[Ollama](https://ollama.com/download)**.

```bash
git clone https://github.com/Jaydeep72/email-commitment-tracker.git
cd email-commitment-tracker
python -m venv .venv && .venv/Scripts/activate     # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
ollama pull llama3.2
python -m streamlit run dashboard/app.py
```

The dashboard opens on first-run setup and walks through four steps: install the model,
sign in, connect a mailbox, and choose where events should go. Mail is read **read-only**,
and the mailbox password is stored in your OS keyring — never in a file.

- Connecting Google Calendar: [`docs/google-setup.md`](docs/google-setup.md)
- Installing the Gmail panel: [`docs/gmail-panel.md`](docs/gmail-panel.md)

## A note on accuracy

The project does not currently ship a measured accuracy figure, and this README will not
invent one. What exists are four guards — evidence grounding, a boilerplate denylist, a
confidence floor, and a corrective retry on validation failure — each added in response to
an observed failure rather than in anticipation of one.

Worth stating plainly: the model self-reports confidence between 0.90 and 1.00 on
essentially everything it produces, so that number is not evidence of correctness. Building
a labelled evaluation set to replace anecdote with precision and recall is the most valuable
work left.

## Built with

Python 3.12 · SQLAlchemy · Pydantic · Ollama · FastAPI · Streamlit · APScheduler ·
NetworkX + pyvis · PyInstaller · Google Calendar & Gmail APIs · Chrome Manifest V3

65 source files, **447 tests**. Development ran in ten phases against a written plan, and
the commit messages carry the reasoning behind most of the decisions above.

## Licence

MIT — see [LICENSE](LICENSE).
