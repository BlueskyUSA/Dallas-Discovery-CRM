"""
Makes clearly-marked DUMMY duplicate contacts so you can try the Possible duplicates page and the Merge button
without touching any real person. Every test email is a "+" address of khres53@gmail.com, so nothing can reach a real person.
Nothing is emailed by this script.

    python3 make_test_duplicates.py                  # dry run, shows what it would add
    python3 make_test_duplicates.py --apply          # adds them (safe to run twice)
    python3 make_test_duplicates.py --remove         # dry run of the clean-up
    python3 make_test_duplicates.py --remove --apply # deletes ONLY the test people made here (merged or not)

Test people have the last name "TestDupe..." and the note "TEST DATA (make_test_duplicates.py)".
"""
import sys
from db import get_db

APPLY = "--apply" in sys.argv
REMOVE = "--remove" in sys.argv
NOTE = "TEST DATA (make_test_duplicates.py)"

# (first, last, email, cell, extra note, enroll in D4 #1)
PEOPLE = [
    # pair 1: same email -- one has the phone, one is signed up for a session
    ("Alex", "TestDupeOne", "khres53+dup1@gmail.com", None, "", True),
    ("Alexander", "TestDupeOne", "khres53+dup1@gmail.com", "214-555-0101", "Second record with the phone number.", False),
    # pair 2: same name, different emails (the second email should end up as an extra email)
    ("Beth", "TestDupeTwo", "khres53+dup2a@gmail.com", None, "", False),
    ("Beth", "TestDupeTwo", "khres53+dup2b@gmail.com", "214-555-0102", "", False),
    # pair 3: different people who share one phone (try 'Not the same person')
    ("Cara", "TestDupeThree", "khres53+dup3a@gmail.com", "214-555-0103", "", False),
    ("Cole", "TestDupeThree", "khres53+dup3b@gmail.com", "214-555-0103", "", False),
]

conn = get_db()

if REMOVE:
    rows = conn.execute("SELECT id FROM contacts WHERE last_name LIKE ?", ("TestDupe%",)).fetchall()
    print(f"{len(rows)} test contact(s) found -- {'DELETING' if APPLY else 'DRY RUN, nothing changed'}")
    if APPLY:
        for r in rows:
            cid = r["id"]
            for e in conn.execute("SELECT id FROM enrollments WHERE contact_id = ?", (cid,)).fetchall():
                for tbl, col in (("training_profiles", "enrollment_id"), ("feedback_responses", "enrollment_id")):
                    try:
                        conn.execute(f"DELETE FROM {tbl} WHERE {col} = ?", (e["id"],)); conn.commit()
                    except Exception:
                        conn.rollback()
                conn.execute("UPDATE enrollments SET partner_enrollment_id = NULL WHERE partner_enrollment_id = ?", (e["id"],))
            for tbl in ("session_letters_sent", "contact_messages", "contact_photos", "contact_list_members", "enrollments"):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE contact_id = ?", (cid,)); conn.commit()
                except Exception:
                    conn.rollback()
            try:
                conn.execute("DELETE FROM contact_dupe_ignore WHERE a = ? OR b = ?", (cid, cid)); conn.commit()
            except Exception:
                conn.rollback()
            conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))
            conn.commit()
        print("Done.")
    sys.exit(0)

co = conn.execute("""SELECT c.id FROM cohorts c JOIN programs p ON p.id = c.program_id
                     WHERE p.code = 'D4' AND c.session_number = 1""").fetchone()
print(f"Adding test duplicates -- {'APPLYING' if APPLY else 'DRY RUN, nothing changed'}\n")
for first, last, email, cell, extra, enroll in PEOPLE:
    exists = conn.execute("SELECT id FROM contacts WHERE first_name = ? AND last_name = ? AND email = ?", (first, last, email)).fetchone()
    print(f"  {first} {last}  {email}  {cell or ''}  {'already there' if exists else 'NEW'}")
    if exists or not APPLY:
        continue
    note = NOTE + ("\n" + extra if extra else "")
    conn.execute("INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, status) VALUES (?,?,?,?,?,'Interested Party')",
                 (first, last, email, cell, note))
    conn.commit()
    cid = conn.execute("SELECT MAX(id) AS m FROM contacts").fetchone()["m"]
    if enroll and co:
        conn.execute("INSERT INTO enrollments (contact_id, cohort_id) VALUES (?,?)", (cid, co["id"]))
        conn.commit()
print("\nDone. Nothing was emailed." if APPLY else "\nDry run only. Add --apply to create them.")
