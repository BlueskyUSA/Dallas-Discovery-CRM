"""
One-time migration: adds a "duty" column to cohort_staffing and
small_group_staffing, so staffing assignments can capture the fuller set of
training roles Kent described (Director, Lead Facilitator, Support
Facilitator, TA, Trainee -- each optionally paired with a specific duty like
Small Group Leader/Team Captain/Doors/Runners/Time Keeper/Microphones/
Housekeeper/Lights/Sound Equipment/Large Group Leader).

What it does:
  - Adds a nullable `duty` TEXT column to cohort_staffing (if not already
    there).
  - Adds a nullable `duty` TEXT column to small_group_staffing (if not
    already there).
  - Does NOT touch any existing rows -- old Facilitator / Contract Support
    TA / TA rows keep working exactly as before, just with duty left blank.

Safe to run more than once.

Run this ONCE against your Postgres database. Easiest way: open your
Render service's "Shell" tab (DATABASE_URL is already set there) and run:

    python3 migrate_add_staffing_duty.py

(Running it from your Mac instead works too, as long as DATABASE_URL is set
to your database's connection string first.)
"""
from db import get_db


def _add_column_if_missing(conn, table, column, coltype="TEXT"):
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    if cur.fetchall():
        print(f"{table}.{column} already exists.")
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    print(f"Added {column} column to {table}.")


def migrate():
    conn = get_db()
    _add_column_if_missing(conn, "cohort_staffing", "duty")
    _add_column_if_missing(conn, "small_group_staffing", "duty")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
