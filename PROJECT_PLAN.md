# PROJECT_PLAN.md
## Email Commitment Tracker — A Private, Local AI System for Automatic Deadline Extraction and Calendar Sync

> **This is the single source of truth for the project.** Claude Code must read
> this file fully before writing any code, and refer back to it throughout
> development. It defines the vision, scope, architecture, and the exact phase
> order in which the project is built. Do not skip ahead in the roadmap. Build
> one phase at a time, test it, then proceed.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Problem Statement](#2-problem-statement)
3. [Objectives](#3-objectives)
4. [Target Users](#4-target-users)
5. [MVP Scope and Core Features](#5-mvp-scope-and-core-features)
6. [High-Level System Architecture](#6-high-level-system-architecture)
7. [AI/ML Architecture](#7-aiml-architecture)
8. [Complete Tech Stack (With Reasons)](#8-complete-tech-stack-with-reasons)
9. [Project Folder Structure](#9-project-folder-structure)
10. [Database Schema](#10-database-schema)
11. [API Endpoints](#11-api-endpoints)
12. [LLM Extraction Workflow](#12-llm-extraction-workflow)
13. [Development Roadmap (Phased)](#13-development-roadmap-phased)
14. [Testing Strategy](#14-testing-strategy)
15. [Deployment](#15-deployment)
16. [Security and Privacy](#16-security-and-privacy)
17. [Future Enhancements](#17-future-enhancements)

---

## 1. Project Overview

The Email Commitment Tracker is a privacy-first desktop application that reads a
user's email inbox in the background, uses a local Large Language Model (via
Ollama) to extract commitments and deadlines from the emails, and automatically
publishes the important ones to the user's calendar through a universal `.ics`
calendar subscription that works on every device and every calendar application.

The defining characteristic of this project is that **all sensitive processing
happens locally on the user's machine**. Email content is never sent to any cloud
LLM or third-party API. The AI that reads emails runs entirely on-device using a
quantized local model. Only non-sensitive structured output (a calendar event
title and date) is ever exposed, and even that is served locally via a standard
calendar file that the user's own calendar app subscribes to.

The system is built by a two-person team over approximately four weeks and is
intended as a technically substantial portfolio project demonstrating local LLM
inference, structured extraction, privacy-first architecture, background service
design, and cross-platform calendar integration.

---

## 2. Problem Statement

Professional and academic email is full of implicit commitments and deadlines
that are easy to miss:

- A professor writes "submit your final project report by 15th August."
- An internship manager writes "send me the status update by Friday."
- A teammate writes "I'll share the design files by tomorrow."
- A client writes "let's meet next Monday, please confirm by Saturday."

These commitments are buried inside email threads. They are not structured, not
centralized, and not connected to any calendar. The current ways people handle
this are all inadequate:

1. **Mental tracking** — deadlines are noted and then forgotten within a day.
2. **Manual calendar entry** — accurate but tedious; most people don't bother.
3. **Re-reading threads before deadlines** — error-prone and time-consuming.

Existing tools do not solve this well. Generic calendar tools only parse specific
sender formats and only for meeting invitations. Task managers require full manual
entry. And critically, no existing tool can process email content with an LLM
**without sending that email content to a third-party cloud service** — which is
unacceptable for professionally or personally sensitive email.

**The gap:** there is no private, automatic system that reads ordinary email,
understands the commitments inside it, and surfaces them on the user's calendar
without compromising the privacy of the email content.

---

## 3. Objectives

**Primary objectives (must achieve for MVP):**

1. Connect to any standard email inbox via IMAP and fetch new emails on a schedule.
2. Filter emails by a user-defined VIP contact list so only relevant senders are processed.
3. Extract commitments, deadlines, and questions from emails using a **local** LLM (Ollama), with zero email content leaving the device.
4. Store extracted commitments in a structured local database with full traceability back to the source email.
5. Publish deadline commitments to the user's calendar via a universal `.ics` subscription that works on Windows, Mac, Android, and iOS.
6. Provide a local dashboard where the user can view, manage, and control everything.

**Secondary objectives (desirable, post-MVP):**

7. Track commitments others made to the user (follow-up reminders).
8. Visualize the commitment network as a graph.
9. Package the application as a downloadable desktop installer.

**Non-objectives (explicitly out of scope):**

- Building a custom calendar application (calendar apps are a solved problem; the project integrates with existing ones).
- Native mobile applications (local LLM inference is not feasible on phones; mobile is served through the universal `.ics` subscription instead).
- Multi-user or cloud-hosted operation (this is a single-user, local-first tool).
- Sending emails or replying to them (the system reads and extracts only).

---

## 4. Target Users

**Primary user — students and early-career professionals:**
People who receive frequent deadline-bearing email from professors, managers,
teammates, and clients, who use multiple devices, and who care about not missing
commitments. They are comfortable installing a desktop application and doing a
one-time setup.

**Secondary user — privacy-conscious knowledge workers:**
People in roles (legal, healthcare, finance, research) where email content is
sensitive and cannot be processed by cloud AI services, but who still want
automated help managing commitments. The local-only architecture is specifically
valuable to this group.

---

## 5. MVP Scope and Core Features

The MVP is the smallest version that delivers the core value: **email in →
deadline automatically on your calendar, privately.**

### In scope for MVP

| Feature | Description |
|---|---|
| IMAP email fetching | Connect to inbox, fetch new emails on an hourly schedule |
| Email parsing | Extract clean plain text from HTML emails, handle threads |
| VIP contact filter | User-defined contact list with tiers controlling what gets processed |
| Local LLM extraction | Ollama-based extraction of commitments, deadlines, questions |
| Structured validation | Pydantic validation of all LLM output before storage |
| Local storage | SQLite database of commitments with source traceability |
| `.ics` calendar publishing | Universal calendar subscription served locally |
| Dashboard | Streamlit UI to view commitments, manage VIPs, control the system |

### Out of scope for MVP (deferred to later phases or future work)

- Commitment graph visualization (Phase 7, optional)
- Desktop installer packaging (Phase 8, optional)
- Tracking commitments others made to you (post-MVP enhancement)
- Auto-learning VIP tiers from user behavior (future enhancement)
- CalDAV direct write-back (future enhancement)

### The four commitment types the system extracts

1. **Deadline on you** — something the user must do by a date (goes to calendar).
2. **Deadline from others** — something someone promised the user (dashboard follow-up).
3. **Question pending** — a question awaiting the user's reply (dashboard, not calendar).
4. **Meeting request** — a proposed meeting time (goes to calendar as an event).

---

## 6. High-Level System Architecture

The system is composed of five logical layers, all running locally on the user's
machine, plus the user's own calendar application as the only external touchpoint.

```
┌──────────────────────────────────────────────────────────────────┐
│                        USER'S LAPTOP                              │
│                                                                   │
│  LAYER 1 — COLLECTION                                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Email Fetcher (IMAP)  →  Email Parser  →  Raw Email Store  │  │
│  │  runs on a schedule (hourly via APScheduler)                │  │
│  └────────────────────────────────────────────────────────────┘  │
│                            │                                      │
│  LAYER 2 — FILTERING       ▼                                      │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  VIP Filter  →  routes emails by tier (CRITICAL/…/SKIP)     │  │
│  └────────────────────────────────────────────────────────────┘  │
│                            │                                      │
│  LAYER 3 — AI EXTRACTION   ▼   (fully local, private)            │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Ollama (Llama 3.2 3B)  →  Pydantic Validation  →  Store    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                            │                                      │
│  LAYER 4 — SYNC LOGIC      ▼                                      │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Sync Engine  →  decides what becomes a calendar event      │  │
│  │  →  writes events to the .ics calendar file                 │  │
│  └────────────────────────────────────────────────────────────┘  │
│                            │                                      │
│  LAYER 5 — INTERFACES      ▼                                      │
│  ┌──────────────────────────┐   ┌──────────────────────────────┐ │
│  │  FastAPI  →  serves       │   │  Streamlit Dashboard         │ │
│  │  /calendar.ics locally    │   │  (view + manage + control)   │ │
│  └──────────────────────────┘   └──────────────────────────────┘ │
│              │                                                    │
└──────────────┼─────────────────────────────────────────────────── ┘
               │  user's calendar app subscribes to the .ics URL
               ▼
    ┌─────────────────────────────────────────────────────┐
    │  User's Calendar App (any of):                       │
    │  Windows Calendar · Outlook · Mac Calendar ·         │
    │  Google Calendar · iOS Calendar · any .ics-capable   │
    └─────────────────────────────────────────────────────┘
```

**Key architectural principle:** the only thing that crosses the machine boundary
is a standard `.ics` calendar file containing event titles and dates. Email
content, extracted evidence, the VIP list, and the database never leave the
device.

**Why `.ics` subscription instead of a calendar API:** the `.ics` format is a
universal open standard supported natively by every calendar application on every
platform. Using it means the system requires no API keys, no OAuth, no paid
accounts, and no per-service integration code — one implementation works
everywhere. The user subscribes to the locally served calendar URL once, and their
calendar app polls it for updates automatically.

---

## 7. AI/ML Architecture

The AI component is a **local structured-extraction pipeline**, not a chatbot and
not a cloud API integration. Its job is to read unstructured email text and output
validated, structured commitment records.

```
Email thread (plain text)
        │
        ▼
┌─────────────────────────────┐
│  Prompt Assembly            │
│  system instruction +       │
│  email text +               │
│  JSON output schema         │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  Ollama — Llama 3.2 3B      │
│  Q4_K_M quantization        │
│  runs locally on CPU        │
│  outputs JSON               │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  Pydantic Validation Layer  │
│  - schema conformance       │
│  - date parsing/validation  │
│  - enum checks (type field) │
│  - confidence range check   │
│  - retry once on failure    │
└──────────────┬──────────────┘
               │
               ▼
     Validated commitment records → SQLite
```

**Model choice — Llama 3.2 3B (Q4_K_M):** A 3-billion-parameter model quantized to
4-bit precision, roughly 2GB in size. It runs comfortably on the target hardware
(AMD Ryzen 7 5700U, 16GB RAM, no GPU) at a few seconds per email. This is
sufficient for the task, which is structured extraction of explicitly stated
information — not open-ended reasoning. Quantization is the key enabler: it reduces
the model to a size that runs on ordinary CPU RAM.

**Why local inference:** email content is sensitive. A privacy-first design that
keeps email on-device is both the core value proposition and the main architectural
differentiator. Ollama makes local inference practical with a simple HTTP API.

**Why structured extraction (not generation):** the model is used to fill a
predefined schema, not to write free text. This makes the output usable by code
and, combined with the Pydantic validation layer, reliable. This
prompt-plus-schema-plus-validation pattern is the core AI engineering skill the
project demonstrates.

**Reliability strategy:** LLM JSON output is not reliable by default. The Pydantic
layer sits between the model and the database, rejecting malformed output,
validating dates and enums, and retrying once before logging a failure. Nothing
unvalidated ever reaches the database.

---

## 8. Complete Tech Stack (With Reasons)

| Component | Technology | Why this choice |
|---|---|---|
| Language | Python 3.11+ | Best ecosystem for LLM, email, and data tooling; both team members know it |
| Local LLM runtime | Ollama | Simplest way to run quantized LLMs locally with an HTTP API; no cloud dependency |
| LLM model | Llama 3.2 3B (Q4_K_M) | Small enough for CPU + 16GB RAM, capable enough for structured extraction |
| Email access | imaplib (stdlib) | Direct IMAP access to any inbox; no third-party email API, no OAuth, keeps credentials local |
| HTML email parsing | BeautifulSoup4 + email (stdlib) | Reliable extraction of clean text from HTML email bodies |
| Output validation | Pydantic v2 | Industry-standard schema validation; the reliability layer between LLM and DB |
| Database | SQLite (via sqlite3 / SQLAlchemy) | Zero-setup, file-based, local; correct scale for a single-user tool |
| Scheduling | APScheduler | Runs the email-fetch cycle on a schedule in the background |
| Calendar output | icalendar library | Generates standard `.ics` files supported by every calendar app |
| Local server | FastAPI + Uvicorn | Serves the `.ics` file locally; lightweight and well-documented |
| Dashboard UI | Streamlit | Fast to build a data dashboard in pure Python; ideal for demos |
| Graph (optional) | NetworkX + pyvis | Commitment-network visualization in a later optional phase |
| Packaging (optional) | PyInstaller | Bundles the app into a downloadable desktop executable in a later optional phase |
| Config / secrets | python-dotenv + OS keyring | Loads settings; stores email credentials in the OS secure keychain, never plaintext |

**Deliberately excluded:**
- **No cloud LLM API** for email processing — would violate the privacy premise.
- **No calendar API / OAuth** — the `.ics` subscription approach is universal and keyless.
- **No heavyweight database** (Postgres, etc.) — unnecessary for single-user local scale.
- **No frontend framework** (React, etc.) — Streamlit is sufficient and faster for this scope.

---

## 9. Project Folder Structure

```
email-commitment-tracker/
├── src/
│   ├── collection/
│   │   ├── email_fetcher.py       # IMAP connection, fetch new emails
│   │   ├── email_parser.py        # HTML→text, thread handling, cleaning
│   │   └── scheduler.py           # APScheduler background fetch loop
│   │
│   ├── filtering/
│   │   └── vip_filter.py          # Match sender to VIP list, assign tier
│   │
│   ├── extraction/
│   │   ├── ollama_client.py       # Wrapper for Ollama HTTP API
│   │   ├── prompts.py             # Extraction prompt templates
│   │   ├── schemas.py             # Pydantic models for extraction output
│   │   └── pipeline.py            # Orchestrates email → LLM → validate → store
│   │
│   ├── storage/
│   │   ├── database.py            # SQLite connection, schema init, queries
│   │   └── models.py              # Data models / ORM entities
│   │
│   ├── sync/
│   │   ├── sync_engine.py         # Decide what becomes a calendar event
│   │   ├── ics_builder.py         # Generate .ics content from commitments
│   │   └── conflict_resolver.py   # Handle updated deadlines / duplicates
│   │
│   ├── server/
│   │   └── calendar_server.py     # FastAPI app serving /calendar.ics
│   │
│   └── config.py                  # Central settings (intervals, paths, model name)
│
├── dashboard/
│   ├── app.py                     # Streamlit entry point
│   ├── pages/
│   │   ├── overview.py            # Today's snapshot
│   │   ├── feed.py                # Full commitment list
│   │   ├── review_queue.py        # Manual-review items + pending questions
│   │   ├── vip_manager.py         # VIP contact management
│   │   └── graph.py               # (optional) commitment graph
│   └── components/
│       └── commitment_card.py     # Reusable UI card
│
├── tests/
│   ├── test_email_parser.py
│   ├── test_vip_filter.py
│   ├── test_extraction.py
│   ├── test_schemas.py
│   ├── test_ics_builder.py
│   └── fixtures/                  # Sample emails for testing
│
├── data/                          # Local runtime data (gitignored)
│   ├── tracker.db                 # SQLite database
│   └── calendar.ics               # Generated calendar file
│
├── .env                           # IMAP host, ports, settings (gitignored)
├── .gitignore
├── requirements.txt
├── PROJECT_PLAN.md                # This file
└── README.md
```

---

## 10. Database Schema

SQLite database with four core tables.

### Table: `raw_emails`
Stores fetched emails before and after processing.

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Unique email row ID |
| message_id | TEXT UNIQUE | Email's own Message-ID header (dedup key) |
| thread_id | TEXT | Groups emails in the same thread |
| sender_email | TEXT | From address |
| sender_name | TEXT | From display name |
| recipient_email | TEXT | To address |
| subject | TEXT | Subject line |
| body_text | TEXT | Cleaned plain-text body |
| received_at | DATETIME | When the email was received |
| vip_tier | TEXT | Assigned tier: CRITICAL / IMPORTANT / MONITOR / SKIP |
| processed | BOOLEAN | Whether extraction has run on this email |
| fetched_at | DATETIME | When the system fetched it |

### Table: `vip_contacts`
User-defined contact priority list.

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Unique contact rule ID |
| match_value | TEXT | Email, name pattern, or domain to match |
| match_type | TEXT | exact_email / name_pattern / domain |
| tier | TEXT | CRITICAL / IMPORTANT / MONITOR / SKIP |
| display_name | TEXT | Friendly name for the dashboard |
| created_at | DATETIME | When the rule was added |

### Table: `commitments`
The extracted commitments — the heart of the system.

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Unique commitment ID |
| email_id | INTEGER FK | Links to raw_emails.id (traceability) |
| type | TEXT | deadline_on_you / deadline_from_others / question_pending / meeting |
| subject | TEXT | Plain-language description of the commitment |
| deadline | DATETIME | When it's due (nullable for open-ended) |
| counterparty_name | TEXT | The other person's name |
| counterparty_email | TEXT | The other person's email |
| direction | TEXT | incoming / outgoing |
| evidence_quote | TEXT | Exact sentence from the email justifying the extraction |
| confidence | REAL | Model confidence 0.0–1.0 |
| vip_tier | TEXT | Inherited tier from the source email |
| status | TEXT | pending / fulfilled / overdue / dismissed |
| calendar_synced | BOOLEAN | Whether it's been written to the .ics file |
| ics_uid | TEXT | Unique ID of the corresponding .ics event (for updates) |
| created_at | DATETIME | When it was extracted |

### Table: `sync_log`
Tracks calendar sync operations for reliability and debugging.

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Unique log entry ID |
| commitment_id | INTEGER FK | Which commitment was synced |
| action | TEXT | created / updated / deleted |
| status | TEXT | success / failed / pending |
| synced_at | DATETIME | When the sync happened |
| error_message | TEXT | Error detail if failed (nullable) |

---

## 11. API Endpoints

The FastAPI server is intentionally minimal — its main job is serving the calendar
file. A few extra endpoints support the dashboard and manual control.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/calendar.ics` | Serves the current calendar file for subscription (the core endpoint) |
| GET | `/health` | Health check — is the service running |
| POST | `/fetch/trigger` | Manually trigger an email fetch cycle (instead of waiting for the schedule) |
| GET | `/commitments` | List commitments (supports filters: tier, type, status) for the dashboard |
| POST | `/commitments/{id}/sync` | Manually push a MONITOR-tier commitment to the calendar |
| POST | `/commitments/{id}/dismiss` | Dismiss a commitment (and remove from calendar if synced) |
| GET | `/vip` | List VIP contacts |
| POST | `/vip` | Add a VIP contact |
| DELETE | `/vip/{id}` | Remove a VIP contact |

Note: the dashboard (Streamlit) may talk directly to the database layer for reads
rather than through the API for simplicity; the API endpoints above exist for the
calendar subscription, manual control, and clean separation where useful.

---

## 12. LLM Extraction Workflow

This is the step-by-step flow for how one email becomes structured commitments.

**Step 1 — Input assembly.** The pipeline takes a cleaned email thread (plain text)
that has passed the VIP filter, and assembles a prompt: a system instruction
describing the task, the email text, and a JSON schema describing the required
output fields.

**Step 2 — Local inference.** The prompt is sent to Ollama (Llama 3.2 3B) via its
local HTTP API. The model returns a JSON array of extracted items. This runs
entirely on the local machine — no network call leaves the device.

**Step 3 — Validation.** The returned JSON is parsed and validated against Pydantic
models. The validator checks: every required field is present, `type` is one of the
four allowed values, `deadline` parses to a valid datetime (or is explicitly null),
`confidence` is between 0.0 and 1.0, and `evidence_quote` is non-empty. If
validation fails, the pipeline retries once with a corrective prompt, then logs a
failure if it still fails.

**Step 4 — Storage.** Each validated item is written to the `commitments` table,
linked to its source email via `email_id` for full traceability. The exact evidence
quote is stored so the user can always see why a commitment was extracted.

**Step 5 — Sync decision.** The sync engine reads each new commitment and decides,
based on `type` and `vip_tier`, whether it becomes a calendar event now (CRITICAL
deadlines), later after review (MONITOR deadlines), or never (questions pending).
See the roadmap for how this is layered in.

**Extraction output schema (conceptual):**
```
{
  "type":               one of [deadline_on_you, deadline_from_others,
                                 question_pending, meeting],
  "subject":            short plain-language description,
  "deadline":           ISO 8601 datetime or null,
  "counterparty":       name of the other party,
  "direction":          incoming or outgoing,
  "evidence_quote":     exact sentence from the email,
  "confidence":         float 0.0 to 1.0
}
```

---

## 13. Development Roadmap (Phased)

The project is built in **eight incremental phases**. Each phase produces something
testable on its own and builds on the previous one. Build one phase at a time. Do
not begin a phase until the previous phase's deliverables work and are tested.

Phases 1–6 constitute the MVP. Phases 7–8 are optional enhancements that make the
project more impressive but are not required for a working system.

---

### Phase 1 — Foundation and Email Collection
**Goal:** The system can connect to a real inbox, fetch emails, and store them
cleanly.

**Deliverables:**
- Project skeleton created per the folder structure
- Virtual environment, `requirements.txt`, `.gitignore`, `.env` template
- `email_fetcher.py` connects via IMAP and fetches recent emails
- `email_parser.py` extracts clean plain text from HTML emails, handles threads, strips signatures and quoted replies
- `database.py` initializes SQLite with the `raw_emails` table
- Fetched emails are stored correctly with all metadata

**Dependencies:** None (starting point).

**How to test:** Run the fetcher against a real inbox; confirm emails appear in
`raw_emails` with clean, readable body text.

**Estimated time:** 4–5 days.

---

### Phase 2 — VIP Filtering
**Goal:** Only emails from user-defined important contacts proceed to processing.

**Deliverables:**
- `vip_contacts` table added to the database
- `vip_filter.py` matches a sender against the VIP list (exact email, name pattern, domain) and assigns a tier
- Emails are tagged with their tier; SKIP-tier emails are marked processed and ignored
- A basic way to add/list VIP contacts (temporarily via script or simple function; full UI comes in Phase 6)

**Dependencies:** Phase 1 (needs stored emails to filter).

**How to test:** Add a known contact as CRITICAL, fetch emails, confirm emails from
that contact are tagged correctly and unknown senders are tagged SKIP.

**Estimated time:** 2–3 days.

---

### Phase 3 — Local LLM Extraction
**Goal:** Ollama extracts structured commitments from filtered emails, validated
and stored.

**Deliverables:**
- Ollama installed and Llama 3.2 3B pulled
- `ollama_client.py` — wrapper for the local Ollama HTTP API with timeout/retry handling
- `prompts.py` — the extraction prompt, iterated for reliable output
- `schemas.py` — Pydantic models for the extraction output
- `pipeline.py` — orchestrates: filtered email → prompt → Ollama → validate → store
- `commitments` table added; validated commitments are stored with source traceability

**Dependencies:** Phase 2 (needs VIP-filtered emails to process).

**How to test:** Run the pipeline on real emails; inspect the `commitments` table
and verify extractions are correct, evidence quotes are accurate, and malformed
output is handled without crashing. This phase requires prompt iteration — expect
to refine the prompt against a set of test emails.

**Estimated time:** 5–6 days (the prompt engineering is the critical work here).

---

### Phase 4 — Calendar File Generation and Serving
**Goal:** Commitments become calendar events in a locally served `.ics` file that a
real calendar app can subscribe to.

**Deliverables:**
- `ics_builder.py` — generates a valid `.ics` file from commitment records using the `icalendar` library, including event titles, dates, reminders, and the evidence quote in the description
- `calendar_server.py` — a FastAPI app serving the `.ics` file at `/calendar.ics`, plus a `/health` endpoint
- `sync_log` table added
- The generated file validates and can be subscribed to from at least one real calendar app

**Dependencies:** Phase 3 (needs commitments to put on the calendar).

**How to test:** Subscribe to the local `/calendar.ics` URL from Mac Calendar,
Windows Calendar, or Google Calendar, and confirm events appear correctly with the
right dates and reminders.

**Estimated time:** 3–4 days.

---

### Phase 5 — Sync Engine and Tier Logic
**Goal:** The system correctly decides what to put on the calendar and keeps it
up to date.

**Deliverables:**
- `sync_engine.py` — applies tier logic: CRITICAL deadlines auto-sync, IMPORTANT sync with a flag, MONITOR wait for manual approval, questions never go to calendar
- `conflict_resolver.py` — detects when a follow-up email updates an existing commitment (same subject + counterparty) and updates the existing calendar event instead of creating a duplicate
- Offline/retry handling: failed syncs are queued in `sync_log` and retried
- `.ics` file stays consistent with the database state

**Dependencies:** Phase 4 (needs calendar generation working).

**How to test:** Simulate a CRITICAL email and confirm auto-sync; simulate a
follow-up changing a deadline and confirm the event updates rather than duplicates;
confirm questions do not appear on the calendar.

**Estimated time:** 3–4 days.

---

### Phase 6 — Dashboard (Completes the MVP)
**Goal:** The user has a full interface to view commitments, manage VIPs, and
control the system.

**Deliverables:**
- Streamlit app with an overview page (today's snapshot: pending deadlines, auto-synced count, questions needing reply)
- Commitments feed with urgency coloring, filters, and evidence drill-down
- Review queue for MONITOR-tier items and pending questions, with approve/dismiss actions
- VIP manager page to add, edit, and remove contacts and tiers
- The scheduler (`scheduler.py`) wired in so fetching runs automatically in the background

**Dependencies:** Phases 1–5 (surfaces everything built so far).

**How to test:** Use the dashboard end to end on a real inbox for a full day: emails
are fetched automatically, commitments appear, CRITICAL deadlines reach the
calendar, and the user can manage VIPs and review items.

**Estimated time:** 5–6 days.

**★ End of MVP.** After Phase 6, the project is a complete, working, demonstrable
system.

---

### Phase 7 — Commitment Graph Visualization (Optional Enhancement)
**Goal:** Visualize the network of commitments across contacts.

**Deliverables:**
- `graph.py` builds a NetworkX graph from the `commitments` table (nodes = people, edges = commitments)
- pyvis renders it as an interactive visualization embedded in the dashboard
- Filtering by person and commitment type

**Dependencies:** Phase 6 (needs the dashboard and populated commitments).

**How to test:** Confirm the graph accurately reflects the commitment data and is
interactive.

**Estimated time:** 3–4 days.

---

### Phase 8 — Desktop Packaging (Optional Enhancement)
**Goal:** Ship the app as a downloadable desktop application.

**Deliverables:**
- PyInstaller configuration bundling the backend, server, and dashboard into a single executable (`.exe` for Windows, `.app` for Mac)
- A system tray presence (via pystray) with open-dashboard, pause, and settings actions
- First-run setup flow: enter email credentials (stored in the OS keyring), see the calendar subscription URL to add to their calendar app
- Desktop notifications when new commitments are found

**Dependencies:** Phase 6 (packages the complete MVP).

**How to test:** Build the installer, run it on a clean machine, complete first-run
setup, and confirm the full flow works without a development environment.

**Estimated time:** 4–5 days.

---

### Roadmap summary

| Phase | Focus | MVP? | Est. time |
|---|---|---|---|
| 1 | Email collection | Yes | 4–5 days |
| 2 | VIP filtering | Yes | 2–3 days |
| 3 | Local LLM extraction | Yes | 5–6 days |
| 4 | Calendar file + serving | Yes | 3–4 days |
| 5 | Sync engine + tier logic | Yes | 3–4 days |
| 6 | Dashboard | Yes | 5–6 days |
| 7 | Commitment graph | Optional | 3–4 days |
| 8 | Desktop packaging | Optional | 4–5 days |

MVP (Phases 1–6) is achievable in approximately four weeks for a two-person team
working 2–4 hours/day. Phases 7–8 are enhancements to pursue if time allows.

---

## 14. Testing Strategy

**Unit tests (per module):**
- `email_parser` — feed known HTML emails, assert clean text output, correct thread and signature handling
- `vip_filter` — assert correct tier assignment across exact/pattern/domain matches
- `schemas` — assert Pydantic validation accepts valid records and rejects malformed ones (bad dates, invalid types, out-of-range confidence)
- `ics_builder` — assert generated `.ics` content is valid and contains correct event data

**Integration tests:**
- Full pipeline on a set of fixture emails: email → filter → extraction → storage, verifying the end-to-end path
- Sync flow: commitment → sync engine → `.ics` file, including the update-not-duplicate case

**Fixtures:** A `tests/fixtures/` directory of representative sample emails
(deadline emails, multi-commitment emails, emails with no commitments, HTML-heavy
emails, threaded replies) used across tests. These are safe, synthetic emails —
never real personal email committed to the repo.

**Manual acceptance testing:** Running the complete system against the developers'
own real inboxes for a sustained period (48+ hours) to catch real-world edge cases
that fixtures miss. This is the ultimate test of extraction quality.

**Evaluation of extraction quality:** Maintain a small labeled set of emails with
known correct extractions and periodically check the pipeline's output against it
as the prompt is refined, to catch regressions.

---

## 15. Deployment

This is a **local-first desktop application**, not a hosted service. "Deployment"
means packaging it so a user can run it on their own machine.

**Development run:** Each component runs locally — the scheduler/fetcher process,
the FastAPI calendar server, and the Streamlit dashboard. During development these
can be started individually.

**Packaged distribution (Phase 8):** PyInstaller bundles everything into a single
downloadable executable per platform. The user downloads it, runs a one-time setup
(email credentials into the OS keyring, add the calendar subscription URL to their
calendar app), and the app then runs in the background via a system tray icon.

**Calendar subscription:** The one external-facing step is the user subscribing
their calendar app to the locally served `/calendar.ics` URL. On the same machine
or same network this is `http://localhost:8765/calendar.ics`. For access from a
phone off the local network, the future-work option is to serve the `.ics` file
through a lightweight tunnel or a free static host.

**No server infrastructure, no cloud costs, no accounts** — the entire system runs
on the user's own hardware.

---

## 16. Security and Privacy

Privacy is the core design principle of this project, not an afterthought.

**Email content never leaves the device.** All LLM inference runs locally via
Ollama. No email body, subject, or extracted content is ever sent to any cloud LLM
or third-party API. This is the central privacy guarantee.

**Credentials are stored securely.** IMAP email credentials are stored in the
operating system's secure keychain (via the `keyring` library), never in plaintext
files and never committed to the repository. The `.env` file (containing only
non-secret configuration like host and port) is gitignored.

**Local data stays local.** The SQLite database, the generated `.ics` file, and all
extracted commitments live only on the user's machine. The user has full control
and can delete everything at any time.

**Minimal external surface.** The only thing served outside the application is the
`.ics` calendar file (event titles and dates) via a local endpoint. It contains no
email bodies or sensitive content — only what the user would have typed into their
calendar manually. When served only on localhost, it is not exposed to the network
at all.

**No third-party data sharing.** The system integrates with no analytics, no
telemetry, and no external services beyond the user's own email server (IMAP) and
their own calendar app (via the local `.ics` subscription).

**App-password guidance.** For providers like Gmail that support it, the setup will
guide users to create a dedicated app-specific password rather than using their
main account password, limiting exposure.

---

## 17. Future Enhancements

Beyond the MVP and the two optional phases, natural directions for extending the
project:

- **Follow-up intelligence for received commitments** — richer tracking and
  reminders for promises others made to the user, including "you asked X five days
  ago and haven't heard back" nudges.
- **Auto-learning VIP tiers** — suggest promoting or demoting contacts based on how
  the user actually interacts with their commitments over time.
- **CalDAV write-back** — in addition to `.ics` subscription, optionally write
  events directly into Google Calendar, iCloud, or Outlook via CalDAV for users who
  prefer two-way integration.
- **Multi-account support** — handle several email accounts (personal, college,
  work) in one unified view.
- **Smarter deadline parsing** — handle relative and fuzzy dates ("early next
  week", "before the demo") with more nuance.
- **Reply drafting** — for questions pending, optionally draft a reply the user can
  review and send (kept local via Ollama to preserve the privacy model).
- **Remote access for the calendar** — a secure hosted `.ics` endpoint so phones
  off the local network always stay in sync.
- **Larger local models** — as hardware improves or for users with more RAM,
  optionally use a larger local model for higher extraction accuracy.

---

*End of PROJECT_PLAN.md — build Phase 1 first.*
