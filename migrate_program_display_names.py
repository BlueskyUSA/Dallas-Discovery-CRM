"""
One-time fix: changes the display names of Discovery's programs.

    D1  "Freedom"          ->  "D1"
    D2  "Genesis"          ->  "D2"
    D3  "Powerful Living"  ->  "D3"
    D5  "Renewal"          ->  "Refocus"

What it does:
  - Renames each program in place -- same row, same id -- so every training
    session, enrollment, contract, donation and training-material record
    already linked to it stays linked. Nothing is deleted. The program
    descriptions (e.g. "Claiming your Peace and Joy...") are NOT changed.
  - Only renames a program whose CURRENT name is exactly the old name shown
    above (any capitalization). If a program has already been renamed, or you
    gave it a different name yourself, it is left alone and the script says so.
  - Includes the D5 rename, so you do NOT also need to run
    migrate_rename_d5_refocus.py (running both is harmless).

Safe to run more than once.

Run this ONCE against your Postgres database. Easiest way: open your
Render service's "Shell" tab (DATABASE_URL is already set there) and run:

    python3 migrate_program_display_names.py
"""
from db import get_db

# (program code, old name, new name)
RENAMES = [
    ("D1", "Freedom", "D1"),
    ("D2", "Genesis", "D2"),
    ("D3", "Powerful Living", "D3"),
    ("D5", "Renewal", "Refocus"),
]


def migrate():
    conn = get_db()
    cur = conn.cursor()
    for code, old_name, new_name in RENAMES:
        row = cur.execute("SELECT id, name FROM programs WHERE code = ?", (code,)).fetchone()
        if not row:
            print(f"{code}: no program with this code -- skipped.")
        elif (row["name"] or "").strip().lower() == old_name.lower():
            cur.execute("UPDATE programs SET name = ? WHERE id = ?", (new_name, row["id"]))
            print(f'{code}: renamed "{row["name"]}" -> "{new_name}".')
        else:
            print(f'{code}: currently named "{row["name"]}" -- left as is.')
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
