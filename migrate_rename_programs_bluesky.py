"""
One-time migration: renames the four training programs from their old
T1/T2/T3/T4 codes to the names you actually use -- Bluesky 1, Bluesky 2,
Bluesky 3, and Squeeze.

What it does:
  - For each program currently stored as T1, T2, T3, or T4, updates its
    code, name, and description in place -- same row, same id, so every
    session, enrollment, contract, donation, and training-material record
    already linked to that program stays linked. Nothing else changes.
  - If a program has already been renamed (or was never created), it's
    skipped -- safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_rename_programs_bluesky.py
"""
from db import get_db

RENAMES = [
    ("T1", "BS1", "Bluesky 1 — Your Past", "Examines your past."),
    ("T2", "BS2", "Bluesky 2 — Your Present", "Examines your present. Requires Bluesky 1."),
    ("T3", "BS3", "Bluesky 3 — Your Future", "Examines your future. Requires Bluesky 2."),
    ("T4", "SQZ", "Squeeze — Bluesky Relationship Training", "Relationship training for couples. Standalone, no prerequisite."),
]


def migrate():
    conn = get_db()
    cur = conn.cursor()

    renamed, skipped = 0, 0
    for old_code, new_code, new_name, new_desc in RENAMES:
        row = cur.execute("SELECT id FROM programs WHERE code = ?", (old_code,)).fetchone()
        if not row:
            skipped += 1
            print(f"No program found with code '{old_code}' -- skipping (maybe already renamed).")
            continue
        cur.execute(
            "UPDATE programs SET code = ?, name = ?, description = ? WHERE id = ?",
            (new_code, new_name, new_desc, row["id"]),
        )
        renamed += 1
        print(f"Renamed '{old_code}' -> '{new_code}' ({new_name}).")

    conn.commit()
    conn.close()
    print(f"\nDone. Renamed {renamed}, skipped {skipped}.")


if __name__ == "__main__":
    migrate()
