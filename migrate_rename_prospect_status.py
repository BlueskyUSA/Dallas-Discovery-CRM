"""
One-time migration: renames the "Prospect" status to "Interested Party"
(less sales-y). Updates:
  - Every existing contact currently marked "Prospect" -> "Interested Party".
  - The column's default value, so new contacts default correctly too.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_rename_prospect_status.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("UPDATE contacts SET status = 'Interested Party' WHERE status = 'Prospect'")
    updated = cur.rowcount
    print(f"Updated {updated} contact(s) from 'Prospect' to 'Interested Party'.")

    cur.execute("ALTER TABLE contacts ALTER COLUMN status SET DEFAULT 'Interested Party'")
    print("Updated the status column's default value.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
