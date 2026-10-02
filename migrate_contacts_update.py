"""
One-time migration for changes to Contacts:
  1. Splits the old single "full_name" field into separate "first_name" and
     "last_name" fields.
  2. Splits the old single "phone" field into "cell_phone", "home_phone",
     and "business_phone" (your existing phone numbers move into cell_phone;
     home and business start out blank for you to fill in).
  3. Adds "partner_name" and "partner_cell" fields (blank until you fill
     them in — nothing to migrate here, they're brand new).
  4. Adds "emergency_contact_name", "emergency_contact_cell", and
     "emergency_contact_home" fields (also brand new, blank to start).
  5. Replaces "Referred By" (a dropdown linked to another contact) with a
     plain-text "Sponsor Name" field. If you had referred-by info saved,
     it's copied over as that contact's name.
  6. Adds "Sponsor Phone", "Sponsor Email", "Sponsor Street Address",
     "Sponsor City/State", and "Sponsor Zip" fields (brand new, blank to
     start).
  7. Adds "Street Address" and "Zip" fields for the trainee's own address
     (your existing City/State stays as-is).
  8. Reformats every phone number already saved (cell, home, business,
     partner, emergency contact, sponsor) to the consistent XXX-XXX-XXXX
     style, so old and new entries look the same. Numbers that aren't a
     standard 10-digit US number are left alone rather than guessed at.
  9. Splits "City/State" into separate "City" and "State" fields (both for
     the trainee and the sponsor), with State stored as a 2-letter
     abbreviation (e.g. TX). Existing "City, ST" values are split
     automatically; anything that doesn't clearly end in a 2-letter state
     is kept as-is in City with State left blank for you to fill in.
  10. Renames "Business phone" to "Work phone" (any existing business
      phone numbers move over automatically).
  11. Adds a "Personal information" section: Gender, Age, DOB, Ethnicity,
      Marital Status, Living with Partner?, Children (Ages), Occupation,
      Employer, Spiritual Orientation, Overall Health, Health Limitations,
      Education, Bluesky Attendance (If Applicable), D1/D2/D3 Month &
      Year, and Other Classes (all brand new, blank to start).
  12. Adds "Partner's Email" (blank to start), and adds the T4
      Relationship Profile questionnaire (a new table — created
      automatically the next time the app starts, no action needed here).

Safe to run more than once — it checks what's already there before changing
anything.

Run this ONCE, from inside your bluesky-crm folder, with the app stopped:

    python3 migrate_contacts_update.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "bluesky_crm.db")


def migrate():
    if not os.path.exists(DB_PATH):
        print("No bluesky_crm.db found here — nothing to migrate. "
              "(This is normal if you haven't run the app yet.)")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cols = [row[1] for row in cur.execute("PRAGMA table_info(contacts)").fetchall()]

    if "first_name" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN first_name TEXT")
        print("Added first_name column.")
    if "last_name" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN last_name TEXT")
        print("Added last_name column.")

    if "full_name" in cols:
        rows = cur.execute("SELECT id, full_name FROM contacts").fetchall()
        updated = 0
        for contact_id, full in rows:
            full = (full or "").strip()
            if not full:
                continue
            if " " in full:
                first, last = full.split(" ", 1)
            else:
                first, last = full, None
            cur.execute(
                "UPDATE contacts SET first_name = ?, last_name = ? WHERE id = ?",
                (first, last, contact_id),
            )
            updated += 1
        print(f"Split {updated} existing contact name(s) into first/last name.")

        try:
            cur.execute("ALTER TABLE contacts DROP COLUMN full_name")
            print("Removed the old full_name column.")
        except sqlite3.OperationalError as e:
            print(f"Could not remove the old full_name column (harmless — it's just "
                  f"left unused): {e}")
    else:
        print("full_name column already gone — nothing to split.")

    # --- phone split ---
    if "cell_phone" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN cell_phone TEXT")
        print("Added cell_phone column.")
    if "home_phone" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN home_phone TEXT")
        print("Added home_phone column.")
    if "work_phone" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN work_phone TEXT")
        print("Added work_phone column.")

    if "phone" in cols:
        cur.execute("UPDATE contacts SET cell_phone = phone WHERE phone IS NOT NULL AND phone != ''")
        print("Moved existing phone numbers into cell_phone.")
        try:
            cur.execute("ALTER TABLE contacts DROP COLUMN phone")
            print("Removed the old phone column.")
        except sqlite3.OperationalError as e:
            print(f"Could not remove the old phone column (harmless — it's just left unused): {e}")

    # --- partner fields (brand new, nothing to migrate) ---
    if "partner_name" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN partner_name TEXT")
        print("Added partner_name column.")
    if "partner_cell" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN partner_cell TEXT")
        print("Added partner_cell column.")
    if "partner_email" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN partner_email TEXT")
        print("Added partner_email column.")

    # --- emergency contact fields (brand new, nothing to migrate) ---
    if "emergency_contact_name" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN emergency_contact_name TEXT")
        print("Added emergency_contact_name column.")
    if "emergency_contact_cell" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN emergency_contact_cell TEXT")
        print("Added emergency_contact_cell column.")
    if "emergency_contact_home" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN emergency_contact_home TEXT")
        print("Added emergency_contact_home column.")

    # --- sponsor name (replaces referred_by_contact_id) ---
    if "sponsor_name" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN sponsor_name TEXT")
        print("Added sponsor_name column.")

    if "referred_by_contact_id" in cols:
        rows = cur.execute(
            "SELECT id, referred_by_contact_id FROM contacts WHERE referred_by_contact_id IS NOT NULL"
        ).fetchall()
        updated = 0
        for contact_id, ref_id in rows:
            ref_row = cur.execute(
                "SELECT first_name, last_name FROM contacts WHERE id = ?", (ref_id,)
            ).fetchone()
            if ref_row:
                first, last = ref_row
                name = (first or "").strip()
                if last:
                    name = f"{name} {last.strip()}".strip()
                if name:
                    cur.execute(
                        "UPDATE contacts SET sponsor_name = ? WHERE id = ?",
                        (name, contact_id),
                    )
                    updated += 1
        print(f"Copied {updated} existing 'referred by' link(s) into sponsor_name as text.")

        try:
            cur.execute("ALTER TABLE contacts DROP COLUMN referred_by_contact_id")
            print("Removed the old referred_by_contact_id column.")
        except sqlite3.OperationalError as e:
            print(f"Could not remove the old referred_by_contact_id column (harmless — "
                  f"it's just left unused): {e}")

    # --- additional sponsor contact fields (brand new, nothing to migrate) ---
    if "sponsor_phone" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN sponsor_phone TEXT")
        print("Added sponsor_phone column.")
    if "sponsor_email" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN sponsor_email TEXT")
        print("Added sponsor_email column.")
    if "sponsor_street_address" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN sponsor_street_address TEXT")
        print("Added sponsor_street_address column.")
    if "sponsor_zip" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN sponsor_zip TEXT")
        print("Added sponsor_zip column.")

    # --- trainee's own street address / zip (brand new, nothing to migrate) ---
    if "street_address" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN street_address TEXT")
        print("Added street_address column.")
    if "zip" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN zip TEXT")
        print("Added zip column.")

    # --- split City/State into separate City and State fields ---
    def split_city_state(value):
        value = (value or "").strip()
        if not value:
            return (None, None)
        if "," in value:
            city_part, state_part = value.rsplit(",", 1)
            city, state = city_part.strip(), state_part.strip()
        else:
            parts = value.rsplit(" ", 1)
            if len(parts) == 2:
                city, state = parts[0].strip(), parts[1].strip()
            else:
                return (value, None)
        if state and len(state) == 2 and state.isalpha():
            return (city or None, state.upper())
        # Doesn't look like a clean 2-letter state — keep the original text
        # as-is in City and leave State blank for you to fill in by hand.
        return (value, None)

    def migrate_city_state(old_col, new_city_col, new_state_col, label):
        if new_city_col not in cols:
            cur.execute(f"ALTER TABLE contacts ADD COLUMN {new_city_col} TEXT")
            print(f"Added {new_city_col} column.")
        if new_state_col not in cols:
            cur.execute(f"ALTER TABLE contacts ADD COLUMN {new_state_col} TEXT")
            print(f"Added {new_state_col} column.")
        if old_col in cols:
            rows = cur.execute(
                f"SELECT id, {old_col} FROM contacts WHERE {old_col} IS NOT NULL AND {old_col} != ''"
            ).fetchall()
            split_count = 0
            for contact_id, value in rows:
                city, state = split_city_state(value)
                cur.execute(
                    f"UPDATE contacts SET {new_city_col} = ?, {new_state_col} = ? WHERE id = ?",
                    (city, state, contact_id),
                )
                split_count += 1
            print(f"Split {split_count} existing {label} City/State value(s).")
            try:
                cur.execute(f"ALTER TABLE contacts DROP COLUMN {old_col}")
                print(f"Removed the old {old_col} column.")
            except sqlite3.OperationalError as e:
                print(f"Could not remove the old {old_col} column (harmless — it's "
                      f"just left unused): {e}")

    migrate_city_state("city_state", "city", "state", "trainee")
    migrate_city_state("sponsor_city_state", "sponsor_city", "sponsor_state", "sponsor")

    # --- rename Business phone to Work phone ---
    if "business_phone" in cols:
        cur.execute(
            "UPDATE contacts SET work_phone = business_phone "
            "WHERE business_phone IS NOT NULL AND business_phone != ''"
        )
        print("Moved existing Business phone numbers into Work phone.")
        try:
            cur.execute("ALTER TABLE contacts DROP COLUMN business_phone")
            print("Removed the old business_phone column.")
        except sqlite3.OperationalError as e:
            print(f"Could not remove the old business_phone column (harmless — it's "
                  f"just left unused): {e}")

    # --- reformat existing phone numbers to XXX-XXX-XXXX ---
    def reformat_phone(value):
        if not value:
            return value
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) != 10:
            return value  # not a standard US number — leave it as-is
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"

    phone_columns = [
        "cell_phone", "home_phone", "work_phone",
        "partner_cell", "emergency_contact_cell", "emergency_contact_home",
        "sponsor_phone",
    ]
    current_cols = [row[1] for row in cur.execute("PRAGMA table_info(contacts)").fetchall()]
    reformatted = 0
    for col in phone_columns:
        if col not in current_cols:
            continue
        rows = cur.execute(f"SELECT id, {col} FROM contacts WHERE {col} IS NOT NULL AND {col} != ''").fetchall()
        for contact_id, value in rows:
            new_value = reformat_phone(value)
            if new_value != value:
                cur.execute(f"UPDATE contacts SET {col} = ? WHERE id = ?", (new_value, contact_id))
                reformatted += 1
    if reformatted:
        print(f"Reformatted {reformatted} existing phone number(s) to XXX-XXX-XXXX.")
    else:
        print("Phone numbers already in XXX-XXX-XXXX format (or none to reformat).")

    # --- Personal information section (brand new, nothing to migrate) ---
    personal_columns = [
        ("gender", "TEXT"),
        ("age", "INTEGER"),
        ("dob", "TEXT"),
        ("ethnicity", "TEXT"),
        ("marital_status", "TEXT"),
        ("living_with_partner", "TEXT"),
        ("children_ages", "TEXT"),
        ("occupation", "TEXT"),
        ("employer", "TEXT"),
        ("spiritual_orientation", "TEXT"),
        ("overall_health", "TEXT"),
        ("health_limitations", "TEXT"),
        ("education", "TEXT"),
        ("bluesky_attendance", "TEXT"),
        ("d1_month_year", "TEXT"),
        ("d2_month_year", "TEXT"),
        ("d3_month_year", "TEXT"),
        ("other_classes", "TEXT"),
    ]
    for col_name, col_type in personal_columns:
        if col_name not in cols:
            cur.execute(f"ALTER TABLE contacts ADD COLUMN {col_name} {col_type}")
            print(f"Added {col_name} column.")

    # first_name is required going forward; make sure no existing row is blank.
    blanks = cur.execute(
        "SELECT COUNT(*) FROM contacts WHERE first_name IS NULL OR first_name = ''"
    ).fetchone()[0]
    if blanks:
        print(f"Warning: {blanks} contact(s) ended up with a blank first name — "
              f"you may want to fix those up by hand in the Contacts list.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
