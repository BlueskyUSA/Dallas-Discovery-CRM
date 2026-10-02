"""
One-time migration: adds "Refocus" and "Relationship Training" month & year
fields alongside the existing D1/D2/D3 month & year fields.

Adds two columns if they don't already exist:
  - refocus_month_year
  - relationship_training_month_year

Nothing to migrate for existing rows — both start out blank until filled in.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_refocus_relationship_training.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    if "refocus_month_year" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN refocus_month_year TEXT")
        print("Added refocus_month_year column.")
    else:
        print("refocus_month_year column already exists.")

    if "relationship_training_month_year" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN relationship_training_month_year TEXT")
        print("Added relationship_training_month_year column.")
    else:
        print("relationship_training_month_year column already exists.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
