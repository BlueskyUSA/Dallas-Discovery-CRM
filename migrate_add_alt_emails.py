"""
One-time migration: adds two extra optional personal-email slots per
contact, so staff and contacts themselves can record an old or secondary
address alongside the primary one. The /discovery short form uses all
three (email, email_2, email_3) when deciding whether a submission belongs
to an existing contact, so someone signing up again under a different
email they've used before still matches correctly instead of creating a
duplicate.

Adds two columns if they don't already exist:
  - email_2  (secondary personal email)
  - email_3  (third personal email)

Nothing to migrate for existing rows -- both start out blank until filled
in from the long form or the CRM.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_alt_emails.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    if "email_2" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN email_2 TEXT")
        print("Added email_2 column.")
    else:
        print("email_2 column already exists.")

    if "email_3" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN email_3 TEXT")
        print("Added email_3 column.")
    else:
        print("email_3 column already exists.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
