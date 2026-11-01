"""Correspondence: one place for every letter.

  Trainee Correspondence       pick a session; the trainee letters for that session (the existing session letters).
  Team Correspondence          pick a session; the facilitator / TA letters; plus a note to Facilitators, TA's,
                               Support Team or Leadership.
  Accounting & Legal           a note to the Accounting & Legal logins.
  Marketing & Email Blasts     the existing page (/email-blasts), unchanged.

Notes (Team and Accounting & Legal) go out one email per person, from a preview/test first, and only when
SESSION_LETTERS_LIVE=1 is set. Nothing here touches the Marketing safeguards.
"""
import html as _html
import os
import re

from flask import abort, flash, redirect, render_template, request, session, url_for

import string

import email_templates as et
from email_utils import send_email, EmailSendError

# key -> (page title, who it goes to, picks people by session?)
GROUPS = {
    "facilitators": ("Facilitators", "Lead and Support Facilitators", True),
    "tas": ("TA's", "TA's and Back-up TA's", True),
    "support": ("Support Team", "Support Team", True),
    "leadership": ("Leadership", "Leadership and Owner logins", False),
    "accounting": ("Accounting & Legal", "Accounting & Legal logins", False),
}
TEAM_GROUPS = ["facilitators", "tas", "support", "leadership"]

_ready = False


def live():
    return (os.environ.get("SESSION_LETTERS_LIVE") or "").strip() == "1"


def ensure_tables(get_db):
    global _ready
    if _ready:
        return
    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS team_notes (
            id SERIAL PRIMARY KEY,
            group_key TEXT NOT NULL,
            cohort_id INTEGER,
            subject TEXT,
            body TEXT,
            sent_by TEXT,
            sent_count INTEGER NOT NULL DEFAULT 0,
            skipped TEXT,
            sent_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))""")
        conn.commit()
        _ready = True
    finally:
        conn.close()


def note_recipients(conn, group, cohort_id=None):
    """[(contact_id or None, first_name, name, email)] one per address. Unsubscribed contacts are left out."""
    roles = {"facilitators": ("Lead Facilitator", "Support Facilitator"),
             "tas": ("TA", "Back-up TA"), "support": ("Support Team",)}
    rows = []
    if group in roles:
        ph = ",".join("?" for _ in roles[group])
        sql = (f"SELECT DISTINCT ct.id, ct.first_name, ct.last_name, ct.email FROM cohort_staffing cs "
               f"JOIN contacts ct ON ct.id = cs.contact_id WHERE cs.role IN ({ph}) "
               f"AND ct.email IS NOT NULL AND ct.email <> '' AND COALESCE(ct.do_not_email, 0) = 0")
        args = list(roles[group])
        if cohort_id:
            sql += " AND cs.cohort_id = ?"
            args.append(cohort_id)
        for r in conn.execute(sql + " ORDER BY ct.last_name, ct.first_name", tuple(args)).fetchall():
            rows.append((r["id"], r["first_name"] or "", f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(), r["email"]))
    elif group == "leadership":
        for r in conn.execute("SELECT id, name, email FROM staff WHERE active = 1 AND (role = 'Leadership' OR is_owner = 1) ORDER BY name").fetchall():
            rows.append((None, (r["name"] or "").split()[0] if r["name"] else "", r["name"], r["email"]))
    elif group == "accounting":
        for r in conn.execute("SELECT id, name, email FROM staff WHERE active = 1 AND role = 'Accounting' ORDER BY name").fetchall():
            rows.append((None, (r["name"] or "").split()[0] if r["name"] else "", r["name"], r["email"]))
    seen, out = set(), []
    for cid, first, name, email in rows:
        e = (email or "").strip()
        k = e.lower()
        if "@" not in e or k in seen:
            continue
        seen.add(k)
        out.append((cid, first, name, e))
    return out


def build_note(subject, body, first, sender, session_label=None):
    """(html, text) for one person. The body is plain paragraphs; [First name] becomes the person's first name."""
    greet = (first or "").strip() or "Friend"
    text_body = re.sub(r"(?i)\[\s*first\s*name\s*\]|\{\s*first\s*name\s*\}", greet, body or "").replace("\r\n", "\n").strip()
    paras = [p for p in text_body.split("\n\n") if p.strip()]
    parts = []
    for p in paras:
        esc = _html.escape(p.strip()).replace("\n", "<br>")
        esc = re.sub(r'(?<!["\'>=])(https?://[^\s<]+)', r'<a href="\1">\1</a>', esc)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        parts.append(f"<p>{esc}</p>")
    sig = f"<p>{_html.escape(sender)}</p>" if sender else ""
    html = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;color:#222">'
            f"{''.join(parts)}{sig}</div>")
    return html, text_body + (f"\n\n{sender}" if sender else "")


def sessions_with_counts(conn):
    return conn.execute(
        """SELECT co.*, p.code, p.name AS program_name,
                  (SELECT COUNT(*) FROM enrollments e WHERE e.cohort_id = co.id) AS n_trainees,
                  (SELECT COUNT(DISTINCT cs.contact_id) FROM cohort_staffing cs WHERE cs.cohort_id = co.id
                      AND cs.role IN ('Lead Facilitator','Support Facilitator','TA','Back-up TA','Support Team')) AS n_team,
                  (SELECT COUNT(*) FROM session_letters_sent s WHERE s.cohort_id = co.id) AS n_sent
           FROM cohorts co JOIN programs p ON p.id = co.program_id
           ORDER BY co.session_date DESC""").fetchall()


def register(app, crm, get_db, h):
    """h: dict of helpers from app.py: leadership_required, correspondence_access, area_required, staff_email,
    ensure_letters_table, program_label (code -> display name)."""
    leadership_required = h["leadership_required"]
    correspondence_access = h["correspondence_access"]

    def _staff_name():
        return session.get("staff_name") or ""

    def _is_leader():
        return bool(session.get("is_owner")) or session.get("staff_role") == "Leadership"

    @crm.route("/correspondence")
    @leadership_required
    def correspondence_home():
        return render_template("correspondence_home.html", groups=GROUPS, team_groups=TEAM_GROUPS,
                               can_marketing=h["has_correspondence_access"]())

    def _sessions_page(kind):
        ensure_tables(get_db)
        conn = get_db()
        try:
            h["ensure_letters_table"](conn)
            sessions = sessions_with_counts(conn)
            counts = {}
            for r in conn.execute("SELECT cohort_id, SUBSTR(sent_at, 1, 4) AS yr, COUNT(*) AS n FROM session_letters_sent "
                                  "GROUP BY cohort_id, SUBSTR(sent_at, 1, 4)").fetchall():
                counts.setdefault(r["cohort_id"], {})[r["yr"]] = r["n"]
            years = sorted({y for c in counts.values() for y in c}, reverse=True)
            edits = {r["letter_key"]: r for r in conn.execute("SELECT letter_key, updated_by, updated_at FROM session_letter_text").fetchall()}
            keys = [k for k in et.SESSION_LETTER_ORDER if (k in et.SESSION_TRAINEE_LETTERS) == (kind == "trainees")]
            letter_rows = [dict(key=k, title=et.SESSION_LETTERS[k]["title"], audience=et.SESSION_LETTERS[k]["audience"],
                                edit=edits.get(k)) for k in keys]
            return render_template("correspondence_sessions.html", kind=kind, sessions=sessions, years=years, counts=counts,
                                   letter_rows=letter_rows,
                                   labels=h["program_labels"], groups=GROUPS, team_groups=TEAM_GROUPS, live=live())
        finally:
            conn.close()

    @crm.route("/correspondence/trainees")
    @leadership_required
    def correspondence_trainees():
        return _sessions_page("trainees")

    @crm.route("/correspondence/team")
    @leadership_required
    def correspondence_team():
        return _sessions_page("team")

    @crm.route("/correspondence/note/<group>", methods=["GET", "POST"])
    def correspondence_note(group):
        if group not in GROUPS:
            abort(404)
        # Team groups: Leadership or Owner. Accounting & Legal: the Owner, or anyone the Owner gave Correspondence.
        if group == "accounting":
            if not h["has_correspondence_access"]():
                flash("Correspondence is restricted. Ask the Owner to give you access.")
                return redirect(url_for(".dashboard"))
        elif not _is_leader():
            flash("That page is restricted to Leadership accounts.")
            return redirect(url_for(".dashboard"))
        ensure_tables(get_db)
        title, who, by_session = GROUPS[group]
        conn = get_db()
        try:
            cohort_id = request.values.get("cohort_id", type=int) if by_session else None
            subject = (request.form.get("subject") or "").strip()
            body = request.form.get("body") or ""
            people = note_recipients(conn, group, cohort_id)
            if request.method == "POST":
                action = request.form.get("action")
                if not subject or not body.strip():
                    flash("Please write a subject and a message first.")
                elif action == "test":
                    me = h["staff_email"]()
                    html, text = build_note(subject, body, (_staff_name() or "").split(" ")[0], _staff_name())
                    try:
                        send_email(to_email=me, subject="[TEST] " + subject, html_content=html, text_content=text)
                        flash(f"Test copy sent to you at {me}. Nothing was sent to the {title} group.")
                    except EmailSendError as e:
                        flash(f"Couldn't send the test copy: {e}")
                elif action == "send":
                    if not live():
                        flash("Sending to people is switched off for now. You can send yourself a test copy.")
                    elif (request.form.get("confirm") or "").strip() != "SEND":
                        flash("Nothing was sent. Type SEND in the box to confirm.")
                    elif not people:
                        flash("There is no one to send to.")
                    else:
                        n, bad = 0, []
                        for cid, first, name, email in people:
                            html, text = build_note(subject, body, first, _staff_name())
                            try:
                                send_email(to_email=email, to_name=first or None, subject=subject,
                                           html_content=html, text_content=text)
                                n += 1
                            except EmailSendError as e:
                                bad.append(f"{name or email} ({e})")
                        conn.execute("INSERT INTO team_notes (group_key, cohort_id, subject, body, sent_by, sent_count, skipped) VALUES (?,?,?,?,?,?,?)",
                                     (group, cohort_id, subject, body, _staff_name(), n, "; ".join(bad) or None))
                        conn.commit()
                        print(f"SENT team note to {group} cohort {cohort_id}: {n} sent by {_staff_name()}", flush=True)
                        msg = f"Sent to {n} {'person' if n == 1 else 'people'}."
                        if bad:
                            msg += " Not sent: " + "; ".join(bad) + "."
                        flash(msg)
                        return redirect(url_for(".correspondence_note", group=group, cohort_id=cohort_id or ""))
            history = conn.execute(
                "SELECT t.*, co.session_number, p.code FROM team_notes t LEFT JOIN cohorts co ON co.id = t.cohort_id "
                "LEFT JOIN programs p ON p.id = co.program_id WHERE t.group_key = ? ORDER BY t.id DESC LIMIT 25", (group,)).fetchall()
            sessions = conn.execute("SELECT co.*, p.code, p.name AS program_name FROM cohorts co JOIN programs p ON p.id = co.program_id "
                                    "ORDER BY co.session_date DESC").fetchall() if by_session else []
            return render_template("correspondence_note.html", group=group, title=title, who=who, by_session=by_session,
                                   people=people, cohort_id=cohort_id, sessions=sessions, labels=h["program_labels"],
                                   subject=subject, body=body, history=history, live=live(),
                                   years=sorted({(x["sent_at"] or "")[:4] for x in history if x["sent_at"]}, reverse=True),
                                   back=("correspondence_team" if group in TEAM_GROUPS else "correspondence_home"))
        finally:
            conn.close()


    # ---------------------------------------------------------------- editing the wording of a session letter
    SAMPLE = dict(first_name="Alex", training="The Art of Us", dates="Feb. 12, 2027 to Feb. 14, 2027",
                  location="the hotel", role="Lead Facilitator", duties=["Sound Equipment"], link="https://example.org/your-link",
                  partner="Jordan", amount="$1,399 per couple")

    def _problem(key, subject, body):
        """A plain-English reason the wording cannot be saved, or None."""
        if not subject.strip() or not body.strip():
            return "Please write a subject and a message."
        names = set()
        try:
            for text in [subject] + [x for x in body.replace("\r\n", "\n").split("\n\n")]:
                for _lit, name, _spec, _conv in string.Formatter().parse(text):
                    if name:
                        names.add(name)
        except ValueError:
            return "A curly bracket { or } is not closed. Words in curly brackets, like {training}, must be spelled exactly as listed."
        bad = sorted(names - set(et.LETTER_FIELDS))
        if bad:
            return "These are not recognized: " + ", ".join("{" + b + "}" for b in bad) + ". Use only the words listed on the right."
        needs_link = "{link}" in et.original_body(key)
        if needs_link and "link" not in names:
            return "This letter must keep {link}, the person's private link."
        return None

    @crm.route("/correspondence/letters/<key>/edit", methods=["GET", "POST"])
    @leadership_required
    def correspondence_letter_edit(key):
        if key not in et.SESSION_LETTERS:
            abort(404)
        conn = get_db()
        try:
            h["ensure_letters_table"](conn)
            orig = et.SESSION_LETTERS[key]
            saved = conn.execute("SELECT * FROM session_letter_text WHERE letter_key = ?", (key,)).fetchone()
            cur = dict(subject=saved["subject"], body=saved["body"], signoff=saved["signoff"], signer=saved["signer"]) if saved else \
                dict(subject=orig["subject"], body=et.original_body(key), signoff=orig["signoff"], signer=orig.get("signer", "Kent Hafemann"))
            form = dict(cur)
            error = None
            if request.method == "POST":
                action = request.form.get("action")
                form = dict(subject=(request.form.get("subject") or "").strip(), body=(request.form.get("body") or "").strip(),
                            signoff=(request.form.get("signoff") or "").strip(), signer=(request.form.get("signer") or "").strip())
                if action == "reset":
                    conn.execute("DELETE FROM session_letter_text WHERE letter_key = ?", (key,))
                    conn.commit()
                    flash("Back to the original wording.")
                    return redirect(url_for(".correspondence_letter_edit", key=key))
                error = _problem(key, form["subject"], form["body"])
                if not error and action == "save":
                    conn.execute(
                        """INSERT INTO session_letter_text (letter_key, subject, body, signoff, signer, updated_by, updated_at)
                           VALUES (?,?,?,?,?,?, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
                           ON CONFLICT (letter_key) DO UPDATE SET subject = EXCLUDED.subject, body = EXCLUDED.body,
                             signoff = EXCLUDED.signoff, signer = EXCLUDED.signer, updated_by = EXCLUDED.updated_by,
                             updated_at = EXCLUDED.updated_at""",
                        (key, form["subject"], form["body"], form["signoff"], form["signer"], session.get("staff_name")))
                    conn.commit()
                    flash("Saved. Letters sent from now on use this wording. Letters already sent keep the wording they went out with.")
                    return redirect(url_for(".correspondence_letter_edit", key=key))
            subject = html = None
            if not error:
                try:
                    fill = et.fill_for(SAMPLE["first_name"], SAMPLE["training"], SAMPLE["dates"], SAMPLE["location"],
                                       SAMPLE["role"], SAMPLE["duties"], SAMPLE["link"], SAMPLE["partner"], SAMPLE["amount"],
                                       "The Friday session ends by 11:00 pm at the latest.")
                    subject, html, text = et.render_spec(et.spec_with_edit(key, form), fill, SAMPLE["link"])
                    if request.method == "POST" and request.form.get("action") == "test":
                        me = h["staff_email"]()
                        try:
                            send_email(to_email=me, subject="[TEST] " + subject, html_content=html, text_content=text)
                            flash(f"Test copy sent to you at {me}. Nothing was sent to anyone else.")
                        except EmailSendError as e:
                            flash(f"Couldn't send the test copy: {e}")
                except Exception:
                    error = "This wording can't be used. Check the curly-bracket words."
            used = [f for f in et.LETTER_FIELDS if "{" + f + "}" in (orig["subject"] + " ".join(orig["paras"])) and f != "first"]
            return render_template("correspondence_letter_edit.html", key=key, orig=orig, form=form, error=error,
                                   saved=saved, preview_subject=subject, preview_html=html, used=used,
                                   back=("correspondence_trainees" if key in et.SESSION_TRAINEE_LETTERS else "correspondence_team"))
        finally:
            conn.close()
