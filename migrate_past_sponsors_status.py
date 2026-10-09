"""One-time, safe to re-run. Sets status to 'Sponsor' for anyone on the "Past sponsors" list who is
still marked 'Interested Party', so past sponsors are not mistaken for new people to reach out to.
Anyone with a different status (Volunteer, TA, Donor, etc.) is left alone.

Run in the Render Shell:

    python3 migrate_past_sponsors_status.py
"""
from db import get_db


def main():
    conn = get_db()
    rows = conn.execute(
        """SELECT c.id FROM contacts c
           JOIN contact_list_members m ON m.contact_id = c.id
           JOIN contact_lists l ON l.id = m.list_id
           WHERE LOWER(l.name) = 'past sponsors' AND c.status = 'Interested Party'"""
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE contacts SET status = 'Sponsor' WHERE id = ?", (r["id"],))
    conn.commit()
    conn.close()
    print(f"Done. Changed {len(rows)} contact(s) from Interested Party to Sponsor.")


if __name__ == "__main__":
    main()
