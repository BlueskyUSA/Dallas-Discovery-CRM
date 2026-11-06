"""Owner Instructions page: the Render and go-live steps in one place, with the current state of each safety switch
and a checklist that saves. Owner only."""
from flask import render_template, request, redirect, url_for, session, flash

CHECKLIST = [
    ("health_check", "Contact health check passed (Contacts → Health check)"),
    ("keith", "Discussions with Keith finished (Nov 20 go / no-go)"),
    ("brevo_webhook", "Brevo bounce connection set up (see below)"),
    ("test_letters", "Test copies of the main letters sent to myself and read"),
    ("switch_letters", "Turned on SESSION_LETTERS_LIVE in Render"),
    ("switch_auto", "Turned on AUTO_LETTERS_LIVE in Render (automatic letters)"),
    ("switch_blasts", "Turned on BLASTS_LIVE in Render (email blasts)"),
    ("public_site", "Public-site files uploaded to GitHub (held until ready to go live)"),
    ("first_blast", "First email blast sent"),
]


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS owner_checklist (key TEXT PRIMARY KEY, done_by TEXT, done_at TEXT)")
    conn.commit()


def register(app, crm, owner_required, get_db, switches_fn):
    @crm.route("/owner-instructions")
    @owner_required
    def owner_instructions():
        import bounces
        conn = get_db()
        try:
            ensure(conn)
            done = {r["key"]: r for r in conn.execute("SELECT key, done_by, done_at FROM owner_checklist").fetchall()}
        finally:
            conn.close()
        try:
            hook = url_for("brevo_webhook", key=bounces.secret(app), _external=True)
        except Exception:
            hook = None
        return render_template("owner_instructions.html", checklist=CHECKLIST, done=done,
                               switches=switches_fn(), hook=hook)

    @crm.route("/owner-instructions/check", methods=["POST"])
    @owner_required
    def owner_instructions_check():
        key = request.form.get("key")
        if key in dict(CHECKLIST):
            conn = get_db()
            try:
                ensure(conn)
                if conn.execute("SELECT 1 FROM owner_checklist WHERE key = ?", (key,)).fetchone():
                    conn.execute("DELETE FROM owner_checklist WHERE key = ?", (key,))
                else:
                    conn.execute("INSERT INTO owner_checklist (key, done_by, done_at) VALUES (?, ?, to_char(now(), 'YYYY-MM-DD'))",
                                 (key, session.get("staff_name")))
                conn.commit()
            finally:
                conn.close()
        return redirect(url_for(".owner_instructions") + "#checklist")
