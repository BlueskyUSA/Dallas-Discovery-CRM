"""
One-time migration: adds the training materials library to the CRM.

What it does:
  - Creates the program_materials table -- one row per uploaded handout,
    resource sheet, worksheet, etc., linked to a program and (optionally)
    which day of the weekend it's used ("Friday Night", "Saturday", "Sunday").

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_program_materials.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'program_materials'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE program_materials (
                id SERIAL PRIMARY KEY,
                program_id INTEGER NOT NULL REFERENCES programs(id),
                day_label TEXT,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            )"""
        )
        print("Created program_materials table.")
    else:
        print("program_materials table already exists.")

    conn.commit()
    conn.close()
    print("Done. Upload/manage handouts from each program's Materials page in the CRM.")


if __name__ == "__main__":
    migrate()
