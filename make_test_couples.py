"""
Makes clearly-marked DUMMY couples on Relationship Training #1 so you can try registration, payment,
letters and the questionnaire. Every test person's email is a "+" address of the one below, so any
test email lands in YOUR inbox -- nothing can reach a real person. Nothing is sent by this script.

    python3 make_test_couples.py            # dry run, shows what it would add
    python3 make_test_couples.py --apply    # adds them (safe to run twice)
    python3 make_test_couples.py --remove   # dry run of the clean-up
    python3 make_test_couples.py --remove --apply   # deletes ONLY the test people made here

Test people are named "Test<Name>" with the note "TEST DATA (make_test_couples.py)".
"""
import sys
from db import get_db

APPLY = "--apply" in sys.argv
REMOVE = "--remove" in sys.argv
BASE_USER, BASE_DOMAIN = "khres53", "gmail.com"      # test emails: khres53+tc1a@gmail.com ...
SESSION_CODE, SESSION_NUMBER = "D4", 1
NOTE = "TEST DATA (make_test_couples.py)"

# (couple tag, first A, first B, state)  state: new / paid / half  -- single has no partner
COUPLES = [
    ("tc1", "Ann", "Adam", "new"),     # just registered: nothing done yet
    ("tc2", "Beth", "Ben", "paid"),    # couple has paid (both show paid)
    ("tc3", "Cara", "Cole", "half"),   # paid, and one partner finished the questionnaire
]
SINGLE = ("tc4", "Dana")               # one person, no partner

conn = get_db()
for stmt in ("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS partner_enrollment_id INTEGER",
             "ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS questionnaire_token TEXT"):
    conn.execute(stmt)
conn.commit()

if REMOVE:
    rows = conn.execute("SELECT id FROM contacts WHERE notes = ?", (NOTE,)).fetchall()
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
            try:
                conn.execute("DELETE FROM session_letters_sent WHERE contact_id = ?", (cid,)); conn.commit()
            except Exception:
                conn.rollback()
            conn.execute("DELETE FROM enrollments WHERE contact_id = ?", (cid,))
            conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))
        conn.commit()
        print("Done.")
    sys.exit(0)

co = conn.execute(
    """SELECT c.id FROM cohorts c JOIN programs p ON p.id = c.program_id
       WHERE p.code = ? AND c.session_number = ?""", (SESSION_CODE, SESSION_NUMBER)).fetchone()
if not co:
    print("Relationship Training #1 isn't scheduled yet. Schedule it, then run this again.")
    sys.exit(1)
cohort_id = co["id"]
print(f"Adding test people to {SESSION_CODE} #{SESSION_NUMBER} -- {'APPLYING' if APPLY else 'DRY RUN, nothing changed'}\n")


def make_person(tag, suffix, first):
    last = "Test" + tag.upper()[2:]
    email = f"{BASE_USER}+{tag}{suffix}@{BASE_DOMAIN}"
    row = conn.execute("SELECT id FROM contacts WHERE email = ? AND notes = ?", (email, NOTE)).fetchone()
    if row:
        return row["id"], False
    if not APPLY:
        return None, True
    conn.execute("INSERT INTO contacts (first_name, last_name, email, notes, status) VALUES (?,?,?,?,'Registered')",
                 (first, last, email, NOTE))
    conn.commit()
    return conn.execute("SELECT MAX(id) AS m FROM contacts").fetchone()["m"], True


def enroll(contact_id):
    if not contact_id:
        return None
    row = conn.execute("SELECT id FROM enrollments WHERE contact_id = ? AND cohort_id = ?", (contact_id, cohort_id)).fetchone()
    if row:
        return row["id"]
    conn.execute("INSERT INTO enrollments (contact_id, cohort_id) VALUES (?,?)", (contact_id, cohort_id))
    conn.commit()
    return conn.execute("SELECT MAX(id) AS m FROM enrollments").fetchone()["m"]


for tag, a, b, state in COUPLES:
    ca, na = make_person(tag, "a", a)
    cb, nb = make_person(tag, "b", b)
    print(f"  Couple {a} & {b} (Test{tag.upper()[2:]}): {state}  {'NEW' if (na or nb) else 'already there'}")
    ea, eb = enroll(ca), enroll(cb)
    if APPLY and ea and eb:
        conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (eb, ea))
        conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (ea, eb))
        if state in ("paid", "half"):
            conn.execute("UPDATE enrollments SET payment_completed_at = date('now') WHERE id IN (?,?)", (ea, eb))
        if state == "half":
            conn.execute("UPDATE enrollments SET questionnaire_completed_at = date('now') WHERE id = ?", (ea,))
        conn.commit()

tag, first = SINGLE
cs, ns = make_person(tag, "a", first)
print(f"  Single {first} (Test{tag.upper()[2:]}): no partner  {'NEW' if ns else 'already there'}")
enroll(cs)
print("\nDone. Nothing was emailed." if APPLY else "\nDry run only. Add --apply to create them.")
