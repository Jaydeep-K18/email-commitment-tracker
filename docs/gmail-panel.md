# The Gmail side panel

A small panel that appears inside Gmail when you open an email. It shows what
the local model found in that message — a deadline, who it involves, how
confident it is — and offers to put it on your calendar.

It is optional. Everything works without it; this is for the times you are
reading an email and want *this one* captured, rather than waiting for the
background cycle or hoping the sender is on your VIP list.

## Install (Chrome, Edge, Brave)

1. Start the tracker (the tray app, or `python -m src.server.calendar_server`)
2. Open `chrome://extensions`
3. Turn on **Developer mode** (top right)
4. Click **Load unpacked** and choose the `extension` folder in this project
5. Open the extension's **Options** (via the puzzle-piece menu, or the
   *Details → Extension options* link)
6. In the tracker dashboard, go to **Mailbox setup → Gmail side panel**, click
   **Show access token**, and copy it
7. Paste it into the extension options and press **Save**, then **Test**

**Test** should report how many open commitments the tracker is holding. If it
does not, the message tells you which of the three usual causes it is: the app
is not running, the token is wrong, or the address is not the default.

Firefox uses the same code but loads it differently (`about:debugging` →
*Load Temporary Add-on*), and unloads it when the browser closes.

## Why there is a token at all

The tracker listens on `127.0.0.1`, and it is tempting to treat that as private.
It is not. Loopback is reachable by **any page your browser has open** — a
random website can quietly `fetch("http://127.0.0.1:8765/…")` in the background.

Without a check, such a page could read what the tracker extracted from your
email, or push events into your calendar. So every `/api/*` request must carry a
token that only the extension has.

`/calendar.ics` and `/health` stay open on purpose: a calendar app subscribing
to a feed cannot send a custom header, and the feed is the whole point. What
leaks in the worst case is your commitment titles to a page that already had to
guess the port — a trade made knowingly, not an oversight.

Press **Generate a new token** in the dashboard to revoke the old one. Anything
still holding it — including the extension, until you paste the new one — stops
working immediately.

## What it does with your email

The message text goes to the tracker on your own machine, which runs the same
local model the background pipeline uses. Nothing is sent to any AI service.
Nothing is stored until you press **Add to calendar**.

## When it cannot read the message

Gmail's HTML is generated and its class names are undocumented, so the panel's
selectors are a guess Google can invalidate at any time. When that happens the
panel says it could not read the message and offers a **Scan** button.

That is deliberate. The alternative — sending an empty body to the model and
reporting "no commitments found" — looks exactly like a genuinely uneventful
email, and would quietly teach you to distrust the panel.

## Adding overrides your skip list

Senders you have never marked as VIP are tiered `SKIP` automatically, and `SKIP`
mail normally never reaches the calendar. Picking one open email by hand is the
single exception: choosing a specific message is a more precise statement than a
rule about its sender.

Commitments added this way are marked `manually_added` in the database, so this
override cannot be reached from the review queue — only from the panel.

## Troubleshooting

**Panel never appears** — it only shows with a message open, not in the message
list. Check the extension is enabled for `mail.google.com`.

**"Could not reach the tracker"** — the desktop app is not running, or the
calendar server was started on a different port.

**"The tracker rejected this token"** — the token was rotated. Copy the current
one from the dashboard.

**"The local model could not be reached"** — Ollama is not running. Start it and
press Scan.
