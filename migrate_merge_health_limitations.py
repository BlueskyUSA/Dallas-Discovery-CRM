"""
One-time migration: "Overall health" and "Health limitations" are being
merged into a single field, relabeled "Overall Health and Health
Limitations/Injuries" (still stored in the existing overall_health column).

For any contact that has a health_limitations value, this appends it onto
their overall_health text (so nothing is lost) in the form:

    <existing overall_health text>
    Limitations/Injuries: <existing health_limitations text>

The health_limitations column itself is left in place (not dropped) in case
you ever need to look at the original raw value — it's just no longer shown
or edited in the app going forward.

Safe to run more than once — it won't double-append if it's already merged
(it tracks this by clearing health_limitations after merging it in).

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_merge_health_limitations.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, overall_health, health_limitations FROM contacts "
        "WHERE health_limitations IS NOT NULL AND health_limitations != ''"
    ).fetchall()

    merged = 0
    for row in rows:
        overall = (row["overall_health"] or "").strip()
        limitations = (row["health_limitations"] or "").strip()
        if not limitations:
            continue
        combined = f"{overall}\nLimitations/Injuries: {limitations}" if overall else \
                   f"Limitations/Injuries: {limitations}"
        cur.execute(
            "UPDATE contacts SET overall_health = %s, health_limitations = '' WHERE id = %s",
            (combined, row["id"]),
        )
        merged += 1

    conn.commit()
    conn.close()
    print(f"Merged health_limitations into overall_health for {merged} contact(s).")


if __name__ == "__main__":
    migrate()
