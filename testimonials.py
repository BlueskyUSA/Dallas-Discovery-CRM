"""Testimonials: invite a contact to share their experience (typed, dictated or recorded), review it in the CRM,
and publish approved stories on the public Stories page.

Public:  /share/<token>   the private link a contact opens to share
         /stories         approved stories, grouped by program
         /stories/video/<id>   approved videos only
CRM:     Admin > Testimonials (review list), /testimonials/request (send a link), /testimonials/<id> (review)
"""
import io
import re
import secrets
from datetime import datetime
import html as _html

from email_utils import DEFAULT_FROM_EMAIL
from flask import render_template, request, redirect, url_for, flash, abort, session, send_file, make_response

# (code, label) in the order shown on the Stories page; "GEN" = general / more than one program
STORY_PROGRAMS = [("D4", "The Art of Us"), ("D1", "The Beginning"), ("D2", "Awakening"), ("D3", "Vision"),
                  ("D5", "Renew"), ("D6", "Spiritual"), ("GEN", "Discovery! in general")]
PROGRAM_LABEL = dict(STORY_PROGRAMS)
MAX_VIDEO_BYTES = 70 * 1024 * 1024
NAME_CHOICES = [("full", "My full name"), ("initial", "My first name and last initial"), ("anon", "Anonymous")]
CONSENT_TEXT = ("I give Discovery! permission to publish what I have shared here (my words and/or my video) on its "
                "website and in its materials, using the name choice I picked. I understand I can ask to have it "
                "removed at any time.")

# Words that sound therapeutic or medical. Shown as a warning on the review page so they can be edited out
# (or the person asked) before anything is published. Matching is by word start, e.g. "heal" also catches "healing".
FLAG_WORDS = ["heal", "therap", "cure", "cured", "trauma", "depress", "anxiety", "anxious", "ptsd", "diagnos", "treatment",
              "medicat", "psychiatr", "psycholog", "counsel", "disorder", "addiction", "recovery", "abuse", "suicid",
              "mental health", "clinical", "symptom", "disease", "illness", "bipolar", "panic"]


def flagged_words(text):
    low = (text or "").lower()
    return sorted({w for w in FLAG_WORDS if re.search(r"\b" + re.escape(w), low)})


_ready = False


def _ensure(get_db):
    global _ready
    if _ready:
        return
    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS testimonial_requests (
            id SERIAL PRIMARY KEY, token TEXT UNIQUE NOT NULL, contact_id INTEGER, program_code TEXT,
            note TEXT, created_at TEXT, sent_at TEXT, sent_by TEXT, submitted_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS testimonials (
            id SERIAL PRIMARY KEY, request_id INTEGER, contact_id INTEGER, program_code TEXT,
            body TEXT, body_original TEXT, name_choice TEXT, display_name TEXT, attended TEXT,
            video_data BYTEA, video_type TEXT, status TEXT NOT NULL DEFAULT 'New', featured INTEGER NOT NULL DEFAULT 0,
            consent_at TEXT, created_at TEXT, approved_at TEXT)""")
        for col in ("confirm_token TEXT", "confirm_state TEXT", "confirm_note TEXT", "confirm_at TEXT"):
            conn.execute(f"ALTER TABLE testimonials ADD COLUMN IF NOT EXISTS {col}")
        conn.commit()
        _ready = True
    except Exception as e:
        conn.rollback()
        print(f"Couldn't create testimonial tables: {e}", flush=True)
    finally:
        conn.close()


def _display_name(choice, first, last):
    first, last = (first or "").strip(), (last or "").strip()
    if choice == "anon":
        return "Anonymous"
    if choice == "initial":
        return (first + (" " + last[0] + "." if last else "")).strip() or "Anonymous"
    return (first + " " + last).strip() or "Anonymous"


def register(app, crm, leadership_required, get_db, send_email, full_name_sql, program_name):
    # ---------------- public: the private share link ----------------
    @app.route("/share/<token>", methods=["GET", "POST"])
    def public_share(token):
        _ensure(get_db)
        conn = get_db()
        req = conn.execute(
            f"""SELECT r.*, ct.first_name, ct.last_name FROM testimonial_requests r
                LEFT JOIN contacts ct ON ct.id = r.contact_id WHERE r.token = ?""", (token,)).fetchone()
        if not req:
            conn.close()
            return render_template("public_share.html", req=None), 404
        if req["submitted_at"]:
            conn.close()
            return render_template("public_share.html", req=req, already=True, done=False)
        if request.method == "POST":
            body = (request.form.get("body") or "").strip()[:8000]
            choice = request.form.get("name_choice") if request.form.get("name_choice") in dict(NAME_CHOICES) else "initial"
            f = request.files.get("video")
            vdata, vtype = None, None
            if f and f.filename:
                vdata = f.read(MAX_VIDEO_BYTES + 1)
                vtype = (f.mimetype or "video/mp4")[:60]
                if len(vdata) > MAX_VIDEO_BYTES:
                    flash("That video is too large. Please record a shorter one (up to 2 minutes) right on this page.")
                    conn.close()
                    return redirect(url_for("public_share", token=token))
                if not vdata:
                    vdata, vtype = None, None
            if not body and not vdata:
                flash("Please type, dictate or record something to share.")
                conn.close()
                return redirect(url_for("public_share", token=token))
            if not request.form.get("consent"):
                flash("Please tick the permission box so we may share your words.")
                conn.close()
                return redirect(url_for("public_share", token=token))
            program = req["program_code"] if req["program_code"] in PROGRAM_LABEL else "GEN"
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute(
                """INSERT INTO testimonials (request_id, contact_id, program_code, body, body_original, name_choice,
                       display_name, attended, video_data, video_type, status, consent_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'New', ?, ?)""",
                (req["id"], req["contact_id"], program, body, body, choice,
                 _display_name(choice, req["first_name"], req["last_name"]),
                 (request.form.get("attended") or "").strip()[:60], vdata, vtype, now, now))
            conn.execute("UPDATE testimonial_requests SET submitted_at = ? WHERE id = ?", (now, req["id"]))
            owners = conn.execute("SELECT email, name FROM staff WHERE is_owner = 1 AND active = 1").fetchall()
            conn.commit()
            conn.close()
            link = url_for("crm.testimonials_review", _external=True)
            for o in owners:
                try:
                    send_email(to_email=o["email"], to_name=o["name"], subject="A new testimonial is ready to review",
                               html_content=f"<p>A new testimonial came in{' (with a video)' if vdata else ''}.</p>"
                                            f"<p><a href=\"{link}\">Open Testimonials to review it</a></p>")
                except Exception as e:
                    print(f"testimonial notice failed: {e}", flush=True)
            return render_template("public_share.html", req=req, done=True, already=False)
        conn.close()
        return render_template("public_share.html", req=req, done=False, already=False, name_choices=NAME_CHOICES,
                               consent_text=CONSENT_TEXT, program_label=PROGRAM_LABEL.get(req["program_code"]),
                               max_mb=MAX_VIDEO_BYTES // (1024 * 1024))

    # ---------------- public: contributor confirms the edited version ----------------
    @app.route("/share/confirm/<token>", methods=["GET", "POST"])
    def public_share_confirm(token):
        _ensure(get_db)
        conn = get_db()
        t = conn.execute("""SELECT id, program_code, body, display_name, (video_data IS NOT NULL) AS has_video,
                                   confirm_state, confirm_token FROM testimonials WHERE confirm_token = ?""", (token,)).fetchone()
        if not t:
            conn.close()
            return render_template("public_share_confirm.html", t=None), 404
        if request.method == "POST" and t["confirm_state"] == "sent":
            yes = request.form.get("answer") == "yes"
            note = (request.form.get("note") or "").strip()[:2000]
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute("UPDATE testimonials SET confirm_state = ?, confirm_note = ?, confirm_at = ? WHERE id = ?",
                         ("yes" if yes else "changes", note, now, t["id"]))
            owners = conn.execute("SELECT email, name FROM staff WHERE is_owner = 1 AND active = 1").fetchall()
            conn.commit()
            conn.close()
            link = url_for("crm.testimonial_detail", tid=t["id"], _external=True)
            for o in owners:
                try:
                    send_email(to_email=o["email"], to_name=o["name"],
                               subject="Testimonial: " + ("approved by the contributor" if yes else "changes requested"),
                               html_content=f"<p>{_html.escape(t['display_name'])} "
                                            + ("approved the edited version." if yes else "asked for changes.")
                                            + (f"</p><p>Their note: {_html.escape(note)}" if note else "")
                                            + f"</p><p><a href=\"{link}\">Open the testimonial</a></p>")
                except Exception as e:
                    print(f"confirm notice failed: {e}", flush=True)
            return render_template("public_share_confirm.html", t=t, answered="yes" if yes else "changes")
        answered = t["confirm_state"] if t["confirm_state"] in ("yes", "changes") else None
        conn.close()
        return render_template("public_share_confirm.html", t=t, answered=answered, label=PROGRAM_LABEL.get(t["program_code"], ""))

    @app.route("/share/confirm/<token>/video")
    def public_share_confirm_video(token):
        _ensure(get_db)
        conn = get_db()
        row = conn.execute("SELECT video_data, video_type FROM testimonials WHERE confirm_token = ?", (token,)).fetchone()
        conn.close()
        if not row or row["video_data"] is None:
            abort(404)
        return send_file(io.BytesIO(bytes(row["video_data"])), mimetype=row["video_type"] or "video/mp4", conditional=True)

    # ---------------- public: Stories page ----------------
    @app.route("/stories")
    def public_stories():
        _ensure(get_db)
        conn = get_db()
        rows = conn.execute(
            """SELECT id, program_code, body, display_name, attended, (video_data IS NOT NULL) AS has_video
               FROM testimonials WHERE status = 'Approved' ORDER BY featured DESC, approved_at DESC, id DESC""").fetchall()
        conn.close()
        groups = []
        for code, label in STORY_PROGRAMS:
            items = [r for r in rows if r["program_code"] == code]
            if items:
                groups.append(dict(code=code, label=label, items=items))
        return render_template("public_stories.html", groups=groups, total=len(rows))

    @app.route("/stories/video/<int:tid>")
    def public_story_video(tid):
        _ensure(get_db)
        conn = get_db()
        row = conn.execute("SELECT video_data, video_type FROM testimonials WHERE id = ? AND status = 'Approved'", (tid,)).fetchone()
        conn.close()
        if not row or row["video_data"] is None:
            abort(404)
        return send_file(io.BytesIO(bytes(row["video_data"])), mimetype=row["video_type"] or "video/mp4", conditional=True)

    # ---------------- CRM ----------------
    @crm.route("/testimonials")
    @leadership_required
    def testimonials_review():
        _ensure(get_db)
        conn = get_db()
        rows = conn.execute(
            f"""SELECT r.id AS req_id, r.program_code AS req_program, r.created_at, r.sent_at, r.token,
                       {full_name_sql('ct')} AS full_name, ct.id AS contact_id,
                       t.id AS tid, t.status, t.confirm_state, t.display_name, t.body, (t.video_data IS NOT NULL) AS has_video
                FROM testimonial_requests r
                LEFT JOIN contacts ct ON ct.id = r.contact_id
                LEFT JOIN testimonials t ON t.request_id = r.id
                ORDER BY r.id DESC""").fetchall()
        conn.close()
        items = []
        order = {"review": 0, "changes": 1, "ready": 2, "waiting_ok": 3, "waiting": 4, "published": 5, "hidden": 6}
        for r in rows:
            d = dict(r)
            if d["tid"] is None:
                d["stage"], d["label"], d["action"] = "waiting", "Waiting for their story", ""
            elif d["status"] == "Approved":
                d["stage"], d["label"], d["action"] = "published", "Published", "Open"
            elif d["status"] == "Hidden":
                d["stage"], d["label"], d["action"] = "hidden", "Hidden", "Open"
            elif d["confirm_state"] == "changes":
                d["stage"], d["label"], d["action"] = "changes", "They asked for changes", "Open"
            elif d["confirm_state"] == "yes":
                d["stage"], d["label"], d["action"] = "ready", "They approved: ready to publish", "Open"
            elif d["confirm_state"] == "sent":
                d["stage"], d["label"], d["action"] = "waiting_ok", "Sent back; waiting for their OK", "Open"
            else:
                d["stage"], d["label"], d["action"] = "review", "Received: needs your review", "Review"
            d["how"] = "Emailed " + d["sent_at"] if d["sent_at"] else "Link only"
            items.append(d)
        items.sort(key=lambda x: (order[x["stage"]], -x["req_id"]))
        counts = {k: sum(1 for i in items if i["stage"] == k) for k in order}
        return render_template("testimonials_review.html", items=items, counts=counts, labels=PROGRAM_LABEL,
                               show=request.args.get("show") or "")

    @crm.route("/testimonials/request/<int:req_id>/delete", methods=["POST"])
    @leadership_required
    def testimonial_request_delete(req_id):
        _ensure(get_db)
        conn = get_db()
        conn.execute("DELETE FROM testimonials WHERE request_id = ?", (req_id,))
        conn.execute("DELETE FROM testimonial_requests WHERE id = ?", (req_id,))
        conn.commit()
        conn.close()
        flash("Deleted. The link no longer works.")
        return redirect(url_for(".testimonials_review"))

    @crm.route("/testimonials/request", methods=["GET", "POST"])
    @leadership_required
    def testimonial_request():
        _ensure(get_db)
        conn = get_db()
        result = None
        if request.method == "POST":
            m = re.fullmatch(r"\d+", (request.form.get("contact_id") or "").strip())
            program = request.form.get("program") if request.form.get("program") in PROGRAM_LABEL else "GEN"
            note = (request.form.get("note") or "").strip()[:1000]
            ct = conn.execute(f"SELECT id, first_name, email, {full_name_sql()} AS full_name FROM contacts WHERE id = ?",
                              (int(m.group(0)),)).fetchone() if m else None
            if not ct:
                flash("Pick a contact from the list (start typing their name).")
            else:
                token = secrets.token_urlsafe(16)
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                conn.execute("""INSERT INTO testimonial_requests (token, contact_id, program_code, note, created_at)
                                VALUES (?, ?, ?, ?, ?)""", (token, ct["id"], program, note, now))
                conn.commit()
                link = url_for("public_share", token=token, _external=True)
                first = (ct["first_name"] or ct["full_name"] or "there").strip()
                what = PROGRAM_LABEL[program] if program != "GEN" else "Discovery!"
                sms = (f"Hi {first}, it's Kent. Would you be willing to share a few words (or a short video) about your "
                       f"experience with {what}? You can speak it, type it or record it here: {link}")
                emailed, err = False, ""
                if request.form.get("action") == "email":
                    if not ct["email"]:
                        err = "This contact has no email address, so use the text message below instead."
                    else:
                        body = (f"<p>Hi {_html.escape(first)},</p>"
                                f"<p>Would you be willing to share a few words, or a short video, about your experience with "
                                f"<b>{_html.escape(what)}</b>? It only takes a couple of minutes. You can speak it, type it, "
                                f"or record yourself. Your story may help someone take their first step.</p>"
                                + (f"<p>{_html.escape(note).replace(chr(10), '<br>')}</p>" if note else "")
                                + f"<p><a href=\"{link}\" style=\"background:#0c4561;color:#fff;padding:12px 22px;border-radius:6px;"
                                  f"text-decoration:none;display:inline-block\">Share your experience</a></p>"
                                  f"<p>Thank you,<br>Kent</p>")
                        try:
                            send_email(to_email=ct["email"], to_name=ct["full_name"],
                                       subject=f"Would you share your {what} experience?", html_content=body,
                                       bcc=DEFAULT_FROM_EMAIL)
                            emailed = True
                            conn.execute("UPDATE testimonial_requests SET sent_at = ?, sent_by = ? WHERE token = ?",
                                         (now, session.get("staff_name") or "", token))
                            conn.commit()
                        except Exception as e:
                            err = f"The email could not be sent ({e}). You can text the link below instead."
                result = dict(name=ct["full_name"], link=link, sms=sms, emailed=emailed, err=err)
        contacts = conn.execute(f"SELECT id, {full_name_sql()} AS full_name, email, cell_phone, home_phone FROM contacts ORDER BY last_name, first_name").fetchall()
        conn.close()
        # Pick-list text shows name and email (no database numbers). Duplicates get the phone, then a counter.
        seen, picks = {}, []
        for c in contacts:
            key = c["full_name"] + (" \u2014 " + c["email"] if c["email"] else "")
            if key in seen:
                key += " \u2014 " + (c["cell_phone"] or c["home_phone"] or "no phone")
            if key in seen:
                seen[key] += 1
                key += f" ({seen[key]})"
            else:
                seen[key] = 1
            picks.append(dict(key=key, id=c["id"], email=c["email"] or "", phone=c["cell_phone"] or c["home_phone"] or ""))
        return render_template("testimonial_request.html", picks=picks, programs=STORY_PROGRAMS, result=result)

    @crm.route("/testimonials/<int:tid>", methods=["GET", "POST"])
    @leadership_required
    def testimonial_detail(tid):
        _ensure(get_db)
        conn = get_db()
        t = conn.execute("""SELECT id, program_code, body, body_original, name_choice, display_name, attended, video_type,
                                   (video_data IS NOT NULL) AS has_video, status, featured, consent_at, created_at, contact_id,
                                   confirm_state, confirm_note, confirm_at
                            FROM testimonials WHERE id = ?""", (tid,)).fetchone()
        if not t:
            conn.close()
            abort(404)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "delete":
                req = conn.execute("SELECT request_id FROM testimonials WHERE id = ?", (tid,)).fetchone()
                conn.execute("DELETE FROM testimonials WHERE id = ?", (tid,))
                if req and req["request_id"]:
                    conn.execute("DELETE FROM testimonial_requests WHERE id = ?", (req["request_id"],))
                conn.commit()
                conn.close()
                flash("Testimonial deleted.")
                return redirect(url_for(".testimonials_review"))
            program = request.form.get("program") if request.form.get("program") in PROGRAM_LABEL else t["program_code"]
            vf = request.files.get("video")
            if vf and vf.filename:
                vd = vf.read(MAX_VIDEO_BYTES + 1)
                if vd and len(vd) <= MAX_VIDEO_BYTES:
                    conn.execute("UPDATE testimonials SET video_data = ?, video_type = ?, confirm_state = NULL WHERE id = ?",
                                 (vd, (vf.mimetype or "video/mp4")[:60], tid))
                else:
                    flash("That video file is empty or larger than %d MB, so it was not replaced." % (MAX_VIDEO_BYTES // (1024 * 1024)))
            status = {"approve": "Approved", "hide": "Hidden", "new": "New"}.get(action, t["status"])
            send_back = action == "send_back"
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute(
                """UPDATE testimonials SET body = ?, display_name = ?, attended = ?, program_code = ?, featured = ?, status = ?,
                          approved_at = CASE WHEN ? = 'Approved' THEN ? ELSE approved_at END WHERE id = ?""",
                ((request.form.get("body") or "").strip()[:8000], (request.form.get("display_name") or "").strip()[:80] or "Anonymous",
                 (request.form.get("attended") or "").strip()[:60], program, 1 if request.form.get("featured") else 0,
                 status, status, now, tid))
            msg = {"approve": "Approved and published on the Stories page.", "hide": "Hidden from the website."}.get(action, "Saved.")
            if send_back:
                ct = conn.execute(f"SELECT first_name, email, {full_name_sql()} AS full_name FROM contacts WHERE id = ?",
                                  (t["contact_id"],)).fetchone() if t["contact_id"] else None
                if not ct or not ct["email"]:
                    msg = "Saved, but this contact has no email address, so it could not be sent back."
                else:
                    token = secrets.token_urlsafe(16)
                    conn.execute("UPDATE testimonials SET confirm_token = ?, confirm_state = 'sent', confirm_note = NULL, confirm_at = NULL WHERE id = ?",
                                 (token, tid))
                    conn.commit()
                    link = url_for("public_share_confirm", token=token, _external=True)
                    first = (ct["first_name"] or ct["full_name"] or "there").strip()
                    try:
                        send_email(to_email=ct["email"], to_name=ct["full_name"], subject="Please check your story before we share it", bcc=DEFAULT_FROM_EMAIL,
                                   html_content=(f"<p>Hi {_html.escape(first)},</p><p>Thank you for sharing your experience. We made a few "
                                                 f"small edits for length and clarity. Before anything is published, please look at it and "
                                                 f"tell us it is right, or ask for changes.</p>"
                                                 f"<p><a href=\"{link}\" style=\"background:#0c4561;color:#fff;padding:12px 22px;"
                                                 f"border-radius:6px;text-decoration:none;display:inline-block\">Review my story</a></p>"
                                                 f"<p>Nothing goes on the website until you say yes.</p><p>Thank you,<br>Kent</p>"))
                        msg = "Saved and sent back to " + ct["full_name"] + " for their approval."
                    except Exception as e:
                        msg = f"Saved, but the email could not be sent ({e})."
            conn.commit()
            conn.close()
            flash(msg)
            return redirect(url_for(".testimonial_detail", tid=tid))
        conn.close()
        return render_template("testimonial_detail.html", t=t, programs=STORY_PROGRAMS, flags=flagged_words(t["body"]))

    @crm.route("/testimonials/<int:tid>/video")
    @leadership_required
    def testimonial_video(tid):
        _ensure(get_db)
        conn = get_db()
        row = conn.execute("SELECT video_data, video_type FROM testimonials WHERE id = ?", (tid,)).fetchone()
        conn.close()
        if not row or row["video_data"] is None:
            abort(404)
        ext = "webm" if "webm" in (row["video_type"] or "") else "mp4"
        dl = bool(request.args.get("dl"))
        return send_file(io.BytesIO(bytes(row["video_data"])), mimetype=row["video_type"] or "video/mp4", conditional=True,
                         as_attachment=dl, download_name=f"testimonial_{tid}.{ext}")
