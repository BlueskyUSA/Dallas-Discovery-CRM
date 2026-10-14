"""
Loads the "Relationship 2020" test team into Relationship Training #1:
creates any missing contacts (matched by full name -- never merged by email,
since Wayne and Elizabeth Harris share one) and adds them to the session's
Session Team with their duties. Nothing is emailed or texted.

Safe by default: with no flag it only SHOWS what it would do.

    python3 load_relationship_team.py                    # dry run, Relationship Training #1 only
    python3 load_relationship_team.py --apply            # for real (safe to run twice)
    python3 load_relationship_team.py --all              # dry run, EVERY session of every training
    python3 load_relationship_team.py --all --apply      # for real, every session (test use)
"""
import sys
from db import get_db

APPLY = "--apply" in sys.argv
ALL = "--all" in sys.argv
SESSION_CODE, SESSION_NUMBER = "D4", 1

# (name, cell, email, experience, [(role, duty), ...])
TEAM = [
    ("Kent Hafemann", "214-783-8011", "kent@blueskyusa.net", "", [("Lead Facilitator", None)]),
    ("Pamela Hafemann", "214-673-8546", "Phafemann@gmail.com", "", [("Support Facilitator", None)]),
    ("Danny Coburn", "469-394-3963", "DannyCoburn@live.com", "Exp",
     [("TA", "Team Captain"), ("TA", "Male Runner"), ("TA", "Troubleshooter / Misc"), ("TA", "Chairs")]),
    ("Lisa Coburn", "214-425-7366", "lisa@shophomegirl.com", "Exp", [("TA", "Female Runner")]),
    ("David Bryan", "214-454-4371", "david@familybryan.com", "Exp", [("TA", "Time Keeper"), ("TA", "Chairs")]),
    ("Toni Bryan", "214-418-8033", "toni@familybryan.com", "Exp", [("TA", "Lights")]),
    ("Wayne Harris", "903-267-9219", "jesusfeak76245@gmail.com", "New", [("TA", "Mail & Name Tags"), ("TA", "Chairs")]),
    ("Elizabeth Harris", "817-602-7363", "jesusfeak76245@gmail.com", "New", [("TA", "Mail & Name Tags")]),
    ("Byron Eldredge", "214-228-4177", "Mail@byroneldredge.com", "New", [("TA", "Signs / Security"), ("TA", "Chairs")]),
    ("Nicole Eldredge", "682-500-0047", "mail@nicoleeldredge.com", "New", [("TA", "Microphone 2"), ("TA", "Doors")]),
    ("Steve Dushack", "405-408-7503", "shackdog1222@yahoo.com", "New", [("TA", "Chair Counter"), ("TA", "Chairs")]),
    ("Angella Dushack", "405-408-7502", "Cmdushack@yahoo.com", "New", [("TA", "Microphone 1")]),
    ("Britiany Goss", "469-569-8737", "bgoss@discovery-training.com", "Exp", [("Support Team", None)]),
    ("Jeff Christie", "214-317-1488", "Christiesgolfranch@gmail.com", "Exp", [("Back-up TA", None)]),
    ("Christen Christie", "214-558-3783", "Crebecca33@gmail.com", "Exp", [("Back-up TA", None)]),
    ("Williams Lawrence", "479-530-4490", "Law773@hotmail.com", "New", [("Back-up TA", "Not confirmed")]),
    ("Victoria Lawrence", "479-366-3218", "vwilliams@gmail.com", "New", [("Back-up TA", "Not confirmed")]),
]

conn = get_db()
if ALL:
    cohorts = conn.execute(
        """SELECT c.id, p.code, c.session_number FROM cohorts c JOIN programs p ON p.id = c.program_id
           WHERE COALESCE(c.status, 'Scheduled') != 'Cancelled' ORDER BY p.code, c.session_number""").fetchall()
else:
    cohorts = conn.execute(
        """SELECT c.id, p.code, c.session_number FROM cohorts c JOIN programs p ON p.id = c.program_id
           WHERE p.code = ? AND c.session_number = ?""", (SESSION_CODE, SESSION_NUMBER)).fetchall()
if not cohorts:
    print(f"No matching session found. Schedule it first, then run this again.")
    sys.exit(1)
print(("EVERY session" if ALL else f"Session {SESSION_CODE} #{SESSION_NUMBER}") + f" ({len(cohorts)}) -- {'APPLYING' if APPLY else 'DRY RUN, nothing will be changed'}\n")

new_contacts = added = already = 0
for name, cell, email, exp, roles in TEAM:
    first, _, last = name.strip().partition(" ")
    row = conn.execute(
        "SELECT id FROM contacts WHERE LOWER(first_name) = ? AND LOWER(COALESCE(last_name, '')) = ?",
        (first.lower(), last.lower())).fetchone()
    if row:
        cid = row["id"]
    else:
        new_contacts += 1
        print(f"  {name}: NEW contact ({cell}, {email})")
        if APPLY:
            note = f"Relationship 2020 team ({exp})." if exp else "Relationship 2020 team."
            conn.execute(
                "INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, status) VALUES (?,?,?,?,?,'TA')",
                (first, last, email, cell, note))
            cid = conn.execute("SELECT MAX(id) AS m FROM contacts").fetchone()["m"]
        else:
            cid = None
    for co in cohorts:
        for role, duty in roles:
            if cid is not None and conn.execute(
                "SELECT 1 FROM cohort_staffing WHERE cohort_id = ? AND contact_id = ? AND role = ? AND COALESCE(duty, '') = ?",
                (co["id"], cid, role, duty or "")).fetchone():
                already += 1
                continue
            added += 1
            if APPLY:
                conn.execute("INSERT INTO cohort_staffing (cohort_id, contact_id, role, duty) VALUES (?,?,?,?)",
                             (co["id"], cid, role, duty))
if APPLY:
    conn.commit()
conn.close()
print(f"\n{new_contacts} new contact(s); {added} team assignment(s) {'added' if APPLY else 'to add'} across {len(cohorts)} session(s); {already} already there.")
if not APPLY:
    print("Nothing was changed. To do it for real, run it again with --apply added")
