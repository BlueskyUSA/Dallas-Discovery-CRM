"""Seed the four programs (Bluesky 1/2/3 + Squeeze) with the
Bluesky 1 -> Bluesky 2 -> Bluesky 3 prerequisite chain.

Note: this is no longer run automatically when the app starts (it used to
re-create these programs on every restart, which fought against deleting or
renaming them). Run it by hand, only if you want these starter programs
created in a brand-new, empty database:

    python3 seed.py
"""
from db import get_db, init_db

def seed():
    init_db()
    conn = get_db()
    cur = conn.cursor()

    existing = cur.execute("SELECT code FROM programs").fetchall()
    existing_codes = {row["code"] for row in existing}

    programs = [
        ("B1", "Bluesky 1 — Your Past", "Examines your past.", None),
        ("B2", "Bluesky 2 — Your Present", "Examines your present. Requires Bluesky 1.", "B1"),
        ("B3", "Bluesky 3 — Your Future", "Examines your future. Requires Bluesky 2.", "B2"),
        ("B4", "Squeeze — Bluesky Relationship Training", "Relationship training for couples. Standalone, no prerequisite.", None),
    ]

    code_to_id = {}
    for code, name, desc, _ in programs:
        if code in existing_codes:
            row = cur.execute("SELECT id FROM programs WHERE code = ?", (code,)).fetchone()
            code_to_id[code] = row["id"]
            continue
        cur.execute(
            "INSERT INTO programs (code, name, description) VALUES (?, ?, ?)",
            (code, name, desc),
        )
        code_to_id[code] = cur.lastrowid

    for code, name, desc, prereq in programs:
        if prereq:
            cur.execute(
                "UPDATE programs SET prerequisite_program_id = ? WHERE code = ?",
                (code_to_id[prereq], code),
            )

    conn.commit()
    conn.close()
    print("Seeded programs:", code_to_id)

if __name__ == "__main__":
    seed()
