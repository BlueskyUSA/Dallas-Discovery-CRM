"""One-time, safe to re-run. Adds the contacts.sponsor_contact_id link (so a Sponsor typed on a
contact's Sponsor details card can count toward the Sponsor pipeline) and links cards that
already have a Sponsor name matching EXACTLY ONE contact's full name. Cards whose name matches
nobody, or more than one person, are left alone (they stay plain text).

Run in the Render Shell:

    python3 migrate_add_sponsor_link.py
"""
from db import get_db


def main():
    conn = get_db()
    has_col = conn.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = 'contacts' AND column_name = 'sponsor_contact_id'"""
    ).fetchone()
    if not has_col:
        conn.execute("ALTER TABLE contacts ADD COLUMN sponsor_contact_id INTEGER REFERENCES contacts(id)")
        conn.commit()
        print("Added contacts.sponsor_contact_id.")
    else:
        print("contacts.sponsor_contact_id already exists.")

    names = {}
    for r in conn.execute("SELECT id, TRIM(first_name || ' ' || COALESCE(last_name, '')) AS n FROM contacts").fetchall():
        names.setdefault((r["n"] or "").strip().lower(), []).append(r["id"])
    linked = unmatched = ambiguous = 0
    for r in conn.execute(
        """SELECT id, sponsor_name FROM contacts
           WHERE sponsor_name IS NOT NULL AND TRIM(sponsor_name) != '' AND sponsor_contact_id IS NULL"""
    ).fetchall():
        matches = [i for i in names.get(r["sponsor_name"].strip().lower(), []) if i != r["id"]]
        if len(matches) == 1:
            conn.execute("UPDATE contacts SET sponsor_contact_id = ? WHERE id = ?", (matches[0], r["id"]))
            linked += 1
        elif matches:
            ambiguous += 1
        else:
            unmatched += 1
    conn.commit()
    conn.close()
    print(f"Linked {linked} Sponsor name(s) to contacts. Left as plain text: {unmatched} with no matching contact, {ambiguous} matching more than one.")
    print("Done.")


if __name__ == "__main__":
    main()
