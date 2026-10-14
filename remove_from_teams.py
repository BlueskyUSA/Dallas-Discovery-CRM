"""
Takes Keith Ashley and Denise Maily (also spelled Mayley) off EVERY session team and small-group
team. Their contact records are NOT touched, only their session assignments.

Safe by default: with no flag it only SHOWS what it would remove.

    python3 remove_from_teams.py          # dry run
    python3 remove_from_teams.py --apply  # for real
"""
import sys
from db import get_db

APPLY = "--apply" in sys.argv
PEOPLE = [("keith", {"ashley"}), ("denise", {"maily", "mayley", "mailey"})]

conn = get_db()
total = 0
for first, lasts in PEOPLE:
    for c in conn.execute("SELECT id, first_name, last_name FROM contacts WHERE LOWER(first_name) = ?", (first,)).fetchall():
        if (c["last_name"] or "").strip().lower() not in lasts:
            continue
        n1 = conn.execute("SELECT COUNT(*) AS n FROM cohort_staffing WHERE contact_id = ?", (c["id"],)).fetchone()["n"]
        n2 = conn.execute("SELECT COUNT(*) AS n FROM small_group_staffing WHERE contact_id = ?", (c["id"],)).fetchone()["n"]
        print(f"{c['first_name']} {c['last_name']} (contact #{c['id']}): {n1} session-team row(s), {n2} small-group row(s)")
        total += n1 + n2
        if APPLY:
            conn.execute("DELETE FROM cohort_staffing WHERE contact_id = ?", (c["id"],))
            conn.execute("DELETE FROM small_group_staffing WHERE contact_id = ?", (c["id"],))
if APPLY:
    conn.commit()
conn.close()
print(f"\n{total} row(s) {'removed' if APPLY else 'would be removed'}." + ("" if APPLY else " Nothing was changed. Run again with --apply to do it."))
