"""
One-time migration: adds an optional "end_date" to training sessions, so a
multi-day seminar (e.g. a Friday-Sunday weekend) can show every day on the
calendar instead of only its first day.

What it does:
  - Adds a nullable `end_date` column to cohorts, if it isn't already there.
  - Changes nothing else. Every existing session keeps its date and simply
    has a blank end date, meaning a one-day session, exactly as before.

Safe to run more than once.

Run this ONCE, BEFORE uploading the new app files. In your Render service's
"Shell" tab (DATABASE_URL is already set there):

    python3 migrate_add_cohort_end_date.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        ("cohorts", "end_date"),
    )
    if cur.fetchone():
        print("cohorts.end_date already exists. Nothing to do.")
    else:
        cur.execute("ALTER TABLE cohorts ADD COLUMN end_date TEXT")
        print("Added cohorts.end_date.")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
