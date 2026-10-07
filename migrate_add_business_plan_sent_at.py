"""
One-time migration: adds a "business_plan_sent_at" column to contacts, so the
CRM can show the date the Preliminary Business Plan was last emailed to
someone. It records only WHEN it was sent -- not which version, since the plan
changes over time.

What it does:
  - Adds a nullable `business_plan_sent_at` TEXT column to contacts, if it
    isn't already there. Existing rows are left blank.

Safe to run more than once.

Run this ONCE against your Postgres database -- BEFORE uploading the app
files that use it. Easiest way: open your Render service's "Shell" tab
(DATABASE_URL is already set there) and run:

    python3 migrate_add_business_plan_sent_at.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        ("contacts", "business_plan_sent_at"),
    )
    if cur.fetchall():
        print("contacts.business_plan_sent_at already exists.")
    else:
        cur.execute("ALTER TABLE contacts ADD COLUMN business_plan_sent_at TEXT")
        print("Added business_plan_sent_at column to contacts.")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
