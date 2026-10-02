"""
One-time migration: moves Gender from a free-text field to a fixed set of
categories (Man, Woman, Non-binary, Prefer to self-describe, Prefer not to
say), backed by a new "gender_self_description" column for the self-describe
text box.

What it does to existing data:
  - Adds the gender_self_description column if it's not already there.
  - Best-effort maps existing free-text Gender values onto the new
    categories (e.g. "Male"/"M" -> "Man", "Female"/"F" -> "Woman",
    "Non-binary"/"NB"/"Enby" -> "Non-binary").
  - Anything that doesn't clearly match one of those is preserved, not
    discarded: Gender is set to "Prefer to self-describe" and the original
    text is copied into gender_self_description, so no data is lost.
  - Blank/empty Gender values are left blank.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_gender_categories.py
"""
from db import get_db

_MAN = {"male", "m", "man", "cis male", "cisgender male"}
_WOMAN = {"female", "f", "woman", "cis female", "cisgender female"}
_NONBINARY = {"non-binary", "nonbinary", "non binary", "nb", "enby", "genderqueer"}
_KNOWN = {"man", "woman", "non-binary", "prefer to self-describe", "prefer not to say"}


def classify(raw):
    value = (raw or "").strip()
    if not value:
        return (None, None)
    lowered = value.lower()
    if lowered in _MAN:
        return ("Man", None)
    if lowered in _WOMAN:
        return ("Woman", None)
    if lowered in _NONBINARY:
        return ("Non-binary", None)
    if lowered in {"prefer not to say", "decline to state", "prefer not to answer"}:
        return ("Prefer not to say", None)
    if lowered in _KNOWN:
        # Already an exact match for one of the new category labels.
        return (value if value[0].isupper() else value.capitalize(), None)
    # Doesn't match a known category — keep the original text, don't drop it.
    return ("Prefer to self-describe", value)


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'contacts'"
    )
    cols = [row["column_name"] for row in cur.fetchall()]

    if "gender_self_description" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN gender_self_description TEXT")
        print("Added gender_self_description column.")
    else:
        print("gender_self_description column already exists.")

    rows = cur.execute(
        "SELECT id, gender FROM contacts WHERE gender IS NOT NULL AND gender != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        new_gender, self_description = classify(row["gender"])
        if new_gender != row["gender"] or self_description:
            cur.execute(
                "UPDATE contacts SET gender = %s, gender_self_description = %s WHERE id = %s",
                (new_gender, self_description, row["id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    print(f"Remapped {updated} existing Gender value(s) onto the new categories "
          f"(anything unrecognized was preserved under 'Prefer to self-describe').")


if __name__ == "__main__":
    migrate()
