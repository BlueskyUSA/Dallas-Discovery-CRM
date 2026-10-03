"""
One-time migration: renames the display names of Discovery's B4, B5, and B6
program slots so the dashboard and Authorized Users hub stop showing
leftover Bluesky-style names.

What it does:
  - For each program currently stored under code B4, B5, or B6, updates its
    name in place -- same row, same id, same code, so every cohort,
    enrollment, contract, donation, and training-material record already
    linked to that program stays linked. Nothing else changes (description
    is left as-is).
  - If a program code doesn't exist yet (e.g. B5/B6 haven't been created
    yet), it's skipped -- safe to run more than once, and safe to run
    before those slots are actually used.

Renames applied:
  B4 -> "Relationship Training"
  B5 -> "Renewal"
  B6 -> "Spiritual"

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_rename_b456_discovery.py
"""
from db import get_db

RENAMES = [
    ("B4", "Relationship Training"),
    ("B5", "Renewal"),
    ("B6", "Spiritual"),
]


def migrate():
    conn = get_db()
    cur = conn.cursor()

    renamed, skipped = 0, 0
    for code, new_name in RENAMES:
        row = cur.execute("SELECT id, name FROM programs WHERE code = ?", (code,)).fetchone()
        if not row:
            skipped += 1
            print(f"No program found with code '{code}' -- skipping (not created yet).")
            continue
        old_name = row["name"]
        cur.execute("UPDATE programs SET name = ? WHERE id = ?", (new_name, row["id"]))
        renamed += 1
        print(f"Renamed {code}: '{old_name}' -> '{new_name}'.")

    conn.commit()
    conn.close()
    print(f"\nDone. Renamed {renamed}, skipped {skipped}.")


if __name__ == "__main__":
    migrate()
