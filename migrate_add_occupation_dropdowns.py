"""
One-time migration: splits the old free-text "Occupation" field into two
dropdowns:
  - Employment Status (new field: employment_status) — Employed Full-Time,
    Employed Part-Time, Self-Employed / Business Owner, Retired, Student,
    Homemaker, Unemployed, Disabled / Unable to Work, Prefer not to say.
  - Occupation Category (still stored in the existing occupation column) —
    Management / Business, Professional (Medical, Legal, Education, etc.),
    Sales, Skilled Trades, Service, Healthcare, Education, Ministry /
    Clergy, Retired, Student, Homemaker, Prefer to self-describe — backed by
    a new occupation_self_description column for the self-describe box.

What it does to existing data:
  - Adds the employment_status and occupation_self_description columns if
    not already there.
  - employment_status starts blank for everyone (it's a brand-new field
    with nothing to derive it from) — nothing to migrate there.
  - Best-effort maps existing free-text Occupation values onto the new
    Occupation Category list (e.g. "teacher" -> "Education", "nurse" ->
    "Healthcare", "electrician" -> "Skilled Trades").
  - Anything that doesn't clearly match is preserved, not discarded:
    occupation is set to "Prefer to self-describe" and the original text is
    copied into occupation_self_description.
  - Blank/empty values are left blank.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_occupation_dropdowns.py
"""
from db import get_db

_ALIASES = {
    "manager": "Management / Business",
    "management": "Management / Business",
    "business owner": "Management / Business",
    "executive": "Management / Business",
    "doctor": "Professional (Medical, Legal, Education, etc.)",
    "physician": "Professional (Medical, Legal, Education, etc.)",
    "lawyer": "Professional (Medical, Legal, Education, etc.)",
    "attorney": "Professional (Medical, Legal, Education, etc.)",
    "engineer": "Professional (Medical, Legal, Education, etc.)",
    "accountant": "Professional (Medical, Legal, Education, etc.)",
    "sales": "Sales",
    "salesman": "Sales",
    "salesperson": "Sales",
    "electrician": "Skilled Trades",
    "plumber": "Skilled Trades",
    "carpenter": "Skilled Trades",
    "mechanic": "Skilled Trades",
    "contractor": "Skilled Trades",
    "server": "Service",
    "waiter": "Service",
    "waitress": "Service",
    "customer service": "Service",
    "nurse": "Healthcare",
    "healthcare worker": "Healthcare",
    "medical assistant": "Healthcare",
    "teacher": "Education",
    "professor": "Education",
    "educator": "Education",
    "pastor": "Ministry / Clergy",
    "minister": "Ministry / Clergy",
    "priest": "Ministry / Clergy",
    "clergy": "Ministry / Clergy",
    "retired": "Retired",
    "student": "Student",
    "homemaker": "Homemaker",
    "stay at home": "Homemaker",
    "stay-at-home": "Homemaker",
}
_KNOWN_LABELS = {
    "Management / Business", "Professional (Medical, Legal, Education, etc.)",
    "Sales", "Skilled Trades", "Service", "Healthcare", "Education",
    "Ministry / Clergy", "Retired", "Student", "Homemaker",
    "Prefer to self-describe",
}


def classify_occupation(raw):
    value = (raw or "").strip()
    if not value:
        return (None, None)
    if value in _KNOWN_LABELS:
        return (value, None)
    mapped = _ALIASES.get(value.lower())
    if mapped:
        return (mapped, None)
    return ("Prefer to self-describe", value)


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    if "employment_status" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN employment_status TEXT")
        print("Added employment_status column.")
    else:
        print("employment_status column already exists.")

    if "occupation_self_description" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN occupation_self_description TEXT")
        print("Added occupation_self_description column.")
    else:
        print("occupation_self_description column already exists.")

    rows = cur.execute(
        "SELECT id, occupation FROM contacts WHERE occupation IS NOT NULL AND occupation != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        new_value, self_description = classify_occupation(row["occupation"])
        if new_value != row["occupation"] or self_description:
            cur.execute(
                "UPDATE contacts SET occupation = %s, occupation_self_description = %s WHERE id = %s",
                (new_value, self_description, row["id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    print(f"Remapped {updated} existing Occupation value(s) onto the new Occupation Category "
          f"list (anything unrecognized was preserved under 'Prefer to self-describe'). "
          f"Employment Status starts blank for everyone since it's a new field.")


if __name__ == "__main__":
    migrate()
