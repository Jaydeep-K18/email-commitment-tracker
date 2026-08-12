# Connecting Google — a five-minute, no-cost setup

The tracker can read your mail and write your calendar through Google's APIs.
Both are **free**; creating the project below costs nothing and needs no card.

You do this once, in your own Google account. The app never sees a password —
Google hands it a token, and that token lives in your operating system's
credential store.

## Why this step exists at all

The tracker also publishes a local `.ics` feed at
`http://127.0.0.1:8765/calendar.ics`. That works with **Outlook desktop**,
**Apple Calendar** and **Thunderbird**, which fetch the file from your own
machine.

It cannot work with Google Calendar. When you "add a calendar from URL", Google
fetches that URL *from Google's servers*, and they have no route to `127.0.0.1`
on your laptop. No amount of leaving the app running fixes it. Connecting Google
properly is the only way deadlines reach Google Calendar and your phone.

## 1. Create a project

1. Go to <https://console.cloud.google.com/projectcreate>
2. Name it anything (`email-commitment-tracker` is fine) and click **Create**

## 2. Turn on the two APIs

With the new project selected, enable both:

- **Gmail API** — <https://console.cloud.google.com/apis/library/gmail.googleapis.com>
- **Google Calendar API** — <https://console.cloud.google.com/apis/library/calendar-json.googleapis.com>

Click **Enable** on each.

## 3. Configure the consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External**, then **Create**
3. Fill in an app name and your own email for both support fields
4. On the **Scopes** step you can click straight through — the app requests its
   scopes at sign-in time
5. On **Test users**, click **Add users** and add **your own Gmail address**

> Leaving the app in *Testing* is the right choice for personal use. It needs no
> review, and only the test users you list can sign in.

## 4. Create the credentials

1. Go to **APIs & Services → Credentials**
2. **Create credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Create, then **Download JSON**

## 5. Drop the file where the app looks

Save the downloaded file as:

```
data/google_client_secret.json
```

(inside the project folder, next to `tracker.db`). In a packaged build, the
setup screen shows the exact path to use.

## 6. Sign in

Start the app, open the dashboard, go to **Mailbox setup**, and click
**Sign in with Google**. A browser tab opens; approve the two permissions.

You will see an "unverified app" warning. That is expected — the app is yours
and has not been through Google's review, which is only needed for public
distribution. Click **Advanced → Go to … (unsafe)** to continue.

## What it asks for, and why

| Scope | Why |
|---|---|
| `gmail.readonly` | Read messages to extract deadlines. Read-only: the app cannot send, delete or modify anything, and never marks mail as read. |
| `calendar.events` | Create and update the events it extracts. Deliberately **not** the full `calendar` scope, which would also allow deleting entire calendars. |

Only the extracted commitment is written to Google: a title, a date, the
sentence it came from, and the contact. Your email content is never sent to any
AI service — extraction runs locally through Ollama.

## Troubleshooting

**"No Google client configuration found yet"** — step 5 is missing, or the file
has a different name. The setup page prints the exact path it is checking.

**"Access blocked: … has not completed the Google verification process"** — your
address is not on the test-user list from step 3.

**Sign-in worked, but no events appear** — events are only created for
commitments the tier policy publishes. Check the **Review queue**: `MONITOR` and
untiered commitments wait for your approval by design.

## Prefer not to do any of this?

The app password path still works and needs no Google project at all — it is
also the only route for non-Gmail mailboxes (Outlook, Yahoo, university IMAP).
You then subscribe a desktop calendar app to the local `.ics` feed. You lose
Google Calendar and phone sync; everything else is identical.
