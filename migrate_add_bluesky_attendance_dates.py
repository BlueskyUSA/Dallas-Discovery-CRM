"""
One-time migration: adds T1/T2/T3 month & year fields, plus "Bluesky Refocus"
and "Bluesky Relationship Training", for the new Bluesky Seminar Attendance
column (mirrors the existing D1/D2/D3 + Refocus/Relationship Training fields
under Discovery attendance).

Adds these columns if they don't already exist:
  - t1_month_year
  - t2_month_year
  - t3_month_year
  - bluesky_refocus_month_year
  - bluesky_relationship_training_month_year

Nothing to migrate for existing rows — all start out blank until filled in.
The existing refocus_month_year / relationship_training_month_year columns
(now labeled "Refocus (Discovery)" / "Relationship Training (Discovery)" in
the UI) are untouched — this migration only adds new columns.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_bluesky_attendance_dates.py
"""
from db import get_db

NEW_COLUMNS = [
    "t1_month_year",
    "t2_month_year",
    "t3_month_year",
    "bluesky_refocus_month_year",
    "bluesky_relationship_training_month_year",
]


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    for col_name in NEW_COLUMNS:
        if col_name not in cols:
            cur.execute(f"ALTER TABLE contacts ADD COLUMN {col_name} TEXT")
            print(f"Added {col_name} column.")
        else:
            print(f"{col_name} column already exists.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
