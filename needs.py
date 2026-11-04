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
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS food_preferences TEXT")
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


FIELDS = ("dietary_needs", "food_preferences", "access_needs", "activity_limits")


def save_from_form(conn, contact_id, form):
    """Saves what was typed; only the fields that were on the form are touched."""
    present = [f for f in FIELDS if f in form]
    if not present:
        return
    ensure(conn)
    conn.execute("UPDATE contacts SET " + ", ".join(f"{f} = ?" for f in present) + " WHERE id = ?",
                 [((form.get(f) or "").strip() or None) for f in present] + [contact_id])
    conn.commit()


def load(conn, contact_id):
    ensure(conn)
    row = conn.execute("SELECT dietary_needs, food_preferences, access_needs, activity_limits FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return {f: ((row[f] if row else None) or "") for f in FIELDS}


def session_summary(conn, cohort_id, full_name_sql):
    """Everyone on a session (trainees AND the session team) who listed a need, plus a head count for the hotel."""
    ensure(conn)
    people = conn.execute(
        f"""SELECT x.contact_id, x.role, {full_name_sql('ct')} AS full_name, ct.last_name, ct.first_name,
                   ct.dietary_needs, ct.food_preferences, ct.access_needs, ct.activity_limits
            FROM (SELECT contact_id, 'Trainee' AS role FROM enrollments WHERE cohort_id = ?
                  UNION ALL SELECT contact_id, role FROM cohort_staffing WHERE cohort_id = ?) x
            JOIN contacts ct ON ct.id = x.contact_id""", (cohort_id, cohort_id)).fetchall()
    by = {}
    for r in people:
        d = by.setdefault(r["contact_id"], dict(r))
        d.setdefault("roles", [])
        if r["role"] not in d["roles"]:
            d["roles"].append(r["role"])
    rows = []
    for d in by.values():
        d["role"] = ", ".join(d.get("roles", []))
        if any((d.get(f) or "").strip() for f in FIELDS):
            rows.append(d)
    rows.sort(key=lambda d: ((d.get("last_name") or "").lower(), (d.get("first_name") or "").lower()))
    return rows, len(by)


def team_without_answers(conn, cohort_id):
    """Session team members (not trainees) with an email who have not answered any food/physical question."""
    ensure(conn)
    return conn.execute(
        """SELECT DISTINCT ct.id, ct.first_name, ct.email, ct.profile_token
           FROM cohort_staffing cs JOIN contacts ct ON ct.id = cs.contact_id
           WHERE cs.cohort_id = ? AND cs.role <> 'Trainee' AND COALESCE(ct.email, '') <> ''
             AND COALESCE(ct.dietary_needs, '') = '' AND COALESCE(ct.food_preferences, '') = ''
             AND COALESCE(ct.access_needs, '') = '' AND COALESCE(ct.activity_limits, '') = ''""", (cohort_id,)).fetchall()


def field_html(label, name, value, rows=2):
    from markupsafe import escape
    return (f'<div class="field" style="grid-column: 1 / -1"><label>{escape(label)}</label>'
            f'<textarea name="{name}" rows="{rows}" style="width:100%">{escape(value or "")}</textarea></div>')
