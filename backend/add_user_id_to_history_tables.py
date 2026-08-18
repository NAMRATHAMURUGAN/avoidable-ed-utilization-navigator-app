"""Idempotent schema-update script: add user_id linkage columns.

This project creates its schema via ``Base.metadata.create_all()`` (see
``backend/database.py`` / ``backend/initialize_database.py``), which only
creates tables that do not exist yet -- it never alters an existing table.
The ``user_id`` column added to ``TriageEncounter``/``NavigationAction`` (so
an authenticated PATIENT or PAYER can retrieve their own persistent history)
therefore will not appear on a PostgreSQL database that already has these
tables from before this change, unless this script is run against it once.

This script only ever adds a column, foreign key, and index if they are not
already present. It never drops, rewrites, or reads application data, and
never drops or recreates a table. Running it multiple times, or against a
database that already has these columns, is a safe no-op.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Connection

from backend.database import get_engine


_TABLES = ("triage_encounters", "navigation_actions")


def _column_exists(connection: Connection, table_name: str, column_name: str) -> bool:
    return any(
        col["name"] == column_name for col in inspect(connection).get_columns(table_name)
    )


def _foreign_key_exists(connection: Connection, table_name: str, column_name: str) -> bool:
    return any(
        column_name in fk["constrained_columns"]
        for fk in inspect(connection).get_foreign_keys(table_name)
    )


def add_user_id_columns(engine: Engine | None = None) -> None:
    """Add a nullable user_id FK (+ index) to triage_encounters and
    navigation_actions if not already present. Safe to run more than once.

    ``engine`` defaults to the process-wide production engine
    (``backend.database.get_engine()``); it is injectable so tests can pass
    an isolated engine instead of ever touching a real configured database.
    """
    engine = engine or get_engine()
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table in _TABLES:
            if not inspector.has_table(table):
                # Table does not exist yet; create_database_schema() will
                # create it (with user_id already in the model) on its own.
                print(f"Skipping {table}: table does not exist yet.")
                continue

            if not _column_exists(connection, table, "user_id"):
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id BIGINT"))
                print(f"Added user_id column to {table}.")
            else:
                print(f"{table}.user_id already exists; skipping column add.")

            if not _foreign_key_exists(connection, table, "user_id"):
                connection.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ADD CONSTRAINT fk_{table}_user_id "
                        f"FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL"
                    )
                )
                print(f"Added user_id foreign key to {table}.")
            else:
                print(f"{table}.user_id foreign key already exists; skipping.")

            connection.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)")
            )
            print(f"Ensured index ix_{table}_user_id on {table}.")


if __name__ == "__main__":
    add_user_id_columns()
    print("user_id schema update complete. No existing data was modified or dropped.")
