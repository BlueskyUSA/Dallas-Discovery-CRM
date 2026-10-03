"""
Read-only helper: prints every row in the programs table (id, code, name)
so we can see the real codes behind whatever names the dashboard is
showing. Doesn't change anything.

Run with:
    python3 list_programs.py
"""
from db import get_db

conn = get_db()
rows = conn.execute("SELECT id, code, name FROM programs ORDER BY id").fetchall()
conn.close()

if not rows:
    print("No programs found in the database.")
else:
    for r in rows:
        print(f"id={r['id']!s:<4} code={r['code']!r:<10} name={r['name']!r}")
