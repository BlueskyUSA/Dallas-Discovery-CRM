"""
One-time migration: adds a photo field to contacts.

What it does:
  - Adds a photo_filename column to contacts (the filename of their uploaded
    photo, stored under static/uploads/contact_photos/ -- nothing else about
    an existing contact is touched).

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_contact_photo.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    contact_cols = [row["column_name"] for row in cur.fetchall()]

    if "photo_filename" not in contact_cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN photo_filename TEXT")
        print("Added contacts.photo_filename.")
    else:
        print("contacts.photo_filename already exists.")

    conn.commit()
    conn.close()
    print("Done. Photos can now be uploaded from the Add Contact form or a contact's own page.")


if __name__ == "__main__":
    migrate()
