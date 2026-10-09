# Email Commitment Tracker

Finds the deadlines buried in your email and puts them on your calendar — using an AI model
that runs entirely on your own machine.

You agree to things in email and then forget them. Not the meetings, which get invites, but
the sentences: *"could you send me the Q3 report by Friday?"* Those live in a thread you
stop scrolling, and the only record is a message you have already read.

This reads mail from senders you care about, extracts the commitments, and publishes them
as calendar events — to Google Calendar, or to any calendar app that reads `.ics`.

**[Try the live demo →](https://jaydeep-k18.github.io/email-commitment-tracker/)** The whole
app, running on generated sample mail in your browser: nothing is sent anywhere, and there is
no account to create.

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

A full-stack app around a Python pipeline. PostgreSQL is the single source of truth; the
Express server is the only thing a browser talks to; the Python worker does the reading,
extracting and publishing as background jobs.

```
  React app ─────────────┐
  Gmail side panel ─/ext─┤        Express + Node (:4000)
  Calendar apps ─/calendar.ics─▶  sessions · REST API · WebSocket · notifications
                                    │                     │ relays /ext, the feed,
                                    ▼                     ▼ and setup steps
                               PostgreSQL ◀──────── Python worker
                     mail · commitments · jobs      collect → filter → extract →
                     the event log                  decide → publish
                                    ▲                     ▲
                                    └──── Redis ──────────┘
                                   job dispatch and delayed retries

  Optional: Kafka and Flink. The worker relays every committed event from
  Postgres to a topic; the server streams it to browsers, and a Flink job turns
  it into one-minute metrics for the System page.
```

| Part | What it does | Where |
|---|---|---|
| **React app** | Smart inbox, commitments, calendar, relationships, analytics, jobs, system health, settings | [`apps/web/`](apps/web/) |
| **Express server** | Owner sign-in, CSRF, the REST API, live updates over WebSocket, notifications | [`apps/server/`](apps/server/) |
| **Python worker** | The pipeline below, run as retried, idempotent background jobs | [`src/`](src/) |
| **Shared contracts** | Types, the tier policy and the schema both languages are tested against | [`packages/shared/`](packages/shared/) |

The worker's pipeline has five layers, each deciding how much of its input deserves to
reach the next, and a check over what ends up on the calendar:

| Layer | What it does | Where |
|---|---|---|
| **Collect** | Fetches mail over IMAP, or the Gmail API | [`src/collection/`](src/collection/) |
| **Filter** | Sorts senders into CRITICAL / IMPORTANT / MONITOR / SKIP | [`src/filtering/vip_filter.py`](src/filtering/vip_filter.py) |
| **Extract** | Prompts the local model, validates and grounds what comes back | [`src/extraction/pipeline.py`](src/extraction/pipeline.py) |
| **Decide** | Applies tier policy: what actually belongs on a calendar | [`src/sync/sync_engine.py`](src/sync/sync_engine.py) |
| **Publish** | Writes `.ics`, pushes to Google Calendar, serves a feed | [`src/sync/`](src/sync/), [`src/server/`](src/server/) |
| **Check** | Flags possible duplicates and clashes, including with events already on your Google Calendar, and finds free times to propose instead | [`src/sync/calendar_intel.py`](src/sync/calendar_intel.py) |

The filter is a cost control, not a nicety: unknown senders default to `SKIP` and **never
reach the model at all**. On the author's mailbox that meant 6 of 120 emails were worth
running inference on.

Every step is recorded in an event log, which is the activity timeline, the source of
notifications, and what the live dashboard streams. The log doubles as a transactional
outbox: an event is written in the same transaction as the change it describes, and a
relay publishes it to Kafka only after that commits, so the stream can never announce a
change the database rolled back, or miss one it kept. A [Gmail side panel](extension/)
offers *Add to calendar* on the message you are reading.

## The parts that fought back

Most of the interesting work was not the happy path.

**Google Calendar cannot subscribe to a `127.0.0.1` feed.** The app serves a perfectly good
`.ics` at `http://127.0.0.1:4000/calendar.ics`, and Outlook, Apple Calendar and Thunderbird
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

**An idle stream never closes a window.** Flink's event-time windows close when a later
event pushes the watermark past them. This stream is quiet most of the time, so the newest
minute would sit open until the next mail check, and an empty minute would never be written
at all — which reads exactly like Flink being down. The job therefore closes minutes by the
wall clock, fifteen seconds after they end, writes empty ones too, and rewrites a minute if
a straggler arrives later. The server's fallback computes the same metrics from the events
table when Flink is not running; both follow one set of rules, written in Python and
TypeScript and held together by a shared test file, so the page cannot tell them apart.

**`127.0.0.1` is not a security boundary.** The Gmail extension talks to a local API, and it
is tempting to treat loopback as private. It is not — any page your browser has open can
issue `fetch("http://127.0.0.1:4000/ext/...")` in the background. Every `/ext/*` route
therefore requires a token compared with `compare_digest`, and the app's own API needs a
session cookie plus a CSRF token. The `.ics` feed stays open deliberately, because a
calendar app subscribing to a URL cannot send a custom header.

## Running it

**Just looking?** Open the [live demo](https://jaydeep-k18.github.io/email-commitment-tracker/).
It is the web app's demo mode, which needs only Node to run yourself — no database, no
model, no mail. Generated sample data answers every request in the browser:

```bash
npm install
npm run demo
```

**The real thing** needs Python 3.12, Node 20, Docker and
[Ollama](https://ollama.com/download):

```bash
git clone https://github.com/Jaydeep-K18/email-commitment-tracker.git
cd email-commitment-tracker
cp .env.example .env               # then replace the change-me passwords
docker compose up -d               # PostgreSQL and Redis, on 127.0.0.1 only
python -m venv .venv && .venv/Scripts/activate     # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
npm install
ollama pull llama3.2
```

Then run the three processes, the worker first — it brings the database schema up to
date when it starts:

```bash
python -m src.jobs.worker          # the pipeline, as background jobs
npm run dev:server                 # the API on http://127.0.0.1:4000
npm run dev:web                    # the app on http://localhost:5173
```

To stream events through Kafka as well, start it with `docker compose --profile events up -d`
and set `KAFKA_BROKERS=127.0.0.1:9092` in `.env` before starting the worker and server.
Without it, live events come straight from Postgres.

For the Flink metrics, use `docker compose --profile streaming up -d --build` instead (Kafka
plus a Flink cluster that submits its own job), and also set `FLINK_URL=http://127.0.0.1:8081`
for the server. Flink's dashboard is then at that address. Without Flink, the System page
computes the same metrics from the database and says so.

The first visit creates your owner account, then walks through four steps: install the
model, sign in with Google (optional), connect a mailbox, and choose where events should
go. Mail is read **read-only**, and the mailbox password is stored in your OS keyring —
never in a file.

For one Node process instead of two, set `WEB_DIST=apps/web/dist` in `.env`, then
`npm run build` and `npm start`: the API server serves the built app itself.

- Connecting Google Calendar: [`docs/google-setup.md`](docs/google-setup.md)
- Installing the Gmail panel: [`docs/gmail-panel.md`](docs/gmail-panel.md)
- Coming from the SQLite version: `python -m scripts.migrate_sqlite_to_postgres`


## Built with

**Front end** React 18 · TypeScript · Vite · TanStack Query · React Router · Tailwind CSS ·
Recharts · Radix UI**Server** Node · Express · PostgreSQL · Redis · Kafka · WebSockets · Zod · argon2**Streaming** Apache Flink 1.20 (PyFlink)**Worker** Python 3.12 · SQLAlchemy · Alembic · Pydantic · FastAPI · Ollama · Google
Calendar & Gmail APIs**Extension** Chrome Manifest V3**Tests** pytest · Vitest · Testing Library · MSW · PGlite

About 900 tests across both languages — including the 400 tier-policy cases that the
Python and TypeScript copies of the policy must agree on, word for word. The commit
messages carry the reasoning behind most of the decisions above.

## Licence

MIT — see [LICENSE](LICENSE).
