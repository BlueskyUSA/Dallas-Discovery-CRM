"""
One-time migration: locks down training materials & music playlists so
they're confidential.

What it does:
  1. Creates program_access -- lets a Leadership account authorize specific
     Staff accounts, by program, to view that program's materials/playlists.
     Leadership accounts can always see everything; Staff need an explicit
     grant (managed from each program's "Confidential access" page).
  2. Moves any already-uploaded training material files out of static/
     (which Flask serves to absolutely anyone with the link, logged in or
     not) into a private folder that only the app itself can read, and
     that requires being logged in AND authorized for that program.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_program_access.py
"""
import os
import shutil
from db import get_db

OLD_MATERIAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads", "program_materials")
NEW_MATERIAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_uploads", "program_materials")


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'program_access'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE program_access (
                id SERIAL PRIMARY KEY,
                program_id INTEGER NOT NULL REFERENCES programs(id),
                staff_id INTEGER NOT NULL REFERENCES staff(id),
                granted_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            )"""
        )
        print("Created program_access table.")
    else:
        print("program_access table already exists.")
    conn.commit()
    conn.close()

    if os.path.isdir(OLD_MATERIAL_DIR):
        os.makedirs(NEW_MATERIAL_DIR, exist_ok=True)
        moved = 0
        for fn in os.listdir(OLD_MATERIAL_DIR):
            src = os.path.join(OLD_MATERIAL_DIR, fn)
            dst = os.path.join(NEW_MATERIAL_DIR, fn)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.move(src, dst)
                moved += 1
        print(f"Moved {moved} existing material file(s) out of static/ into the private folder.")
        try:
            if not os.listdir(OLD_MATERIAL_DIR):
                os.rmdir(OLD_MATERIAL_DIR)
        except OSError:
            pass
    else:
        print("No existing static/uploads/program_materials folder found -- nothing to move.")

    print("Done. Use each program's 'Confidential access' page to authorize Staff accounts.")


if __name__ == "__main__":
    migrate()
