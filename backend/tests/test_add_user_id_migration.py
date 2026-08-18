"""Regression tests for the idempotent user_id schema-update script.

These tests only ever use isolated, throwaway SQLite engines created here --
never backend.database.get_engine() / DATABASE_URL -- so they can never
touch a real configured database. The script's actual ALTER TABLE statements
are PostgreSQL-specific (this project targets PostgreSQL in production; see
the module docstring in add_user_id_to_history_tables.py) and are therefore
not exercised here: these tests instead verify the idempotent-detection
logic itself, using engines where the column is already present (the same
state a fresh SQLite test database is always in, since the model already
defines user_id) so only the "already present, skip" branches run.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect

from backend.add_user_id_to_history_tables import add_user_id_columns
from backend.database import Base


class AddUserIdMigrationTestCase(unittest.TestCase):
    def test_is_a_safe_no_op_when_columns_already_exist(self) -> None:
        """A schema created from the current models already has user_id;
        running the script against it must be a safe, error-free no-op."""
        engine = create_engine("sqlite:///:memory:")
        try:
            import backend.models  # noqa: F401  (register all declarative models)

            Base.metadata.create_all(engine)

            add_user_id_columns(engine=engine)  # must not raise

            inspector = inspect(engine)
            for table in ("triage_encounters", "navigation_actions"):
                columns = {col["name"] for col in inspector.get_columns(table)}
                self.assertIn("user_id", columns)

            # Running it a second time must also be a safe no-op.
            add_user_id_columns(engine=engine)
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()

    def test_is_a_safe_no_op_when_tables_do_not_exist_yet(self) -> None:
        """Against a completely empty database, the script must skip
        cleanly rather than error -- create_database_schema() owns table
        creation, not this script."""
        engine = create_engine("sqlite:///:memory:")
        try:
            add_user_id_columns(engine=engine)  # must not raise
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
