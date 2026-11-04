"""Food and access needs for each person (food allergies, vegetarian/vegan, wheelchair or other accessibility needs).
Stored on the contact (two plain-text columns added the first time they are needed), asked on the long profile form and on
the training questionnaire, edited on the staff Edit Contact form, and summarized privately for Leadership on each session page."""

_ready = False
FOOD_LABEL = "Food allergies or dietary needs (for example vegetarian, vegan, gluten-free, a food allergy)"
LIMITS_LABEL = "Injuries that limit everyday activities"
ACCESS_LABEL = "Accessibility or room needs (for example a wheelchair-accessible room, mobility, hearing or vision needs)"
HELP_TEXT = "Optional. We share this only with the people planning your meals, rooms and activities."


def ensure(conn):
    """Adds the two columns the first time they're needed."""
    global _ready
    if _ready:
        return
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS dietary_needs TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS access_needs TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS activity_limits TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS previous_roles TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS partner_first_name TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS partner_last_name TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS no_partner TEXT")
        conn.commit()
        _ready = True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"Couldn't add food/access needs columns: {e}", flush=True)


def save_from_form(conn, contact_id, form):
    """Saves what was typed; does nothing if the form didn't include these fields."""
    if "dietary_needs" not in form and "access_needs" not in form and "activity_limits" not in form:
        return
    ensure(conn)
    conn.execute("UPDATE contacts SET dietary_needs = ?, access_needs = ?, activity_limits = ? WHERE id = ?",
                 ((form.get("dietary_needs") or "").strip() or None,
                  (form.get("access_needs") or "").strip() or None,
                  (form.get("activity_limits") or "").strip() or None, contact_id))
    conn.commit()


def load(conn, contact_id):
    ensure(conn)
    row = conn.execute("SELECT dietary_needs, access_needs, activity_limits FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    g = lambda k: (row[k] if row else None) or ""
    return {"dietary_needs": g("dietary_needs"), "access_needs": g("access_needs"), "activity_limits": g("activity_limits")}


def session_summary(conn, cohort_id, full_name_sql):
    """Everyone enrolled in a session who listed a need, plus counts for the hotel."""
    ensure(conn)
    rows = conn.execute(
        f"""SELECT {full_name_sql('ct')} AS full_name, ct.dietary_needs, ct.access_needs, ct.activity_limits
            FROM enrollments e JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.cohort_id = ? AND (COALESCE(ct.dietary_needs, '') <> '' OR COALESCE(ct.access_needs, '') <> '' OR COALESCE(ct.activity_limits, '') <> '')
            ORDER BY ct.last_name, ct.first_name""", (cohort_id,)).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM enrollments WHERE cohort_id = ?", (cohort_id,)).fetchone()["n"]
    return [dict(r) for r in rows], total


def field_html(label, name, value, rows=2):
    from markupsafe import escape
    return (f'<div class="field" style="grid-column: 1 / -1"><label>{escape(label)}</label>'
            f'<textarea name="{name}" rows="{rows}" style="width:100%">{escape(value or "")}</textarea></div>')
