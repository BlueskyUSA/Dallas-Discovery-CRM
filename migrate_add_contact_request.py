"""
One-time migration: adds the "please reach out to me personally" request
fields to contacts -- used by the new public /connect/<token> page linked
from the Excitement email, so someone who wants a personal follow-up
(instead of just emailing Kent directly) can ask for one, and staff get a
simple queue of those requests instead of a flooded personal inbox.

Adds four columns if they don't already exist:
  - contact_request_method   ('Email' or 'Phone')
  - contact_request_phone    (optional callback number, if different from cell_phone)
  - contact_request_note     (optional note they left)
  - contact_requested_at     (timestamp; cleared once staff mark it handled)

Nothing to migrate for existing rows -- all start out blank.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_contact_request.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    new_columns = [
        "contact_request_method",
        "contact_request_phone",
        "contact_request_note",
        "contact_requested_at",
    ]
    for col in new_columns:
        if col not in cols:
            cur.execute(f"ALTER TABLE contacts ADD COLUMN {col} TEXT")
            print(f"Added {col} column.")
        else:
            print(f"{col} column already exists.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
