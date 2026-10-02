"""
One-time data migration: copies everything in your local bluesky_crm.db
(SQLite) into your new Postgres database (DATABASE_URL).

Run this ONCE, on your Mac, after:
  1. You've created the Postgres database on Render (or wherever it's hosted).
  2. You've set DATABASE_URL in your .env file to point at it.
  3. The app has been started at least once against that Postgres database
     (so schema_postgres.sql has created the empty tables) OR you run
     `python3 -c "from db import init_db; init_db()"` first.

It is safe to re-run: existing rows in Postgres (matched by id) are left
alone and not duplicated, but if you're unsure, migrate into a *fresh*
empty Postgres database rather than re-running against one you've already
started using.

Usage:
    pip install -r requirements.txt
    python3 migrate_sqlite_to_postgres.py
"""

import os
import sqlite3

import psycopg
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "bluesky_crm.db")
DATABASE_URL = os.environ.get("DATABASE_URL")

# Tables in dependency order (parents before children), matching schema.sql.
TABLES = [
    "programs",
    "contacts",
    "cohorts",
    "small_groups",
    "cohort_staffing",
    "small_group_staffing",
    "enrollments",
    "contracts",
    "contract_revisions",
    "training_profiles",
    "donations",
]

# Older local databases (from before the "training_profiles" rename) may
# still have this under its original name — read from whichever exists.
LEGACY_TABLE_NAMES = {
    "training_profiles": "t4_relationship_profiles",
}


def _sqlite_table_exists(sconn, table):
    row = sconn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def migrate():
    if not DATABASE_URL:
        raise SystemExit(
            "DATABASE_URL is not set. Fill it in in your .env file first "
            "(see .env.example)."
        )
    if not os.path.exists(SQLITE_PATH):
        raise SystemExit(f"No local database found at {SQLITE_PATH} — nothing to migrate.")

    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row

    pconn = psycopg.connect(DATABASE_URL)
    pcur = pconn.cursor()

    for table in TABLES:
        source_table = table
        if not _sqlite_table_exists(sconn, source_table):
            legacy_name = LEGACY_TABLE_NAMES.get(table)
            if legacy_name and _sqlite_table_exists(sconn, legacy_name):
                source_table = legacy_name
            else:
                print(f"{table}: no such table in your local database — skipping")
                continue

        rows = sconn.execute(f"SELECT * FROM {source_table}").fetchall()
        if not rows:
            print(f"{table}: nothing to migrate")
            continue

        cols = rows[0].keys()
        col_list = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))

        copied = 0
        for row in rows:
            values = [row[c] for c in cols]
            pcur.execute(
                f"""INSERT INTO {table} ({col_list}) VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING""",
                values,
            )
            copied += pcur.rowcount
        pconn.commit()
        print(f"{table}: copied {copied} of {len(rows)} row(s) (skipped ones with a matching id already there)")

        # Keep the table's auto-increment sequence in sync with the ids we
        # just inserted, so the next INSERT in the app doesn't collide.
        pcur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )
        pconn.commit()

    sconn.close()
    pcur.close()
    pconn.close()
    print("\nDone. Spot-check a few contacts and enrollments in the app before deleting bluesky_crm.db.")


if __name__ == "__main__":
    migrate()
