"""
One-time fix: renames the program coded D5 from "Renewal" to "Refocus".

What it does:
  - Finds the program whose code is D5. If its name is "Renewal" (any
    capitalization), changes the name to "Refocus" -- same row, same id, so
    every cohort, enrollment, contract and training-material record already
    linked to it stays linked. Nothing else is touched.
  - If D5 doesn't exist, or already has a different name (including
    "Refocus"), it leaves it alone and says so -- so it can never overwrite a
    name you chose yourself.

Safe to run more than once.

Run this ONCE against your Postgres database. Easiest way: open your
Render service's "Shell" tab (DATABASE_URL is already set there) and run:

    python3 migrate_rename_d5_refocus.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT id, name FROM programs WHERE code = ?", ("D5",)).fetchone()
    if not row:
        print("No program with code D5 found -- nothing to rename.")
    elif (row["name"] or "").strip().lower() == "renewal":
        cur.execute("UPDATE programs SET name = ? WHERE id = ?", ("Refocus", row["id"]))
        print('Renamed D5 from "Renewal" to "Refocus".')
    else:
        print(f'D5 is currently named "{row["name"]}" -- leaving it as is.')
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
