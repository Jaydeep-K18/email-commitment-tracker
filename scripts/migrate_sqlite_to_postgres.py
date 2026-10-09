"""Copy the existing SQLite database into PostgreSQL — once, verifiably.

    python -m scripts.migrate_sqlite_to_postgres
    python -m scripts.migrate_sqlite_to_postgres --source sqlite:///path/to/tracker.db

The target is ``DATABASE_URL``. Its schema is created by Alembic first, so the
copy lands in exactly the tables the Node server expects.

Primary keys are copied as they are, not renumbered. That matters more than it
looks: commitments already published to Google Calendar are matched to their
events by ``gcal_event_id`` and their ``.ics`` UIDs are derived from their ids,
so renumbering would make the next sync create a second copy of every event the
user already has. The script verifies those ids survived before reporting
success.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from sqlalchemy import Table, create_engine, func, inspect, select, text, update
from sqlalchemy.engine import Engine

from src import config
from src.storage.database import build_engine, is_sqlite, upgrade_schema
from src.storage.models import Base, Commitment, User

BATCH_SIZE = 500


class MigrationError(RuntimeError):
    """The copy could not be done safely, or did not verify."""


@dataclass
class MigrationReport:
    copied: dict[str, int] = field(default_factory=dict)
    google_event_ids: int = 0

    def summary(self) -> str:
        lines = [f"  {name:20} {count:6}" for name, count in self.copied.items()]
        lines.append(f"  Google event ids preserved: {self.google_event_ids}")
        return "\n".join(lines)


def _self_references(table: Table) -> list[str]:
    """Columns pointing back at their own table, e.g. ``supersedes_id``.

    A row can reference one that comes later in id order, so these are written
    as NULL on the first pass and filled in once every row exists.
    """
    return [
        column.name
        for column in table.columns
        for fk in column.foreign_keys
        if fk.column.table is table
    ]


def _prepare_target(target_url: str, engine: Engine) -> None:
    if is_sqlite(target_url):
        Base.metadata.create_all(engine)   # only ever a test target
    else:
        upgrade_schema(target_url)


def _ensure_empty(engine: Engine) -> None:
    with engine.connect() as connection:
        existing = connection.execute(
            select(func.count()).select_from(Base.metadata.tables["raw_emails"])
        ).scalar_one()
    if existing:
        raise MigrationError(
            f"The target database already holds {existing} emails. Refusing to copy "
            "on top of it, since that would duplicate rows. Point DATABASE_URL at "
            "an empty database."
        )


def _owner(target: Engine) -> int:
    """The account the copied mail belongs to: the first admin.

    The SQLite database predates accounts. If nobody has signed up yet, the
    rows go to a placeholder admin that the first account created claims
    (apps/server/src/auth/routes.ts), as migration 0005 does.
    """
    with target.begin() as connection:
        owner = connection.execute(
            select(User.id).where(User.is_admin.is_(True)).order_by(User.id)
        ).scalar()
        if owner is None:
            owner = connection.execute(
                User.__table__.insert().values(email="unclaimed@localhost", is_admin=True).returning(User.id)
            ).scalar_one()
    return owner


def _copy_table(table: Table, source: Engine, target: Engine, source_columns: set[str], owner: int) -> int:
    columns = [column for column in table.columns if column.name in source_columns]
    deferred = [name for name in _self_references(table) if name in source_columns]

    with source.connect() as reader:
        rows = [dict(row._mapping) for row in reader.execute(
            select(*columns).order_by(*(c for c in table.primary_key.columns if c.name in source_columns))
        )]
    if not rows:
        return 0
    if "user_id" in table.columns and "user_id" not in source_columns:
        rows = [{**row, "user_id": owner} for row in rows]

    first_pass = [{**row, **{name: None for name in deferred}} for row in rows]
    with target.begin() as writer:
        for start in range(0, len(first_pass), BATCH_SIZE):
            writer.execute(table.insert(), first_pass[start:start + BATCH_SIZE])

        for name in deferred:
            key = table.primary_key.columns.values()[0]
            for row in rows:
                if row[name] is not None:
                    writer.execute(
                        update(table).where(key == row[key.name]).values({name: row[name]})
                    )
    return len(rows)


def _reset_sequences(target: Engine) -> None:
    """Move each Postgres id sequence past the ids that were copied in.

    Inserting explicit ids does not advance the sequence, so without this the
    next email the worker stores would be given id 1 and collide.
    """
    if target.dialect.name != "postgresql":
        return
    with target.begin() as connection:
        for table in Base.metadata.sorted_tables:
            key_columns = list(table.primary_key.columns)
            if len(key_columns) != 1 or key_columns[0].name != "id":
                continue
            if not key_columns[0].autoincrement or key_columns[0].type.python_type is str:
                continue
            connection.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
            ))


def _verify(source: Engine, target: Engine, report: MigrationReport) -> None:
    for name, expected in report.copied.items():
        table = Base.metadata.tables[name]
        with target.connect() as connection:
            actual = connection.execute(select(func.count()).select_from(table)).scalar_one()
        if actual != expected:
            raise MigrationError(f"{name}: copied {expected} rows but found {actual}.")

    query = select(Commitment.id, Commitment.gcal_event_id).where(
        Commitment.gcal_event_id.is_not(None)
    )
    with source.connect() as connection:
        before = dict(connection.execute(query).all())
    with target.connect() as connection:
        after = dict(connection.execute(query).all())
    if before != after:
        raise MigrationError(
            "Google Calendar event ids did not survive the copy. Do not run a sync "
            "against this database: it would create duplicate events."
        )
    report.google_event_ids = len(after)


def migrate(source_url: str, target_url: str) -> MigrationReport:
    """Copy every table that exists in the source; verify; return what moved."""
    if source_url == target_url:
        raise MigrationError("Source and target are the same database.")

    source = create_engine(source_url, future=True)
    target = build_engine(target_url)
    try:
        _prepare_target(target_url, target)
        _ensure_empty(target)

        present = set(inspect(source).get_table_names())
        owner = _owner(target)
        report = MigrationReport()
        for table in Base.metadata.sorted_tables:
            if table.name not in present or table.name == "users":
                continue
            source_columns = {c["name"] for c in inspect(source).get_columns(table.name)}
            report.copied[table.name] = _copy_table(table, source, target, source_columns, owner)

        _reset_sequences(target)
        _verify(source, target, report)
        return report
    finally:
        source.dispose()
        target.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default=config.SQLITE_URL)
    parser.add_argument("--target", default=config.DATABASE_URL)
    args = parser.parse_args(argv)

    target = config.normalize_database_url(args.target)
    if is_sqlite(target):
        print(
            "DATABASE_URL still points at SQLite. Set it to your Postgres database "
            "(see .env.example) and run this again."
        )
        return 1

    print(f"Copying {args.source}\n     -> {target.split('@')[-1]}")
    try:
        report = migrate(args.source, target)
    except MigrationError as exc:
        print(f"\nStopped: {exc}")
        return 1
    print("\nCopied and verified:\n" + report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
