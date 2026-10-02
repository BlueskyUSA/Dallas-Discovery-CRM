"""
One-time migration: renames the training programs to the short B1-B4 codes
and replaces the old per-program "Confidential access" pages with the new
"Authorized Users" hub (Accounting, Marketing, Contacts, plus B1-B6).

What it does:
  1. Renames program codes BS1/BS2/BS3/SQZ -> B1/B2/B3/B4 (or straight from
     the older T1-T4 codes, if you haven't run the earlier rename yet).
     Only the short code changes -- full names stay as they are.
  2. Creates the access_grants table (Leadership always has access to
     everything; up to 4 Staff accounts can be authorized per area).
  3. Carries over any grants you already made on the old per-program access
     page into the new system, then removes that old table.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_authorized_users.py
"""
from db import get_db

# Supports running this whether you're still on the original T1-T4 codes,
# already on BS1/BS2/BS3/SQZ (yesterday's rename), or partway between.
CODE_RENAMES = [
    ("T1", "B1"), ("BS1", "B1"),
    ("T2", "B2"), ("BS2", "B2"),
    ("T3", "B3"), ("BS3", "B3"),
    ("T4", "B4"), ("SQZ", "B4"),
]


def rename_codes(conn):
    renamed = 0
    for old_code, new_code in CODE_RENAMES:
        row = conn.execute("SELECT id FROM programs WHERE code = ?", (old_code,)).fetchone()
        if row:
            conn.execute("UPDATE programs SET code = ? WHERE id = ?", (new_code, row["id"]))
            renamed += 1
            print(f"Renamed program code '{old_code}' -> '{new_code}'.")
    conn.commit()
    if not renamed:
        print("No program codes needed renaming (already on B1-B4, or no programs yet).")


def create_access_grants(cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'access_grants'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE access_grants (
                id SERIAL PRIMARY KEY,
                area_key TEXT NOT NULL,
                staff_id INTEGER NOT NULL REFERENCES staff(id),
                granted_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            )"""
        )
        print("Created access_grants table.")
    else:
        print("access_grants table already exists.")


def migrate_old_grants(conn, cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'program_access'"
    )
    if not cur.fetchall():
        print("No old program_access table found -- nothing to carry over.")
        return

    old_grants = conn.execute(
        """SELECT pa.staff_id, p.code FROM program_access pa
           JOIN programs p ON p.id = pa.program_id ORDER BY pa.id"""
    ).fetchall()
    carried, capped = 0, 0
    counts = {}
    for g in old_grants:
        area_key = g["code"]
        counts.setdefault(area_key, 0)
        if counts[area_key] >= 4:
            capped += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?", (area_key, g["staff_id"])
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (area_key, g["staff_id"])
            )
            counts[area_key] += 1
            carried += 1
    conn.commit()
    print(f"Carried over {carried} existing grant(s) into Authorized Users.")
    if capped:
        print(f"Note: {capped} old grant(s) were skipped because an area already had 4 authorized users.")

    cur.execute("DROP TABLE program_access")
    conn.commit()
    print("Removed the old program_access table.")


def migrate():
    conn = get_db()
    cur = conn.cursor()
    rename_codes(conn)
    create_access_grants(cur)
    conn.commit()
    migrate_old_grants(conn, cur)
    conn.close()
    print("Done. Manage access from the 'Authorized Users' link in the nav (Leadership accounts only).")


if __name__ == "__main__":
    migrate()
