"""Tests for the desktop packaging layer (Phase 8).

The build itself cannot be asserted here — it takes minutes and produces a
120MB artefact. What *can* be pinned down are the things that silently differ
between running from a checkout and running from a bundle: where the database
ends up, how the app re-launches itself, and what the user is asked for on a
first run. Those are exactly the places a packaged build breaks while every
existing test still passes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from desktop import notifier, runtime
from src import config, first_run
from src.storage import database
from src.storage.models import Base, Commitment, RawEmail

NOW = datetime(2026, 8, 11, 12, 0)


# --- Where data lives ------------------------------------------------------

def test_a_checkout_keeps_its_data_in_the_repo():
    """Development behaviour must not change: the existing database is there."""
    resolved = config.user_data_dir(
        frozen=False, base_dir=Path("/repo"), environ={}
    )
    assert resolved == Path("/repo/data")


def test_a_frozen_build_writes_outside_the_bundle():
    """PyInstaller unpacks a one-file build to a temp dir and deletes it on
    exit. Writing the database there would lose every commitment on quit."""
    resolved = config.user_data_dir(
        frozen=True,
        platform="win32",
        environ={"LOCALAPPDATA": r"C:\Users\me\AppData\Local"},
    )
    assert resolved == Path(r"C:\Users\me\AppData\Local") / config.APP_NAME


@pytest.mark.parametrize(
    "platform,environ,expected_tail",
    [
        ("darwin", {}, ("Library", "Application Support")),
        ("linux", {"XDG_DATA_HOME": "/home/me/.share"}, (".share",)),
    ],
)
def test_each_platform_uses_its_own_convention(platform, environ, expected_tail):
    resolved = config.user_data_dir(
        frozen=True, platform=platform, environ=environ
    )
    assert resolved.name == config.APP_NAME
    assert resolved.parent.parts[-len(expected_tail):] == expected_tail


def test_an_explicit_data_dir_overrides_everything():
    """Needed for testing, and for a user who wants the database elsewhere."""
    resolved = config.user_data_dir(
        frozen=True, platform="win32", environ={"ECT_DATA_DIR": r"D:\tracker"}
    )
    assert resolved == Path(r"D:\tracker")


def test_the_resource_dir_follows_pyinstallers_unpack_directory(monkeypatch):
    monkeypatch.setattr(config.sys, "_MEIPASS", r"C:\Temp\_MEI123", raising=False)
    assert config.resource_dir(frozen=True) == Path(r"C:\Temp\_MEI123")


def test_the_resource_dir_is_the_repo_in_a_checkout():
    # dashboard/app.py is resolved against this, so it has to be the root.
    assert (config.resource_dir(frozen=False) / "dashboard" / "app.py").exists()


# --- Re-launching ourselves ------------------------------------------------

def test_a_checkout_relaunches_through_the_interpreter(monkeypatch):
    monkeypatch.setattr(config, "is_frozen", lambda: False)
    command = runtime.self_command(runtime.SERVE_DASHBOARD_FLAG)
    assert command[1:] == ["-m", "desktop.main", runtime.SERVE_DASHBOARD_FLAG]


def test_a_frozen_build_relaunches_itself(monkeypatch):
    """sys.executable is the bundled .exe once frozen, so there is no
    interpreter to hand ``-m streamlit`` to — the app must re-run itself."""
    monkeypatch.setattr(config, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime.sys, "executable", r"C:\App\Tracker.exe")
    assert runtime.self_command(runtime.SERVE_DASHBOARD_FLAG) == [
        r"C:\App\Tracker.exe",
        runtime.SERVE_DASHBOARD_FLAG,
    ]


def test_the_dashboard_stays_on_loopback():
    assert runtime.DASHBOARD_HOST == "127.0.0.1"
    assert runtime.dashboard_url().startswith("http://127.0.0.1:")


def test_waiting_for_a_port_gives_up_rather_than_hanging():
    # Nothing listens on this port; the call must return False, not block.
    assert runtime.wait_for_port("127.0.0.1", 9, timeout=0.5) is False


# --- First-run setup -------------------------------------------------------

def test_setup_is_needed_until_both_pieces_are_present():
    assert not first_run.SetupState(has_user=False, has_password=False).complete
    assert not first_run.SetupState(has_user=True, has_password=False).complete
    assert first_run.SetupState(has_user=True, has_password=True).complete


def test_the_missing_piece_is_named_in_the_users_terms():
    assert "address" in first_run.SetupState(False, False).missing
    assert "password" in first_run.SetupState(True, False).missing
    assert first_run.SetupState(True, True).missing == ""


def test_saving_an_address_twice_leaves_one_line():
    """Correcting a typo must not leave two IMAP_USER lines, where whichever
    one loses the parse silently decides which mailbox is read."""
    text = first_run.env_upsert("", "IMAP_USER", "first@x.com")
    text = first_run.env_upsert(text, "IMAP_USER", "second@x.com")
    assert text.count("IMAP_USER") == 1
    assert "second@x.com" in text


def test_saving_an_address_preserves_the_rest_of_the_file():
    original = "# comment\nIMAP_PORT=993\nIMAP_USER=old@x.com\nFETCH_MAX_EMAILS=50\n"
    updated = first_run.env_upsert(original, "IMAP_USER", "new@x.com")
    assert "IMAP_PORT=993" in updated
    assert "FETCH_MAX_EMAILS=50" in updated
    assert "# comment" in updated
    assert "old@x.com" not in updated


def test_a_commented_key_is_not_mistaken_for_a_real_one():
    updated = first_run.env_upsert("#IMAP_USER=ignored@x.com\n", "IMAP_USER", "real@x.com")
    assert "real@x.com" in updated
    assert "#IMAP_USER=ignored@x.com" in updated


def test_the_password_is_never_written_to_the_env_file(tmp_path, monkeypatch):
    """The keyring is the only place it may live (PROJECT_PLAN.md §16)."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    first_run.save_email_address("someone@example.com")
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "someone@example.com" in written
    assert "PASSWORD" not in written.upper()


def test_a_missing_address_means_no_stored_password():
    assert first_run.password_is_stored("") is False


# --- New-commitment notifications ------------------------------------------

@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """A real database file wired into the storage layer."""
    engine = create_engine(f"sqlite:///{tmp_path / 'desktop.db'}", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(bind=engine, future=True, expire_on_commit=False),
    )
    try:
        yield engine
    finally:
        engine.dispose()


def add_commitment(subject: str, *, deadline=None, status="pending") -> int:
    with database.session_scope() as session:
        email = RawEmail(message_id=f"<{subject}>", subject=subject)
        session.add(email)
        session.flush()
        commitment = Commitment(
            email_id=email.id,
            type="deadline_on_you",
            subject=subject,
            deadline=deadline,
            evidence_quote="because the email said so",
            confidence=0.9,
            status=status,
        )
        session.add(commitment)
        session.flush()
        return commitment.id


def test_nothing_is_announced_when_nothing_is_new(engine):
    sent = []
    watcher = notifier.CommitmentNotifier(lambda t, m: sent.append((t, m)))
    add_commitment("Existing work")
    watcher.prime()

    assert watcher.poll() is None
    assert sent == []


def test_priming_silences_the_existing_backlog(engine):
    """A first run must describe what just arrived, not the whole history.

    Announcing everything already in the database is how a user learns to
    ignore the notifications entirely.
    """
    for index in range(12):
        add_commitment(f"Old commitment {index}")

    sent = []
    watcher = notifier.CommitmentNotifier(lambda t, m: sent.append((t, m)))
    watcher.prime()

    assert watcher.high_water > 0
    assert watcher.poll() is None
    assert sent == []


def test_a_new_commitment_is_announced_once(engine):
    sent = []
    watcher = notifier.CommitmentNotifier(lambda t, m: sent.append((t, m)))
    watcher.prime()

    add_commitment("Send the report", deadline=NOW + timedelta(days=1))
    first = watcher.poll()
    assert first is not None
    assert "1 new commitment" == first.title
    assert "Send the report" in first.message
    assert len(sent) == 1

    # Polling again with nothing new must stay quiet.
    assert watcher.poll() is None
    assert len(sent) == 1


def test_a_batch_names_the_soonest_and_counts_the_rest(engine):
    sent = []
    watcher = notifier.CommitmentNotifier(lambda t, m: sent.append((t, m)))
    watcher.prime()

    add_commitment("Later task", deadline=NOW + timedelta(days=9))
    add_commitment("Urgent task", deadline=NOW + timedelta(hours=2))
    add_commitment("Middle task", deadline=NOW + timedelta(days=3))
    add_commitment("Fourth task", deadline=NOW + timedelta(days=20))

    notice = watcher.poll()
    assert notice is not None
    assert notice.title == "4 new commitments"
    # The soonest deadline leads, because that is what earns opening the app.
    assert notice.message.index("Urgent task") < notice.message.index("Middle task")
    assert "Later task" not in notice.message
    assert "and 2 more" in notice.message


def test_dismissed_and_superseded_arrivals_are_not_announced(engine):
    sent = []
    watcher = notifier.CommitmentNotifier(lambda t, m: sent.append((t, m)))
    watcher.prime()

    add_commitment("Replaced by a later email", status="superseded")
    add_commitment("Already dismissed", status="dismissed")

    assert watcher.poll() is None
    assert sent == []


def test_a_quiet_arrival_still_advances_the_high_water_mark(engine):
    """Otherwise a superseded row would be reconsidered on every later cycle."""
    watcher = notifier.CommitmentNotifier(lambda t, m: None)
    watcher.prime()
    before = watcher.high_water

    add_commitment("Superseded", status="superseded")
    watcher.poll()

    assert watcher.high_water > before


def test_an_undated_commitment_sorts_last_but_is_still_announced(engine):
    watcher = notifier.CommitmentNotifier(lambda t, m: None)
    watcher.prime()

    add_commitment("No date given", deadline=None)
    add_commitment("Has a date", deadline=NOW + timedelta(days=2))

    notice = watcher.poll()
    assert notice is not None
    assert notice.message.index("Has a date") < notice.message.index("No date given")


def test_a_failing_notification_backend_does_not_break_the_cycle(engine):
    """A cycle that extracted commitments must still be considered done even if
    the toast could not be shown."""
    def explode(title, message):
        raise RuntimeError("no notification service")

    watcher = notifier.CommitmentNotifier(explode)
    watcher.prime()
    add_commitment("Something new")

    notice = watcher.poll()          # must not raise
    assert notice is not None
    assert watcher.poll() is None    # and must not re-announce


def test_polling_before_priming_primes_instead_of_shouting(engine):
    """Guards the case where a caller forgets prime(): the first poll must not
    announce the entire database."""
    for index in range(5):
        add_commitment(f"Pre-existing {index}")

    sent = []
    watcher = notifier.CommitmentNotifier(lambda t, m: sent.append((t, m)))
    assert watcher.poll() is None
    assert sent == []


def test_an_empty_batch_produces_no_notice():
    assert notifier.build_notice([]) is None


# --- Windowed-build streams ------------------------------------------------

def test_absent_standard_streams_are_replaced(monkeypatch):
    """A ``console=False`` build starts with sys.stdout and sys.stderr as None.

    Any library that inspects them then fails in a way that never occurs during
    development: uvicorn's log formatter calls sys.stdout.isatty() while the
    config object is being built, so constructing a server raised
    "Unable to configure formatter 'default'" and the tray started nothing.
    """
    from desktop import main as desktop_main

    monkeypatch.setattr(desktop_main.sys, "stdout", None)
    monkeypatch.setattr(desktop_main.sys, "stderr", None)

    desktop_main.ensure_standard_streams()

    assert desktop_main.sys.stdout is not None
    assert desktop_main.sys.stderr is not None
    # The contract is that the calls uvicorn makes *answer* rather than raise.
    # (On Windows the null device reports isatty() True, which is fine — colour
    # codes written to NUL go nowhere.)
    assert isinstance(desktop_main.sys.stdout.isatty(), bool)
    desktop_main.sys.stdout.write("discarded")
    desktop_main.sys.stderr.write("discarded")


def test_existing_streams_are_left_alone(monkeypatch):
    sentinel = object()
    from desktop import main as desktop_main

    monkeypatch.setattr(desktop_main.sys, "stdout", sentinel)
    desktop_main.ensure_standard_streams()
    assert desktop_main.sys.stdout is sentinel
