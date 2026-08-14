"""Create the database-foundation tables without seeding or dropping data."""

from backend.database import create_database_schema


if __name__ == "__main__":
    create_database_schema()
    print("Database foundation tables are present.")
