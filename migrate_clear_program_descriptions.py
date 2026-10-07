"""
One-time fix: clears the six old program descriptions, so they no longer
show on the Programs page (or on the public programs page):

  1. Claiming your Peace and Joy without the clutter that trips you up.
  2. Living in the moment with a renewed mindset.
  3. Identifying and seizing what is important in your life.
  4. Explore Life Wisdom ... (The Squeeze) vs ... (The Juice).
  5. Take a deep breath, refresh, and review your leanings.
  6. An invitation to find your center and draw close to the creator.

What it does:
  - Blanks ONLY the description text of a program whose description contains
    one of those phrases (any capitalization). A description you have
    written or edited yourself is left alone, and the script says so.
  - Never touches a program's code, name, prerequisite, or any training
    session, enrollment or contract linked to it. Nothing is deleted.

Safe to run more than once. You can type a new description back into any
program later on the Programs page.

Run it ONCE against your Postgres database. In your Render service's
"Shell" tab (DATABASE_URL is already set there):

    python3 migrate_clear_program_descriptions.py
"""
from db import get_db

OLD_PHRASES = [
    "peace and joy",
    "renewed mindset",
    "seizing what is important",
    "life wisdom",
    "the squeeze",
    "the juice",
    "refresh, and review",
    "find your center",
]


def migrate():
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("SELECT id, code, description FROM programs ORDER BY code").fetchall()
    cleared = 0
    for r in rows:
        desc = (r["description"] or "").strip()
        if not desc:
            continue
        if any(p in desc.lower() for p in OLD_PHRASES):
            cur.execute("UPDATE programs SET description = NULL WHERE id = ?", (r["id"],))
            print(f'{r["code"]}: description cleared.')
            cleared += 1
        else:
            print(f'{r["code"]}: has a different description -- left as is.')
    conn.commit()
    conn.close()
    print(f"Done. {cleared} description(s) cleared.")


if __name__ == "__main__":
    migrate()
