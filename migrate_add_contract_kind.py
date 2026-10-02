"""
Splits the single "Contract" record per contact into multiple kinds, so a
contact can have a D1 Contract, a D2 "Your Poem", and a D6 "Spiritual
Contract" recorded separately instead of just one generic contract.

What it does:
  - Adds a `kind` column to `contracts` (defaulting existing rows to 'D1',
    since the Contract section that already existed was for D1 training).
  - Replaces the old UNIQUE(contact_id) constraint (one contract per
    contact, period) with UNIQUE(contact_id, kind) (one contract per
    contact PER KIND), so the same contact can now have a D1, D2, and D6
    row side by side.

Existing contract data is NOT lost -- every contract already on file
becomes that contact's D1 entry.

Safe to run more than once. Run from Render's Shell:

    python3 migrate_add_contract_kind.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'contracts' AND column_name = 'kind'"
    )
    if not cur.fetchall():
        cur.execute("ALTER TABLE contracts ADD COLUMN kind TEXT NOT NULL DEFAULT 'D1'")
        conn.commit()
        print("Added kind column to contracts (existing contracts default to 'D1').")
    else:
        print("kind column already exists on contracts.")

    # Find and drop the old one-contract-per-contact UNIQUE constraint (its
    # auto-generated name varies, so look it up rather than hardcoding it).
    cur.execute(
        """SELECT tc.constraint_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name
           WHERE tc.table_name = 'contracts'
             AND tc.constraint_type = 'UNIQUE'
             AND kcu.column_name = 'contact_id'
           GROUP BY tc.constraint_name
           HAVING COUNT(*) = 1"""  # the single-column (contact_id) constraint, not the new 2-column one
    )
    old_constraints = [row["constraint_name"] for row in cur.fetchall()]
    for name in old_constraints:
        cur.execute(f"ALTER TABLE contracts DROP CONSTRAINT {name}")
        conn.commit()
        print(f"Dropped old one-per-contact constraint ({name}).")

    cur.execute(
        "SELECT constraint_name FROM information_schema.table_constraints "
        "WHERE table_name = 'contracts' AND constraint_name = 'contracts_contact_id_kind_key'"
    )
    if not cur.fetchall():
        cur.execute("ALTER TABLE contracts ADD CONSTRAINT contracts_contact_id_kind_key UNIQUE (contact_id, kind)")
        conn.commit()
        print("Added new UNIQUE(contact_id, kind) constraint.")
    else:
        print("UNIQUE(contact_id, kind) constraint already exists.")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    migrate()
