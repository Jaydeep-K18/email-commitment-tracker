"""Store the IMAP password in the OS keyring (run once).

The password is read interactively via ``getpass`` — it is never passed on the
command line, printed, or written to any file. See PROJECT_PLAN.md §16.

    python -m scripts.set_imap_password
"""
from __future__ import annotations

import getpass

import keyring

from src import config


def main() -> int:
    if not config.IMAP_USER:
        print(
            "IMAP_USER is not set. Copy .env.example to .env and set IMAP_USER "
            "to your email address first."
        )
        return 1

    print(f"Storing IMAP password for: {config.IMAP_USER}")
    print(f"Keyring service:           {config.KEYRING_SERVICE}")
    print(
        "\nFor Gmail: enable 2-Step Verification and create an App Password at\n"
        "https://myaccount.google.com/apppasswords , then paste it below.\n"
    )

    password = getpass.getpass("IMAP password (input hidden): ").strip()
    if not password:
        print("No password entered. Aborted.")
        return 1

    keyring.set_password(config.KEYRING_SERVICE, config.IMAP_USER, password)
    print("\nSaved to the OS keyring. Now run:  python -m src.collection.email_fetcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
