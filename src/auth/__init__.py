"""Google sign-in (Phase 9 of PROJECT_PLAN.md).

Deliberately **not** named ``google``: that is a namespace package owned by the
Google client libraries, and a local package of the same name shadows it, which
breaks every ``from google.oauth2 import ...`` in the process.
"""
