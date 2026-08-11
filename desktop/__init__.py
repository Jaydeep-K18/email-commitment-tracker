"""Desktop packaging layer (Phase 8 of PROJECT_PLAN.md).

Turns the collection of scripts that make up the tracker into one double-clickable
application: a tray icon that supervises the dashboard, the calendar server and
the background scheduler, plus the first-run setup a user needs before any of it
can work.

Nothing in :mod:`src` or :mod:`dashboard` imports from here — the app runs
exactly the same from a checkout as it does from a bundle.
"""
