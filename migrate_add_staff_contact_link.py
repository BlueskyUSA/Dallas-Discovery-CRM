"""
One-time migration: links team logins to Contact records.

Why: Kent wants every team member represented as a full Contact (name,
address, emergency contact, sponsor info, etc), matched up with
participants, rather than a bare name+email on their login. This adds an
optional link from a staff (team login) row to its Contact record. New
team accounts are created through this link going forward (see
staff_new_search / staff_new_for_contact in app.py); existing accounts can
be matched up one at a time from the Team page's new "Link to contact"
link.

What it does:
  - Adds a nullable contact_id column to staff, referencing contacts(id).

Safe to run more than once. Doesn't touch or guess any existing data --
it only adds the column.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_staff_contact_link.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'staff' AND column_name = 'contact_id'"
    )
    if not cur.fetchall():
        cur.execute("ALTER TABLE staff ADD COLUMN contact_id INTEGER REFERENCES contacts(id)")
        conn.commit()
        print("Added contact_id column to staff.")
    else:
        print("contact_id column already exists.")

    conn.close()


if __name__ == "__main__":
    migrate()
