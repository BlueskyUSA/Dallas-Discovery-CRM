"""
One-time migration: adds "contact lists" -- a record of which list(s) each
contact came from -- and tags every contact you have TODAY as being on
"Kent's personal list".

Why: when the Dallas Discovery database is added later, the two groups will
be mixed together. This record is what lets you (and the email sender) still
tell them apart -- e.g. send Blast #2 only to the Dallas Discovery list.

What it does:
  - Creates the two small tables (contact_lists, contact_list_members) if
    they are not already there.
  - Creates the list "Kent's personal list" and puts every existing contact
    on it, dated today -- but ONLY the first time. If that list already
    exists, nothing is tagged, so running this again later (after the Dallas
    database has been added) will NOT wrongly label those new people.
  - Changes nothing about any contact's own information.

Safe to run more than once.

Run this ONCE, BEFORE adding the Dallas Discovery database. In your Render
service's "Shell" tab (DATABASE_URL is already set there):

    python3 migrate_add_contact_lists.py
"""
from datetime import date
from db import get_db

PERSONAL_LIST = "Kent's personal list"

CREATE_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS contact_lists (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS contact_list_members (
        id SERIAL PRIMARY KEY,
        contact_id INTEGER NOT NULL REFERENCES contacts(id),
        list_id INTEGER NOT NULL REFERENCES contact_lists(id),
        added_at TEXT NOT NULL DEFAULT '',
        UNIQUE(contact_id, list_id)
    )""",
]


def migrate():
    conn = get_db()
    cur = conn.cursor()
    for stmt in CREATE_STATEMENTS:
        cur.execute(stmt)
    today = date.today().isoformat()

    existing = cur.execute("SELECT id FROM contact_lists WHERE name = ?", (PERSONAL_LIST,)).fetchone()
    if existing:
        print(f'"{PERSONAL_LIST}" already exists -- no contacts were tagged this time.')
    else:
        cur.execute("INSERT INTO contact_lists (name, created_at) VALUES (?, ?)", (PERSONAL_LIST, today))
        list_id = cur.execute("SELECT id FROM contact_lists WHERE name = ?", (PERSONAL_LIST,)).fetchone()["id"]
        cur.execute(
            "INSERT INTO contact_list_members (contact_id, list_id, added_at) "
            "SELECT id, CAST(? AS INTEGER), CAST(? AS TEXT) FROM contacts",
            (list_id, today),
        )
        n = cur.execute("SELECT COUNT(*) AS n FROM contact_list_members WHERE list_id = ?", (list_id,)).fetchone()["n"]
        print(f'Created "{PERSONAL_LIST}" and put {n} existing contacts on it.')
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
