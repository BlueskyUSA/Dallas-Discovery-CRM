"""
One-time migration: adds the "complete your profile" self-service link.

Why: Kent wants to email past Discovery/Bluesky participants (and anyone
else) a personal link so they can fill in or update their own full
Contact profile without a CRM login. Each contact gets a unique,
unguessable token (generated on demand from their Contact page) that maps
to a public URL: /complete-profile/<token>.

What it does:
  - Adds a nullable, unique profile_token column to contacts.

Safe to run more than once. Doesn't generate any tokens itself -- those
are created one at a time from each contact's page when you click
"Generate link".

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_contact_profile_token.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'contacts' AND column_name = 'profile_token'"
    )
    if not cur.fetchall():
        cur.execute("ALTER TABLE contacts ADD COLUMN profile_token TEXT UNIQUE")
        conn.commit()
        print("Added profile_token column to contacts.")
    else:
        print("profile_token column already exists.")

    conn.close()


if __name__ == "__main__":
    migrate()
