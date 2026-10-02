"""
One-time migration: renames the "Staff" role to "Team" everywhere it's
stored, to match the "Staff" -> "Team" wording change across the app
(nav, page titles, login pages, training rosters, etc).

Why: Kent asked to rename every user-visible "Staff" to "Team". The
account role stored in the database is one of the things people actually
see (it shows in the Role column on the Team page, and it's what the
login-page label/dropdown reflect), so existing accounts with
role = 'Staff' need to be updated to role = 'Team' too. Nothing else in
the app compares against the literal string 'Staff' (only 'Leadership' is
checked for), so this is safe -- it only affects display and the Add
account dropdown, not any access-control logic.

What it does:
  - Updates every staff row with role = 'Staff' to role = 'Team'.
  - Leaves 'Leadership' rows untouched.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_staff_role_to_team.py
"""
from db import get_db


def migrate():
    conn = get_db()

    rows = conn.execute("SELECT id, name, email FROM staff WHERE role = 'Staff'").fetchall()
    if not rows:
        print("No accounts have role = 'Staff' -- nothing to do.")
        conn.close()
        return

    conn.execute("UPDATE staff SET role = 'Team' WHERE role = 'Staff'")
    conn.commit()
    conn.close()

    print(f"Updated {len(rows)} account(s) from role 'Staff' to 'Team':")
    for r in rows:
        print(f"  - {r['name']} ({r['email']})")


if __name__ == "__main__":
    migrate()
