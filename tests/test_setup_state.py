"""Tests for where the app keeps its data, and for first-run setup.

Setup is what the onboarding screens and Settings → Integrations show; the
worker's internal API answers them from :mod:`src.first_run`. The data
directory decides where ``.env``, the Google token and the ``.ics`` file live,
including for an install that keeps them outside the checkout.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import config, first_run


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
    # Migrations and .env are resolved against this, so it has to be the root.
    assert (config.resource_dir(frozen=False) / "alembic.ini").exists()


# --- First-run setup -------------------------------------------------------

#: Setup now has a second requirement — a local model — so the mailbox tests
#: below hold it constant rather than silently relying on its default.
MODEL_READY = {"ollama_running": True, "model_present": True}


def test_setup_is_needed_until_both_pieces_are_present():
    assert not first_run.SetupState(False, False, **MODEL_READY).complete
    assert not first_run.SetupState(True, False, **MODEL_READY).complete
    assert first_run.SetupState(True, True, **MODEL_READY).complete


def test_credentials_alone_are_not_enough_without_a_model():
    """A mailbox the app cannot read is not a working setup."""
    assert not first_run.SetupState(True, True, ollama_running=False).complete
    assert not first_run.SetupState(
        True, True, ollama_running=True, model_present=False
    ).complete


def test_the_missing_piece_is_named_in_the_users_terms():
    assert "address" in first_run.SetupState(False, False, **MODEL_READY).missing
    assert "password" in first_run.SetupState(True, False, **MODEL_READY).missing
    assert first_run.SetupState(True, True, **MODEL_READY).missing == ""


def test_a_missing_model_is_named_before_credentials():
    """Ollama is the first thing to fix, so it is the first thing reported."""
    assert "Ollama" in first_run.SetupState(False, False).missing
    assert "model" in first_run.SetupState(
        False, False, ollama_running=True, model_present=False
    ).missing


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
