"""
One-time check: is the Render Postgres database empty, or does it already
have the CRM's tables in it?

Doesn't change anything -- just looks and reports. Safe to run any time.

Run this from your Mac, inside the bluesky-crm project folder, with your
normal Python environment (the one that already has psycopg installed):

    python3 check_render_db.py "postgresql://<user>:<password>@<host>.render.com/<dbname>?sslmode=require"

(That's the EXTERNAL database URL from Render's Postgres page -- the one
that ends in ".ohio-postgres.render.com/blueskycrm", not the short internal
one. Use sslmode=require since you're connecting from outside Render.)
"""
import sys

import psycopg


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 check_render_db.py \"<external database URL>\"")
        sys.exit(1)

    url = sys.argv[1]
    conn = psycopg.connect(url)
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )
    tables = [r[0] for r in cur.fetchall()]

    if not tables:
        print("This database is EMPTY -- no tables yet.")
        print("Next step: run schema_postgres.sql against it, then every migrate_*.py script.")
    else:
        print(f"This database already has {len(tables)} table(s):")
        for t in tables:
            print(f"  - {t}")
        if "staff" in tables:
            cur.execute("SELECT COUNT(*) FROM staff")
            print(f"\nstaff table has {cur.fetchone()[0]} row(s).")
        if "contacts" in tables:
            cur.execute("SELECT COUNT(*) FROM contacts")
            print(f"contacts table has {cur.fetchone()[0]} row(s).")

    conn.close()


if __name__ == "__main__":
    main()
