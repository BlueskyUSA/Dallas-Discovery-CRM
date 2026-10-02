"""
One-time migration: "Marketing source" becomes "How did you hear about us"
and moves from free text to a fixed dropdown (Word of Mouth, Facebook,
Instagram, Google Search, Website, Church / Community Group, Past Attendee /
Graduate, Event or Public Speaking, Prefer to self-describe), backed by a new
"marketing_source_self_description" column for the self-describe text box.

What it does to existing data:
  - Adds the marketing_source_self_description column if not already there.
  - Best-effort maps existing free-text values onto the new categories
    (e.g. "facebook ad" -> "Facebook", "referral"/"friend" -> "Word of
    Mouth").
  - Anything that doesn't clearly match is preserved, not discarded:
    marketing_source is set to "Prefer to self-describe" and the original
    text is copied into marketing_source_self_description.
  - Blank/empty values are left blank.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_marketing_source_dropdown.py
"""
from db import get_db

_ALIASES = {
    "word of mouth": "Word of Mouth",
    "referral": "Word of Mouth",
    "friend": "Word of Mouth",
    "family": "Word of Mouth",
    "facebook": "Facebook",
    "facebook ad": "Facebook",
    "instagram": "Instagram",
    "instagram ad": "Instagram",
    "google": "Google Search",
    "google search": "Google Search",
    "website": "Website",
    "church": "Church / Community Group",
    "church / community group": "Church / Community Group",
    "community group": "Church / Community Group",
    "past attendee": "Past Attendee / Graduate",
    "past attendee / graduate": "Past Attendee / Graduate",
    "graduate": "Past Attendee / Graduate",
    "event": "Event or Public Speaking",
    "event or public speaking": "Event or Public Speaking",
    "public speaking": "Event or Public Speaking",
    "seminar": "Event or Public Speaking",
}
_KNOWN_LABELS = {
    "Word of Mouth", "Facebook", "Instagram", "Google Search", "Website",
    "Church / Community Group", "Past Attendee / Graduate",
    "Event or Public Speaking", "Prefer to self-describe",
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

    if "marketing_source_self_description" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN marketing_source_self_description TEXT")
        print("Added marketing_source_self_description column.")
    else:
        print("marketing_source_self_description column already exists.")

    rows = cur.execute(
        "SELECT id, marketing_source FROM contacts WHERE marketing_source IS NOT NULL AND marketing_source != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        new_source, self_description = classify(row["marketing_source"])
        if new_source != row["marketing_source"] or self_description:
            cur.execute(
                "UPDATE contacts SET marketing_source = %s, marketing_source_self_description = %s WHERE id = %s",
                (new_source, self_description, row["id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    print(f"Remapped {updated} existing 'How did you hear about us' value(s) onto the new "
          f"categories (anything unrecognized was preserved under 'Prefer to self-describe').")


if __name__ == "__main__":
    migrate()
