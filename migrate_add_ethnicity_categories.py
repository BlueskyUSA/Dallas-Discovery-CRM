"""
One-time migration: moves Ethnicity from a free-text field to a fixed set of
categories (American Indian or Alaska Native, Asian, Black or African
American, Hispanic or Latino, Native Hawaiian or Other Pacific Islander,
White, Prefer to self-describe, Prefer not to say), backed by a new
"ethnicity_self_description" column for the self-describe text box.

What it does to existing data:
  - Adds the ethnicity_self_description column if it's not already there.
  - Best-effort maps existing free-text Ethnicity values onto the new
    categories (e.g. "African American" -> "Black or African American",
    "Latino"/"Latina"/"Latinx" -> "Hispanic or Latino", "Caucasian" ->
    "White").
  - Anything that doesn't clearly match one of those is preserved, not
    discarded: Ethnicity is set to "Prefer to self-describe" and the
    original text is copied into ethnicity_self_description, so no data is
    lost.
  - Blank/empty Ethnicity values are left blank.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_ethnicity_categories.py
"""
from db import get_db

_CATEGORY_ALIASES = {
    "american indian or alaska native": "American Indian or Alaska Native",
    "american indian": "American Indian or Alaska Native",
    "alaska native": "American Indian or Alaska Native",
    "native american": "American Indian or Alaska Native",
    "asian": "Asian",
    "asian american": "Asian",
    "black or african american": "Black or African American",
    "black": "Black or African American",
    "african american": "Black or African American",
    "hispanic or latino": "Hispanic or Latino",
    "hispanic": "Hispanic or Latino",
    "latino": "Hispanic or Latino",
    "latina": "Hispanic or Latino",
    "latinx": "Hispanic or Latino",
    "native hawaiian or other pacific islander": "Native Hawaiian or Other Pacific Islander",
    "native hawaiian": "Native Hawaiian or Other Pacific Islander",
    "pacific islander": "Native Hawaiian or Other Pacific Islander",
    "white": "White",
    "caucasian": "White",
    "prefer not to say": "Prefer not to say",
    "decline to state": "Prefer not to say",
}
_KNOWN_LABELS = {
    "American Indian or Alaska Native", "Asian", "Black or African American",
    "Hispanic or Latino", "Native Hawaiian or Other Pacific Islander", "White",
    "Prefer to self-describe", "Prefer not to say",
}


def classify(raw):
    value = (raw or "").strip()
    if not value:
        return (None, None)
    if value in _KNOWN_LABELS:
        return (value, None)
    mapped = _CATEGORY_ALIASES.get(value.lower())
    if mapped:
        return (mapped, None)
    # Doesn't match a known category — keep the original text, don't drop it.
    return ("Prefer to self-describe", value)


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    if "ethnicity_self_description" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN ethnicity_self_description TEXT")
        print("Added ethnicity_self_description column.")
    else:
        print("ethnicity_self_description column already exists.")

    rows = cur.execute(
        "SELECT id, ethnicity FROM contacts WHERE ethnicity IS NOT NULL AND ethnicity != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        new_ethnicity, self_description = classify(row["ethnicity"])
        if new_ethnicity != row["ethnicity"] or self_description:
            cur.execute(
                "UPDATE contacts SET ethnicity = %s, ethnicity_self_description = %s WHERE id = %s",
                (new_ethnicity, self_description, row["id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    print(f"Remapped {updated} existing Ethnicity value(s) onto the new categories "
          f"(anything unrecognized was preserved under 'Prefer to self-describe').")


if __name__ == "__main__":
    migrate()
