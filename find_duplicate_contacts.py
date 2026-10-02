"""
Read-only report: lists groups of contacts that share the same email
address. Useful after the discovery-form dedup fix (which only prevents
NEW duplicates going forward) to find and clean up contacts that were
already duplicated before that fix went in -- e.g. someone who filled out
the /discovery form and got a second, separate contact record even though
they were already in the CRM.

Doesn't change anything. Run from Render's Shell:

    python3 find_duplicate_contacts.py

For each duplicate group it prints enough to help you decide which record
to keep (id, name, status, whether they have a photo, whether they're
linked to a staff/leadership login, when they were created, and how long
their notes are). Once you know which id should "win", use:

    python3 merge_contacts.py <keeper_id> <duplicate_id>

to fold the duplicate into the keeper.
"""
from db import get_db


def find_duplicates():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """SELECT email FROM contacts
           WHERE email IS NOT NULL AND email != ''
           GROUP BY email HAVING COUNT(*) > 1
           ORDER BY email"""
    )
    dup_emails = [row["email"] for row in cur.fetchall()]

    if not dup_emails:
        print("No duplicate contacts found -- every email in the CRM is unique.")
        conn.close()
        return

    print(f"Found {len(dup_emails)} email address(es) shared by more than one contact:\n")

    for email in dup_emails:
        cur.execute(
            """SELECT c.id, c.first_name, c.last_name, c.status, c.created_at,
                      c.notes,
                      (SELECT COUNT(*) FROM contact_photos p WHERE p.contact_id = c.id) AS has_photo,
                      (SELECT COUNT(*) FROM staff s WHERE s.contact_id = c.id) AS has_staff_login
               FROM contacts c
               WHERE c.email = ?
               ORDER BY c.id""",
            (email,),
        )
        rows = cur.fetchall()
        print(f"--- {email} ({len(rows)} contacts) ---")
        for r in rows:
            name = f"{r['first_name']} {r['last_name'] or ''}".strip()
            notes_len = len(r["notes"] or "")
            print(
                f"  id={r['id']:<5} {name:<30} status={r['status']:<18} "
                f"created={r['created_at']:<20} photo={'yes' if r['has_photo'] else 'no':<4} "
                f"staff_login={'yes' if r['has_staff_login'] else 'no':<4} notes_len={notes_len}"
            )
        print()

    print("To merge a duplicate into the contact you want to keep, run:")
    print("    python3 merge_contacts.py <keeper_id> <duplicate_id>")
    conn.close()


if __name__ == "__main__":
    find_duplicates()
