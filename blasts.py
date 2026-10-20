"""Owner-only email blasts: one card per audience (a contact list, or the Guides), an editable message,
a test send, and a confirmed, paced, logged send.

Safety: real sends need BLASTS_LIVE=1 in the environment, a test send of the CURRENT wording, and the
word SEND typed on the confirm box. Nobody who asked not to be emailed is sent to, nobody gets the same
message twice, and every send is logged.
"""
import hashlib
import hmac
import html as _html
import os
import threading
import time

from flask import render_template, request, redirect, url_for, flash, abort, session, current_app

import call_flow as cf
from email_templates import excitement_blast_content, EXCITEMENT_BLAST_SUBJECT

PAUSE = 0.25          # seconds between emails, so the email service is never flooded
_ready = False

VOLUNTEERS_BODY = """Dear {first},

When people ask about Discovery, we want them to visit with an experienced, credible person who has been through the training. The training changed your life, and we hope you are passionate about sharing what it did for you.

Instead of relying on staff for the heavy lifting, we built an AI-assisted portal that makes it easy for every willing Dallas Discovery volunteer to help people register for the training. Our hope is you will help us fill seats. Your personal page works on your Android or Apple phone, iPad, or computer.

If you are willing, tap the link below to get your own personal volunteer page. If you know another Discovery volunteer who would like to help, your page has a link you can text or email to them.

{link}

Thank you for all that you do.

The Discovery Team"""

GUIDES_BODY = ""


def ensure_tables(get_db):
    global _ready
    if _ready:
        return
    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS blast_audiences (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'text',
            list_id INTEGER,
            subject TEXT,
            body TEXT,
            link_path TEXT,
            tested_hash TEXT,
            tested_at TEXT,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS blast_sends (
            id SERIAL PRIMARY KEY,
            audience_id INTEGER NOT NULL,
            subject TEXT,
            sent_by TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            total INTEGER NOT NULL DEFAULT 0,
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            snapshot_html TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS blast_recipients (
            id SERIAL PRIMARY KEY,
            send_id INTEGER NOT NULL,
            contact_id INTEGER,
            first_name TEXT,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            sent_at TEXT)""")
        conn.execute("ALTER TABLE blast_sends ADD COLUMN IF NOT EXISTS snapshot_html TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS do_not_email INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) AS n FROM blast_audiences").fetchone()["n"]
        if not n:
            list_id = lambda name: cf.list_id_for(conn, name)
            now = cf._ts(cf._utc_now())
            seeds = [
                ("Excitement blast: my contacts", "excitement", None, EXCITEMENT_BLAST_SUBJECT, None, None, 10),
                ("Excitement blast: Discovery contacts", "excitement", list_id(cf.ENTIRE_LIST), EXCITEMENT_BLAST_SUBJECT, None, None, 20),
                ("Discovery Guides", "guides", None, "", GUIDES_BODY, None, 30),
                ("Discovery volunteers", "text", list_id(cf.RESPONDED_LIST), "Exciting Changes to the Dallas Discovery Program",
                 VOLUNTEERS_BODY, "/volunteer-helper/join", 40),
                ("Volunteer staff", "text", list_id("Volunteer staff"), "", "", None, 50),
            ]
            for name, kind, lid, subj, body, link, order in seeds:
                conn.execute("INSERT INTO blast_audiences (name, kind, list_id, subject, body, link_path, sort_order, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (name, kind, lid, subj, body, link, order, now))
            conn.commit()
        # make sure the two data-set lists exist, and point the volunteer email at the people who responded
        cf.list_id_for(conn, cf.ENTIRE_LIST)
        resp_id = cf.list_id_for(conn, cf.RESPONDED_LIST)
        old = conn.execute("SELECT l.id FROM contact_lists l WHERE LOWER(l.name) = 'discovery volunteers'").fetchone()
        if old:
            conn.execute("UPDATE blast_audiences SET list_id = ? WHERE name = 'Discovery volunteers' AND list_id = ?", (resp_id, old["id"]))
        conn.commit()
        _ready = True
    except Exception as e:
        conn.rollback()
        print(f"Couldn't set up the email-blast tables: {e}", flush=True)
    finally:
        conn.close()


def blasts_live():
    return os.environ.get("BLASTS_LIVE") == "1"


def _secret():
    return (current_app.secret_key or "dev").encode()


def unsub_token(contact_id):
    sig = hmac.new(_secret(), f"unsub:{contact_id}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{contact_id}-{sig}"


def unsub_contact(token):
    try:
        cid, sig = (token or "").split("-", 1)
        good = hmac.new(_secret(), f"unsub:{int(cid)}".encode(), hashlib.sha256).hexdigest()[:24]
        return int(cid) if hmac.compare_digest(sig, good) else None
    except Exception:
        return None


def content_hash(aud):
    return hashlib.sha1(f"{aud['kind']}|{aud['subject']}|{aud['body']}|{aud['link_path']}".encode()).hexdigest()


def recipients(conn, aud):
    """[(contact_id or None, first_name, email)] — people who can be emailed, one per address."""
    rows = []
    if aud["kind"] == "guides":
        for r in conn.execute("SELECT name, email FROM call_volunteers WHERE active = 1 ORDER BY id").fetchall():
            rows.append((None, (r["name"] or "").split()[0] if r["name"] else "", r["email"]))
    elif aud["list_id"]:
        q = conn.execute("""SELECT c.id, c.first_name, c.email FROM contact_list_members m JOIN contacts c ON c.id = m.contact_id
                            WHERE m.list_id = ? AND c.email IS NOT NULL AND c.email <> '' AND COALESCE(c.do_not_email, 0) = 0
                            ORDER BY c.id""", (aud["list_id"],)).fetchall()
        for r in q:
            rows.append((r["id"], r["first_name"] or "", r["email"]))
    # an address that has unsubscribed on ANY contact record is never emailed (people are sometimes in the database twice)
    blocked = {r["e"] for r in conn.execute("SELECT DISTINCT LOWER(email) AS e FROM contacts WHERE COALESCE(do_not_email, 0) = 1 AND email IS NOT NULL").fetchall()}
    seen, out = set(blocked), []
    for cid, first, email in rows:
        e = (email or "").strip()
        k = e.lower()
        if "@" not in e or "." not in e.split("@")[-1] or k in seen:
            continue
        seen.add(k)
        out.append((cid, first, e))
    return out


def already_received(conn, aud):
    rows = conn.execute("""SELECT DISTINCT LOWER(r.email) AS e FROM blast_recipients r JOIN blast_sends s ON s.id = r.send_id
                           WHERE s.audience_id = ? AND s.subject = ? AND r.status = 'sent'""", (aud["id"], aud["subject"])).fetchall()
    return {r["e"] for r in rows}


def build(aud, first, contact_id, base_url):
    """(subject, html, text) for one person."""
    greet = first.strip() if first and first.strip() else "Friend"
    footer_h = footer_t = ""
    if aud["kind"] != "guides" and contact_id is not None:
        u = "#" if contact_id == -1 else f"{base_url}/unsubscribe/{unsub_token(contact_id)}"
        footer_h = (f'<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 10px">'
                    f'<p style="font-size:.8em;color:#777">You are receiving this because you are part of the Dallas Discovery community. '
                    f'<a href="{u}">Unsubscribe</a></p>')
        footer_t = f"\n\n--\nYou are receiving this because you are part of the Dallas Discovery community. Unsubscribe: {u}"
    if aud["kind"] == "excitement":
        html, text = excitement_blast_content(greet)
        return aud["subject"] or EXCITEMENT_BLAST_SUBJECT, html + footer_h, text + footer_t
    link = (base_url + aud["link_path"]) if aud["link_path"] else ""
    body = (aud["body"] or "").replace("{first}", greet)
    paras = [p for p in body.split("\n\n") if p.strip()]
    html_parts = []
    for p in paras:
        if p.strip() == "{link}":
            html_parts.append(f'<p><a href="{link}" style="display:inline-block;background:#1b2b45;color:#fff;padding:12px 22px;'
                              f'border-radius:8px;text-decoration:none;font-weight:bold">Get my volunteer page</a></p>'
                              f'<p style="font-size:.85em;color:#555">Or paste this into your browser: {link}</p>')
        else:
            html_parts.append("<p>" + _html.escape(p.strip()).replace("\n", "<br>").replace("{link}", f'<a href="{link}">{link}</a>') + "</p>")
    text = body.replace("{link}", link)
    return aud["subject"], "".join(html_parts) + footer_h, text + footer_t


def run_send(get_db, send_id, base_url):
    """Sends every queued recipient of one send, a few per second. Safe to run again after a restart."""
    conn = get_db()
    try:
        s = conn.execute("SELECT * FROM blast_sends WHERE id = ?", (send_id,)).fetchone()
        aud = conn.execute("SELECT * FROM blast_audiences WHERE id = ?", (s["audience_id"],)).fetchone()
        queue = conn.execute("SELECT * FROM blast_recipients WHERE send_id = ? AND status = 'queued' ORDER BY id", (send_id,)).fetchall()
        for r in queue:
            cur = conn.execute("SELECT status FROM blast_sends WHERE id = ?", (send_id,)).fetchone()
            if cur["status"] != "running":
                break
            subject, html, text = build(aud, r["first_name"], r["contact_id"], base_url)
            subject = s["subject"] or subject
            try:
                cf._send(r["email"], subject, html, text, to_name=r["first_name"] or None)
                conn.execute("UPDATE blast_recipients SET status = 'sent', sent_at = ? WHERE id = ?", (cf._ts(cf._utc_now()), r["id"]))
                conn.execute("UPDATE blast_sends SET sent_count = sent_count + 1 WHERE id = ?", (send_id,))
            except Exception as e:
                conn.execute("UPDATE blast_recipients SET status = 'failed', error = ? WHERE id = ?", (str(e)[:300], r["id"]))
                conn.execute("UPDATE blast_sends SET failed_count = failed_count + 1 WHERE id = ?", (send_id,))
            conn.commit()
            time.sleep(PAUSE)
        cur = conn.execute("SELECT status FROM blast_sends WHERE id = ?", (send_id,)).fetchone()
        if cur["status"] == "running":
            conn.execute("UPDATE blast_sends SET status = 'done', finished_at = ? WHERE id = ?", (cf._ts(cf._utc_now()), send_id))
            conn.commit()
    except Exception as e:
        print(f"Email blast {send_id} stopped: {e}", flush=True)
    finally:
        conn.close()


def register(app, crm, owner_required, get_db):
    base = lambda: cf._base_url()

    def _aud(conn, aid):
        a = conn.execute("SELECT * FROM blast_audiences WHERE id = ?", (aid,)).fetchone()
        if not a:
            abort(404)
        return a

    def _lists(conn):
        return conn.execute("SELECT id, name FROM contact_lists ORDER BY name").fetchall()

    @crm.route("/email-blasts", methods=["GET", "POST"])
    @owner_required
    def email_blasts():
        ensure_tables(get_db)
        conn = get_db()
        try:
            if request.method == "POST":
                name = (request.form.get("name") or "").strip()[:120]
                lid = request.form.get("list_id") or None
                if name:
                    conn.execute("INSERT INTO blast_audiences (name, kind, list_id, subject, body, sort_order, created_at) VALUES (?, 'text', ?, '', '', 90, ?)",
                                 (name, int(lid) if lid else None, cf._ts(cf._utc_now())))
                    conn.commit()
                    new = conn.execute("SELECT id FROM blast_audiences ORDER BY id DESC LIMIT 1").fetchone()
                    return redirect(url_for(".email_blast_edit", aid=new["id"]))
            cards = []
            for a in conn.execute("SELECT * FROM blast_audiences ORDER BY sort_order, id").fetchall():
                recips = recipients(conn, a)
                lst = conn.execute("SELECT name FROM contact_lists WHERE id = ?", (a["list_id"],)).fetchone() if a["list_id"] else None
                last = conn.execute("SELECT * FROM blast_sends WHERE audience_id = ? ORDER BY id DESC LIMIT 1", (a["id"],)).fetchone()
                cards.append(dict(a=a, count=len(recips), list_name=lst["name"] if lst else None,
                                  last=(dict(last, when=cf.local_text(last["started_at"])) if last else None)))
            return render_template("email_blasts.html", cards=cards, lists=_lists(conn), live=blasts_live())
        finally:
            conn.close()

    @crm.route("/email-blasts/<int:aid>", methods=["GET", "POST"])
    @owner_required
    def email_blast_edit(aid):
        ensure_tables(get_db)
        conn = get_db()
        try:
            a = _aud(conn, aid)
            act = request.form.get("action") if request.method == "POST" else None
            if act == "save":
                lid = request.form.get("list_id") or None
                conn.execute("UPDATE blast_audiences SET name = ?, list_id = ?, subject = ?, body = ?, link_path = ? WHERE id = ?",
                             ((request.form.get("name") or a["name"]).strip()[:120],
                              int(lid) if (lid and a["kind"] != "guides") else a["list_id"],
                              (request.form.get("subject") or "").strip()[:200],
                              request.form.get("body") if a["kind"] == "text" or a["kind"] == "guides" else a["body"],
                              ((request.form.get("link_path") or "").strip() or None) if a["kind"] != "excitement" else a["link_path"], aid))
                conn.commit()
                flash("Saved. Send yourself a test before the real send.")
                return redirect(url_for(".email_blast_edit", aid=aid))
            if act == "test":
                to = (request.form.get("to") or "").strip()
                if "@" not in to:
                    flash("Please enter an email address for the test.")
                else:
                    first = (session.get("staff_name") or "").split()[0] if session.get("staff_name") else "Friend"
                    subject, html, text = build(a, first, -1 if a["kind"] != "guides" else None, base())
                    try:
                        cf._send(to, "[TEST] " + (subject or "(no subject)"), html, text, to_name=first)
                        conn.execute("UPDATE blast_audiences SET tested_hash = ?, tested_at = ? WHERE id = ?",
                                     (content_hash(a), cf._ts(cf._utc_now()), aid))
                        conn.commit()
                        flash(f"Test sent to {to}. Check how it looks, then you can send for real.")
                    except Exception as e:
                        flash(f"The test could not be sent: {e}")
                return redirect(url_for(".email_blast_edit", aid=aid))
            if act == "send":
                recips = recipients(conn, a)
                skip = already_received(conn, a)
                todo = [r for r in recips if r[2].lower() not in skip]
                if not blasts_live():
                    flash("Real sending is switched off (BLASTS_LIVE is not set to 1 in Render).")
                elif (request.form.get("confirm") or "").strip() != "SEND":
                    flash("Type SEND in the box to confirm.")
                elif a["tested_hash"] != content_hash(a):
                    flash("Please send yourself a test of the current wording first.")
                elif not (a["subject"] or "").strip() and a["kind"] != "excitement":
                    flash("Please add a subject line.")
                elif a["kind"] != "excitement" and not (a["body"] or "").strip():
                    flash("Please write the message first.")
                elif not todo:
                    flash("There is nobody left to send this to.")
                else:
                    snap_subject, snap_html, _t = build(a, "Friend", -1 if a["kind"] != "guides" else None, base())
                    conn.execute("INSERT INTO blast_sends (audience_id, subject, sent_by, status, total, skipped_count, started_at, snapshot_html) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
                                 (aid, a["subject"] or snap_subject, session.get("staff_name"), len(todo), len(recips) - len(todo), cf._ts(cf._utc_now()), snap_html))
                    sid = conn.execute("SELECT id FROM blast_sends ORDER BY id DESC LIMIT 1").fetchone()["id"]
                    for cid, first, email in todo:
                        conn.execute("INSERT INTO blast_recipients (send_id, contact_id, first_name, email) VALUES (?, ?, ?, ?)", (sid, cid, first, email))
                    conn.commit()
                    if os.environ.get("DISABLE_BACKGROUND_THREADS"):
                        run_send(get_db, sid, base())
                    else:
                        threading.Thread(target=run_send, args=(get_db, sid, base()), daemon=True).start()
                    return redirect(url_for(".email_blast_send", sid=sid))
                return redirect(url_for(".email_blast_edit", aid=aid))
            recips = recipients(conn, a)
            skip = already_received(conn, a)
            first = (session.get("staff_name") or "").split()[0] if session.get("staff_name") else "Friend"
            subject, html, _t = build(a, first, -1 if a["kind"] != "guides" else None, base())
            sends = [dict(s, when=cf.local_text(s["started_at"])) for s in
                     conn.execute("SELECT * FROM blast_sends WHERE audience_id = ? ORDER BY id DESC LIMIT 10", (aid,)).fetchall()]
            owner = conn.execute("SELECT email FROM staff WHERE id = ?", (session.get("staff_id"),)).fetchone()
            return render_template("email_blast_edit.html", a=a, lists=_lists(conn), count=len(recips), already=len([r for r in recips if r[2].lower() in skip]),
                                   preview_subject=subject, preview_html=html, tested=(a["tested_hash"] == content_hash(a)),
                                   tested_when=cf.local_text(a["tested_at"]) if a["tested_at"] else "", live=blasts_live(), sends=sends,
                                   owner_email=(owner["email"] if owner else ""))
        finally:
            conn.close()

    @crm.route("/email-blasts/letters")
    @owner_required
    def email_blast_letters():
        """Every audience's letter on one page, plus what was actually sent the last time."""
        ensure_tables(get_db)
        conn = get_db()
        try:
            first = (session.get("staff_name") or "").split()[0] if session.get("staff_name") else "Friend"
            items = []
            for a in conn.execute("SELECT * FROM blast_audiences ORDER BY sort_order, id").fetchall():
                subject, html, _t = build(a, first, -1 if a["kind"] != "guides" else None, base())
                last = conn.execute("SELECT * FROM blast_sends WHERE audience_id = ? ORDER BY id DESC LIMIT 1", (a["id"],)).fetchone()
                items.append(dict(a=a, subject=subject, html=html, count=len(recipients(conn, a)),
                                  empty=(a["kind"] != "excitement" and not (a["body"] or "").strip()),
                                  last=(dict(last, when=cf.local_text(last["started_at"])) if last else None)))
            return render_template("email_blast_letters.html", items=items)
        finally:
            conn.close()

    @crm.route("/email-blasts/send/<int:sid>", methods=["GET", "POST"])
    @owner_required
    def email_blast_send(sid):
        ensure_tables(get_db)
        conn = get_db()
        try:
            s = conn.execute("SELECT * FROM blast_sends WHERE id = ?", (sid,)).fetchone()
            if not s:
                abort(404)
            if request.method == "POST" and request.form.get("action") == "stop" and s["status"] == "running":
                conn.execute("UPDATE blast_sends SET status = 'stopped', finished_at = ? WHERE id = ?", (cf._ts(cf._utc_now()), sid))
                conn.commit()
                return redirect(url_for(".email_blast_send", sid=sid))
            a = _aud(conn, s["audience_id"])
            failed = conn.execute("SELECT first_name, email, error FROM blast_recipients WHERE send_id = ? AND status = 'failed' LIMIT 50", (sid,)).fetchall()
            s = conn.execute("SELECT * FROM blast_sends WHERE id = ?", (sid,)).fetchone()
            return render_template("email_blast_send.html", s=s, a=a, failed=failed, started=cf.local_text(s["started_at"]))
        finally:
            conn.close()

    @app.route("/unsubscribe/<token>", methods=["GET", "POST"])
    def public_unsubscribe(token):
        ensure_tables(get_db)
        cid = unsub_contact(token)
        if not cid:
            abort(404)
        done = False
        if request.method == "POST":
            conn = get_db()
            try:
                conn.execute("UPDATE contacts SET do_not_email = 1 WHERE id = ?", (cid,))
                conn.execute("UPDATE contacts SET do_not_email = 1 WHERE email IS NOT NULL AND LOWER(email) = "
                             "(SELECT LOWER(email) FROM contacts WHERE id = ?)", (cid,))
                conn.commit()
                done = True
            finally:
                conn.close()
        return render_template("public_unsubscribe.html", done=done)

    # a restart in the middle of a send picks up where it left off
    def _resume():
        time.sleep(30)
        try:
            ensure_tables(get_db)
            conn = get_db()
            try:
                ids = [r["id"] for r in conn.execute("SELECT id FROM blast_sends WHERE status = 'running'").fetchall()]
            finally:
                conn.close()
            for sid in ids:
                run_send(get_db, sid, (os.environ.get("PUBLIC_BASE_URL") or "https://dallas-discovery-crm.onrender.com").rstrip("/"))
        except Exception as e:
            print(f"Couldn't resume email blasts: {e}", flush=True)

    if not os.environ.get("DISABLE_BACKGROUND_THREADS"):
        threading.Thread(target=_resume, daemon=True).start()
