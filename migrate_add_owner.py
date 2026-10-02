"""
One-time migration: introduces an "Owner" flag, separate from the
Leadership role.

Why: up to now, every Leadership account automatically saw every program's
confidential materials and all of Contacts. Kent wants that automatic,
no-exceptions access reserved for himself alone -- a second Leadership
account (e.g. a partner's) should see confidential areas only once
specifically authorized, same as Staff, unless explicitly given full access.

What it does:
  - Adds an is_owner column to staff (defaults to 0/false for everyone).
  - Sets is_owner = 1 on Kent's own account (matched by email), so he's
    never locked out by this change. If that email isn't found, the
    earliest-created Leadership account is used instead, and this script
    says clearly which account it picked.

IMPORTANT: after this runs, other existing Leadership accounts (if any)
will lose automatic access to Contacts and program materials/playlists --
they'll need to be authorized per-area (or given full access) from the
Staff page, same as anyone else. Only the account this script marks as
Owner keeps seeing everything automatically.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_owner.py
"""
from db import get_db

OWNER_EMAIL = "kent@blueskyusa.net"


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'staff' AND column_name = 'is_owner'")
    if not cur.fetchall():
        cur.execute("ALTER TABLE staff ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0")
        print("Added is_owner column to staff.")
    else:
        print("is_owner column already exists.")
    conn.commit()

    existing_owner = conn.execute("SELECT name, email FROM staff WHERE is_owner = 1").fetchone()
    if existing_owner:
        print(f"Owner already set: {existing_owner['name']} ({existing_owner['email']}). Leaving as-is.")
        conn.close()
        return

    target = conn.execute("SELECT * FROM staff WHERE email = ?", (OWNER_EMAIL,)).fetchone()
    if not target:
        target = conn.execute(
            "SELECT * FROM staff WHERE role = 'Leadership' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if target:
            print(f"No account found with email {OWNER_EMAIL} -- using the earliest Leadership account instead.")

    if not target:
        print("Couldn't find any account to mark as Owner. Create your account first, then re-run this script.")
        conn.close()
        return

    conn.execute("UPDATE staff SET is_owner = 1, role = 'Leadership' WHERE id = ?", (target["id"],))
    conn.commit()
    conn.close()
    print(f"Set {target['name']} ({target['email']}) as the Owner.")
    print("Every other account -- Staff or Leadership -- now needs to be authorized per area, or given full access, from the Staff page.")


if __name__ == "__main__":
    migrate()
