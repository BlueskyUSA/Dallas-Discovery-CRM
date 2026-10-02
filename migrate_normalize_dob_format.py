"""
One-time cleanup: the DOB field used to be a browser date picker, which
stores dates as YYYY-MM-DD (e.g. "1953-08-15"). It's now a plain text
field so people can type a birth date directly (e.g. "08/15/1953")
instead of scrolling a calendar back decades. This migration rewrites any
already-saved YYYY-MM-DD values to that same MM/DD/YYYY format, so every
contact's DOB -- old or new -- looks the same.

Safe to run more than once: only rows that still look like YYYY-MM-DD get
touched; anything already in MM/DD/YYYY (or anything else) is left alone.

Run from Render's Shell:

    python3 migrate_normalize_dob_format.py
"""
import re
from datetime import date
from db import get_db

ISO_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def migrate():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, dob FROM contacts WHERE dob IS NOT NULL AND dob != ''")
    rows = cur.fetchall()

    updated = 0
    skipped_invalid = 0
    for row in rows:
        match = ISO_DATE_PATTERN.match(row["dob"])
        if not match:
            continue  # already MM/DD/YYYY, or some other free text -- leave it
        year, month, day = match.groups()
        try:
            parsed = date(int(year), int(month), int(day))
        except ValueError:
            skipped_invalid += 1
            print(f"  Contact #{row['id']}: dob {row['dob']!r} isn't a valid date -- left as-is, please check by hand.")
            continue
        new_value = parsed.strftime("%m/%d/%Y")
        cur.execute("UPDATE contacts SET dob = ? WHERE id = ?", (new_value, row["id"]))
        updated += 1

    conn.commit()
    conn.close()
    print(f"\nDone. Converted {updated} contact(s) from YYYY-MM-DD to MM/DD/YYYY.")
    if skipped_invalid:
        print(f"{skipped_invalid} contact(s) had an unparseable dob and were left untouched -- see above.")


if __name__ == "__main__":
    migrate()
