"""
One-time migration: moves Spiritual orientation from a free-text field to a
fixed dropdown (Christian, Catholic, Jewish, Muslim, Buddhist, Hindu,
Spiritual but not religious, Agnostic, Atheist, Prefer to self-describe,
Prefer not to say), backed by a new "spiritual_orientation_self_description"
column for the self-describe text box.

What it does to existing data:
  - Adds the spiritual_orientation_self_description column if not already
    there.
  - Best-effort maps existing free-text values onto the new categories
    (e.g. "non-denominational" -> "Christian", "none" -> "Atheist").
  - Anything that doesn't clearly match is preserved, not discarded:
    spiritual_orientation is set to "Prefer to self-describe" and the
    original text is copied into spiritual_orientation_self_description.
  - Blank/empty values are left blank.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_spiritual_orientation_dropdown.py
"""
from db import get_db

_ALIASES = {
    "christian": "Christian",
    "christianity": "Christian",
    "non-denominational": "Christian",
    "nondenominational": "Christian",
    "protestant": "Christian",
    "catholic": "Catholic",
    "catholicism": "Catholic",
    "jewish": "Jewish",
    "judaism": "Jewish",
    "muslim": "Muslim",
    "islam": "Muslim",
    "buddhist": "Buddhist",
    "buddhism": "Buddhist",
    "hindu": "Hindu",
    "hinduism": "Hindu",
    "spiritual but not religious": "Spiritual but not religious",
    "spiritual": "Spiritual but not religious",
    "spiritual not religious": "Spiritual but not religious",
    "agnostic": "Agnostic",
    "atheist": "Atheist",
    "none": "Atheist",
    "prefer not to say": "Prefer not to say",
}
_KNOWN_LABELS = {
    "Christian", "Catholic", "Jewish", "Muslim", "Buddhist", "Hindu",
    "Spiritual but not religious", "Agnostic", "Atheist",
    "Prefer to self-describe", "Prefer not to say",
}


def classify(raw):
    value = (raw or "").strip()
    if not value:
        return (None, None)
    if value in _KNOWN_LABELS:
        return (value, None)
    mapped = _ALIASES.get(value.lower())
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

    if "spiritual_orientation_self_description" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN spiritual_orientation_self_description TEXT")
        print("Added spiritual_orientation_self_description column.")
    else:
        print("spiritual_orientation_self_description column already exists.")

    rows = cur.execute(
        "SELECT id, spiritual_orientation FROM contacts "
        "WHERE spiritual_orientation IS NOT NULL AND spiritual_orientation != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        new_value, self_description = classify(row["spiritual_orientation"])
        if new_value != row["spiritual_orientation"] or self_description:
            cur.execute(
                "UPDATE contacts SET spiritual_orientation = %s, "
                "spiritual_orientation_self_description = %s WHERE id = %s",
                (new_value, self_description, row["id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    print(f"Remapped {updated} existing Spiritual orientation value(s) onto the new categories "
          f"(anything unrecognized was preserved under 'Prefer to self-describe').")


if __name__ == "__main__":
    migrate()
