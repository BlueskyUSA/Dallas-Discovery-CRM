"""
One-time migration: moves uploaded file storage (contact photos and program
materials) from the web server's local disk into Postgres.

Why: Render's web service filesystem is ephemeral -- it's wiped and
recreated fresh on every deploy. Files written to local disk at runtime
(contact photos in static/uploads/contact_photos/, program materials in
private_uploads/program_materials/) were silently disappearing every time
the app was redeployed. Storing the file bytes directly in the database
fixes this for good, at no extra infrastructure cost.

What it does:
  - Creates a new contact_photos table (contact_id -> data, content_type),
    one row per contact with a photo.
  - Adds nullable `data` and `content_type` columns to program_materials.

This does NOT recover any photos or materials that were already wiped by a
previous deploy -- those files are gone. Anyone affected will need to
re-upload. This migration only makes future uploads durable.

Safe to run more than once. Run this ONCE against your Postgres database:

    python3 migrate_add_file_storage_to_db.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'contact_photos'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE contact_photos (
                contact_id INTEGER PRIMARY KEY REFERENCES contacts(id),
                data BYTEA NOT NULL,
                content_type TEXT NOT NULL
            )"""
        )
        conn.commit()
        print("Created contact_photos table.")
    else:
        print("contact_photos table already exists.")

    for column, coltype in (("data", "BYTEA"), ("content_type", "TEXT")):
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'program_materials' AND column_name = ?",
            (column,),
        )
        if not cur.fetchall():
            cur.execute(f"ALTER TABLE program_materials ADD COLUMN {column} {coltype}")
            conn.commit()
            print(f"Added {column} column to program_materials.")
        else:
            print(f"{column} column already exists on program_materials.")

    conn.close()


if __name__ == "__main__":
    migrate()
