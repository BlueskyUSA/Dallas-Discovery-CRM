"""
One-time migration: renames Discovery's program codes from B1-B6 to D1-D6
(Discovery, not Bluesky -- "B" was a leftover from the original fork).

What it does:
  1. For each program currently coded B1..B6, updates its code to D1..D6 in
     place -- same row, same id, so every cohort, enrollment, contract,
     donation, and training-material record already linked to it stays
     linked. Names are untouched.
  2. For any access_grants row already authorizing a Team account for area
     "B1".."B6" (the Authorized Users hub), renames that area_key to
     "D1".."D6" too, so nobody's existing access silently breaks.

Safe to run more than once -- anything already on a D-code (or with no
matching B-code row) is skipped.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_rename_b_to_d_codes.py

Note: run this *after* migrate_rename_b456_discovery.py (which gets the
codes from "Bluesky 1".."Bluesky 6" to B1..B6 in the first place). If you
haven't run that one yet, run it first.
"""
from db import get_db

CODES = ["1", "2", "3", "4", "5", "6"]


def migrate():
    conn = get_db()
    cur = conn.cursor()

    renamed, skipped = 0, 0
    for n in CODES:
        old_code, new_code = f"B{n}", f"D{n}"

        row = cur.execute("SELECT id, name FROM programs WHERE code = ?", (old_code,)).fetchone()
        if row:
            cur.execute("UPDATE programs SET code = ? WHERE id = ?", (new_code, row["id"]))
            renamed += 1
            print(f"Renamed program {old_code} -> {new_code} ({row['name']}).")
        else:
            skipped += 1
            print(f"No program found with code '{old_code}' -- skipping (already renamed, or not created yet).")

        grants = cur.execute("SELECT id FROM access_grants WHERE area_key = ?", (old_code,)).fetchall()
        for g in grants:
            cur.execute("UPDATE access_grants SET area_key = ? WHERE id = ?", (new_code, g["id"]))
        if grants:
            print(f"  Also updated {len(grants)} access_grants row(s) from area '{old_code}' to '{new_code}'.")

    conn.commit()
    conn.close()
    print(f"\nDone. Renamed {renamed} program(s), skipped {skipped}.")


if __name__ == "__main__":
    migrate()
