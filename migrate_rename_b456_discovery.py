"""
One-time migration: fixes Discovery's program codes and names so the
dashboard and Authorized Users hub stop showing leftover Bluesky text.

Background: these 6 programs were created with the literal strings
"Bluesky 1".."Bluesky 6" typed into the *code* column (instead of a short
code like "B1"), which is what shows as the big number on the dashboard.
It's also why the app's internal "is this the Squeeze/couples program?"
check (which compares a program's code to the string "B4") was silently
never matching -- the real code was "Bluesky 4", not "B4".

What it does, for each program below:
  - Updates its code (e.g. "Bluesky 4" -> "B4") and, where given, its name
    -- same row, same id, so every cohort, enrollment, contract, donation,
    and training-material record already linked to that program stays
    linked. Nothing else changes.
  - If a program isn't found under its expected old code, it's skipped --
    safe to run more than once.

Changes applied:
  code "Bluesky 1" -> "B1"   (name left as-is: "Freedom")
  code "Bluesky 2" -> "B2"   (name left as-is: "Genesis")
  code "Bluesky 3" -> "B3"   (name left as-is: "Powerful Living")
  code "Bluesky 4" -> "B4"   name -> "Relationship Training"
  code "Bluesky 5" -> "B5"   (name left as-is: "Renewal")
  code "Bluesky 6" -> "B6"   (name left as-is: "Spiritual")

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_rename_b456_discovery.py
"""
from db import get_db

# (old_code, new_code, new_name_or_None)
RENAMES = [
    ("Bluesky 1", "B1", None),
    ("Bluesky 2", "B2", None),
    ("Bluesky 3", "B3", None),
    ("Bluesky 4", "B4", "Relationship Training"),
    ("Bluesky 5", "B5", None),
    ("Bluesky 6", "B6", None),
]


def migrate():
    conn = get_db()
    cur = conn.cursor()

    renamed, skipped = 0, 0
    for old_code, new_code, new_name in RENAMES:
        row = cur.execute("SELECT id, name FROM programs WHERE code = ?", (old_code,)).fetchone()
        if not row:
            skipped += 1
            print(f"No program found with code '{old_code}' -- skipping (already renamed, or not created yet).")
            continue
        final_name = new_name if new_name is not None else row["name"]
        cur.execute(
            "UPDATE programs SET code = ?, name = ? WHERE id = ?",
            (new_code, final_name, row["id"]),
        )
        renamed += 1
        print(f"Renamed '{old_code}' -> '{new_code}' ({final_name}).")

    conn.commit()
    conn.close()
    print(f"\nDone. Renamed {renamed}, skipped {skipped}.")


if __name__ == "__main__":
    migrate()
