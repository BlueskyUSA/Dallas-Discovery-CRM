"""
One-time migration: adds staff logins to the CRM.

What it does:
  - Creates the staff table (name, email, password, role) if it doesn't
    already exist.
  - If the table is empty, walks you through creating your own first
    account interactively (right here in the terminal) -- you'll set your
    name, email, and a password, as a Leadership account so you have full
    access from the start.

Safe to run more than once: if staff already exist, it just confirms the
table is there and does not touch existing accounts.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_staff_logins.py

After this, restart the app and go to /crm -- you'll be sent to a login
page. Once logged in as a Leadership account, an "Add staff" page lets you
create logins for anyone else who needs access.
"""
import getpass
from werkzeug.security import generate_password_hash
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'staff'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE staff (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Staff',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            )"""
        )
        conn.commit()
        print("Created staff table.")
    else:
        print("staff table already exists.")

    cur.execute("SELECT COUNT(*) AS n FROM staff")
    existing_count = cur.fetchone()["n"]

    if existing_count > 0:
        print(f"There are already {existing_count} staff account(s) -- nothing more to do.")
        conn.close()
        return

    print("\nNo staff accounts exist yet. Let's create your first login (Leadership access).")
    name = input("Your name: ").strip()
    email = input("Your email: ").strip().lower()
    while True:
        password = getpass.getpass("Choose a password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Those didn't match -- try again.")
            continue
        if len(password) < 8:
            print("Please use at least 8 characters -- try again.")
            continue
        break

    cur.execute(
        "INSERT INTO staff (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (name, email, generate_password_hash(password), "Leadership"),
    )
    conn.commit()
    conn.close()
    print(f"\nDone. {name} <{email}> can now log in at /crm as Leadership.")


if __name__ == "__main__":
    migrate()
