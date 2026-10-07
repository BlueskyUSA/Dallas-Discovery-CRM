"""
One-time migration: adds an "enrolled_by_contact_id" column to enrollments,
so the CRM can record WHO brought each trainee in. That powers the new
requirement: to TA in D1 or the Relationship training, a person must have
enrolled at least one person; to be a TA Team Captain, at least two.

What it does:
  - Adds a nullable `enrolled_by_contact_id` INTEGER column (a reference to
    contacts) to enrollments, if it isn't already there.
  - Does NOT touch any existing rows -- every existing enrollment simply
    starts with "brought in by" blank, and can be filled in on the cohort
    page whenever you like.

Safe to run more than once.

Run this ONCE against your Postgres database. Easiest way: open your
Render service's "Shell" tab (DATABASE_URL is already set there) and run:

    python3 migrate_add_enrolled_by.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        ("enrollments", "enrolled_by_contact_id"),
    )
    if cur.fetchall():
        print("enrollments.enrolled_by_contact_id already exists.")
    else:
        cur.execute(
            "ALTER TABLE enrollments ADD COLUMN enrolled_by_contact_id INTEGER REFERENCES contacts(id)"
        )
        print("Added enrolled_by_contact_id column to enrollments.")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
