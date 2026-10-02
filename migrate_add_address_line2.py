"""
One-time migration: adds a second address line (apt/suite/unit) for both the
trainee's own address and the sponsor's address.

Adds two columns if they don't already exist:
  - street_address_2          (trainee's own address, line 2)
  - sponsor_street_address_2  (sponsor's address, line 2)

Nothing to migrate for existing rows — both start out blank until filled in.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_address_line2.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    if "street_address_2" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN street_address_2 TEXT")
        print("Added street_address_2 column.")
    else:
        print("street_address_2 column already exists.")

    if "sponsor_street_address_2" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN sponsor_street_address_2 TEXT")
        print("Added sponsor_street_address_2 column.")
    else:
        print("sponsor_street_address_2 column already exists.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
