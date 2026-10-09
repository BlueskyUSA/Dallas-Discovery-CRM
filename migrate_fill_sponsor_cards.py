"""One-time, safe to re-run: for people whose session roster already names a Sponsor
(the person who brought them in) but whose Sponsor details card is still empty, copy
the sponsor's name, phone, email and address onto the card. Never overwrites a card
that already has a name. Run in the Render Shell:

    python3 migrate_fill_sponsor_cards.py
"""
from db import get_db


def fill_card(conn, trainee_id, sponsor_id):
    t = conn.execute("SELECT sponsor_name FROM contacts WHERE id = ?", (trainee_id,)).fetchone()
    if not t or (t["sponsor_name"] or "").strip():
        return False
    s = conn.execute(
        """SELECT TRIM(first_name || ' ' || COALESCE(last_name, '')) AS full_name, email, cell_phone,
                  home_phone, work_phone, street_address, street_address_2, city, state, zip
           FROM contacts WHERE id = ?""",
        (sponsor_id,),
    ).fetchone()
    if not s:
        return False
    conn.execute(
        """UPDATE contacts SET sponsor_name = ?, sponsor_phone = ?, sponsor_email = ?,
                               sponsor_street_address = ?, sponsor_street_address_2 = ?,
                               sponsor_city = ?, sponsor_state = ?, sponsor_zip = ?
           WHERE id = ?""",
        (s["full_name"], s["cell_phone"] or s["home_phone"] or s["work_phone"], s["email"],
         s["street_address"], s["street_address_2"], s["city"], s["state"], s["zip"], trainee_id),
    )
    return True


def main():
    conn = get_db()
    rows = conn.execute(
        """SELECT e.contact_id, e.enrolled_by_contact_id, MAX(e.id) AS last_enr
           FROM enrollments e
           WHERE e.enrolled_by_contact_id IS NOT NULL AND e.contact_id != e.enrolled_by_contact_id
           GROUP BY e.contact_id, e.enrolled_by_contact_id
           ORDER BY last_enr"""
    ).fetchall()
    filled = 0
    for r in rows:
        if fill_card(conn, r["contact_id"], r["enrolled_by_contact_id"]):
            filled += 1
    conn.commit()
    conn.close()
    print(f"Done. Filled {filled} Sponsor details card(s).")


if __name__ == "__main__":
    main()
