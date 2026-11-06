"""TA invitation: a private link in the invitation letter opens a page where the person answers Yes or No.
The answer is kept here and shown on the session's letters page and as a Dashboard alert."""
import secrets
from flask import render_template, request, abort


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS ta_invitations (
        id SERIAL PRIMARY KEY, cohort_id INTEGER NOT NULL, contact_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
        response TEXT, response_note TEXT, responded_at TEXT, UNIQUE (cohort_id, contact_id))""")


def token_for(get_db, cohort_id, contact_id):
    conn = get_db()
    try:
        ensure(conn)
        row = conn.execute("SELECT token FROM ta_invitations WHERE cohort_id = ? AND contact_id = ?", (cohort_id, contact_id)).fetchone()
        if row:
            return row["token"]
        tok = secrets.token_urlsafe(18)
        conn.execute("INSERT INTO ta_invitations (cohort_id, contact_id, token) VALUES (?,?,?) ON CONFLICT (cohort_id, contact_id) DO NOTHING",
                     (cohort_id, contact_id, tok))
        conn.commit()
        row = conn.execute("SELECT token FROM ta_invitations WHERE cohort_id = ? AND contact_id = ?", (cohort_id, contact_id)).fetchone()
        return row["token"]
    finally:
        conn.close()


def responses(conn, cohort_id):
    try:
        ensure(conn)
        return {r["contact_id"]: r for r in conn.execute(
            "SELECT contact_id, response, response_note, responded_at FROM ta_invitations WHERE cohort_id = ?", (cohort_id,)).fetchall()}
    except Exception:
        conn.rollback()
        return {}


def register(app, get_db, training_label, date_span):
    @app.route("/ta-invite/<token>", methods=["GET", "POST"])
    def public_ta_invite(token):
        conn = get_db()
        try:
            ensure(conn)
            inv = conn.execute(
                """SELECT i.*, ct.first_name, c.session_date, c.end_date, c.location, p.code, p.name
                   FROM ta_invitations i JOIN contacts ct ON ct.id = i.contact_id
                   JOIN cohorts c ON c.id = i.cohort_id JOIN programs p ON p.id = c.program_id
                   WHERE i.token = ?""", (token,)).fetchone()
            if not inv:
                abort(404)
            if request.method == "POST":
                answer = request.form.get("answer")
                if answer in ("Yes", "No"):
                    note = (request.form.get("note") or "").strip()[:600]
                    conn.execute("UPDATE ta_invitations SET response = ?, response_note = ?, "
                                 "responded_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?", (answer, note or None, inv["id"]))
                    conn.commit()
                    inv = dict(inv); inv["response"] = answer; inv["response_note"] = note
            return render_template("public_ta_invite.html", inv=inv, training=training_label(inv["code"], inv["name"]),
                                   dates=date_span(inv))
        finally:
            conn.close()
