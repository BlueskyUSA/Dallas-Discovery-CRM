"""
Folds a duplicate contact into the contact you want to keep ("the keeper"),
then deletes the duplicate. Use this after find_duplicate_contacts.py shows
you which ids are duplicates of each other.

What it moves from the duplicate onto the keeper:
  - Photo: only if the keeper doesn't already have one.
  - Notes: appended onto the keeper's notes (nothing is lost).
  - profile_token: only if the keeper doesn't already have one -- this
    keeps any long-form link already emailed out working, since it now
    resolves to the keeper.
  - Enrollments, donations, cohort/small-group staffing roles, staff
    login link, and contract records: reassigned to the keeper's id,
    *unless* the keeper already has a conflicting record there (e.g. a
    contract, since a contact can only have one) -- those are left on the
    duplicate and printed out so you can look at them by hand.

Nothing else on the keeper is touched -- profile fields (address, phone,
etc.) are NOT overwritten by the duplicate's data, only added where the
keeper was blank. If you'd rather the duplicate's answers win, edit the
keeper by hand afterward in the CRM.

Safe by default: running it with just the two ids shows you exactly what
it WOULD do, without changing anything. Add --apply to actually do it.

Usage (from Render's Shell):

    python3 merge_contacts.py <keeper_id> <duplicate_id>            # dry run
    python3 merge_contacts.py <keeper_id> <duplicate_id> --apply    # for real
"""
import sys
from db import get_db

# columns on `contacts` that get filled in on the keeper ONLY if the
# keeper's value is currently blank
FILL_IF_BLANK_FIELDS = [
    "last_name", "cell_phone", "home_phone", "work_phone", "street_address",
    "city", "state", "zip", "gender", "age", "dob", "ethnicity",
    "marital_status", "occupation", "employer", "spiritual_orientation",
    "pseudonym",
]

# child tables that reference contacts(id), and whether moving a row to
# an id that already has one there would violate a uniqueness constraint
CHILD_TABLES = [
    ("cohort_staffing", "contact_id", False),
    ("small_group_staffing", "contact_id", False),
    ("enrollments", "contact_id", False),
    ("enrollments", "enrolled_by_contact_id", False),
    ("donations", "contact_id", False),
    ("contracts", "contact_id", True),
    ("contracts", "led_by_contact_id", False),
    ("contracts", "assisted_by_contact_id", False),
    ("staff", "contact_id", True),
]


def merge(keeper_id, duplicate_id, apply_changes):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM contacts WHERE id = ?", (keeper_id,))
    keeper = cur.fetchone()
    cur.execute("SELECT * FROM contacts WHERE id = ?", (duplicate_id,))
    dup = cur.fetchone()

    if not keeper:
        print(f"No contact with id {keeper_id}.")
        return
    if not dup:
        print(f"No contact with id {duplicate_id}.")
        return

    mode = "APPLYING" if apply_changes else "DRY RUN (add --apply to actually do this)"
    print(f"{mode}: merging duplicate #{duplicate_id} ({dup['first_name']} {dup['last_name'] or ''}) "
          f"into keeper #{keeper_id} ({keeper['first_name']} {keeper['last_name'] or ''})\n")

    # 1. fill-if-blank fields
    fields_to_fill = {}
    for field in FILL_IF_BLANK_FIELDS:
        if not keeper[field] and dup[field]:
            fields_to_fill[field] = dup[field]
    if fields_to_fill:
        print("Will fill these blank fields on the keeper from the duplicate:")
        for k, v in fields_to_fill.items():
            print(f"    {k} = {v!r}")
        if apply_changes:
            set_clause = ", ".join(f"{k} = ?" for k in fields_to_fill)
            cur.execute(f"UPDATE contacts SET {set_clause} WHERE id = ?",
                        list(fields_to_fill.values()) + [keeper_id])
    else:
        print("No blank keeper fields to fill in from the duplicate.")

    # 2. notes
    if dup["notes"]:
        print("Will append the duplicate's notes onto the keeper's notes.")
        if apply_changes:
            combined = (keeper["notes"] + "\n\n" + dup["notes"]) if keeper["notes"] else dup["notes"]
            cur.execute("UPDATE contacts SET notes = ? WHERE id = ?", (combined, keeper_id))

    # 3. profile_token (only if keeper doesn't have one)
    if dup["profile_token"] and not keeper["profile_token"]:
        print(f"Will move profile_token {dup['profile_token']!r} to the keeper "
              "(so any link already emailed out keeps working).")
        if apply_changes:
            # profile_token is UNIQUE, so clear it off the duplicate first --
            # it's about to be deleted anyway -- before putting it on the keeper
            token = dup["profile_token"]
            cur.execute("UPDATE contacts SET profile_token = NULL WHERE id = ?", (duplicate_id,))
            cur.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (token, keeper_id))

    # 4. photo (only if keeper doesn't have one)
    cur.execute("SELECT * FROM contact_photos WHERE contact_id = ?", (keeper_id,))
    keeper_photo = cur.fetchone()
    cur.execute("SELECT * FROM contact_photos WHERE contact_id = ?", (duplicate_id,))
    dup_photo = cur.fetchone()
    if dup_photo and not keeper_photo:
        print("Will move the duplicate's photo to the keeper.")
        if apply_changes:
            cur.execute(
                "INSERT INTO contact_photos (contact_id, data, content_type) VALUES (?, ?, ?)",
                (keeper_id, dup_photo["data"], dup_photo["content_type"]),
            )
    elif dup_photo and keeper_photo:
        print("Keeper already has a photo -- leaving the duplicate's photo alone "
              "(it will be deleted with the duplicate contact).")

    # 5. child table records
    left_behind = []
    for table, column, unique_per_contact in CHILD_TABLES:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (duplicate_id,))
        count = cur.fetchone()["n"]
        if not count:
            continue
        if unique_per_contact:
            cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (keeper_id,))
            if cur.fetchone()["n"]:
                left_behind.append((table, column, count))
                continue
        print(f"Will move {count} row(s) in {table}.{column} to the keeper.")
        if apply_changes:
            cur.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (keeper_id, duplicate_id))

    if left_behind:
        print("\nCOULD NOT move these (keeper already has a conflicting record) -- "
              "left on the duplicate, review by hand before deleting it:")
        for table, column, count in left_behind:
            print(f"    {table}.{column}: {count} row(s)")
        print(f"\nNot deleting duplicate #{duplicate_id} while records are left behind on it.")
        conn.commit() if apply_changes else None
        conn.close()
        return

    # 6. delete the duplicate (its contact_photos row, if any, cascades via FK-less
    # manual cleanup below since contact_photos.contact_id has no ON DELETE CASCADE)
    print(f"Will delete duplicate contact #{duplicate_id} and its photo row (if any).")
    if apply_changes:
        cur.execute("DELETE FROM contact_photos WHERE contact_id = ?", (duplicate_id,))
        cur.execute("DELETE FROM contacts WHERE id = ?", (duplicate_id,))
        conn.commit()
        print(f"\nDone -- #{duplicate_id} merged into #{keeper_id} and deleted.")
    else:
        print("\nNothing changed (dry run). Re-run with --apply to do this for real.")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 merge_contacts.py <keeper_id> <duplicate_id> [--apply]")
        sys.exit(1)
    keeper_id = int(sys.argv[1])
    duplicate_id = int(sys.argv[2])
    apply_changes = "--apply" in sys.argv[3:]
    if keeper_id == duplicate_id:
        print("keeper_id and duplicate_id must be different contacts.")
        sys.exit(1)
    merge(keeper_id, duplicate_id, apply_changes)
