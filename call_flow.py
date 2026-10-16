"""
"Have a Discovery team member call you" -- the interested-party call flow.

How it works
  1. Someone fills in the short form (front page, program pages, or the Reserve form) and answers Yes/No to
     "Do we have permission to call and text you on your cell phone?"
  2. On Yes, a call request is saved and the volunteers for that program (set up on the Call volunteers page,
     up to six per program, plus a CEP Team Captain) are alerted. Each alert has a personal link.
  3. The first volunteer to open their link and tap "I'll call within 2 hours" gets the request. The others see
     "already claimed". The interested person is then told the volunteer's name and number.
  4. After calling, the volunteer logs the outcome on the same link.
  5. If nobody claims it in 2 hours, the volunteers are alerted again and Kent is emailed. If a volunteer claims it
     but hasn't logged the call after 2 hours, they get a reminder.

Safety switches (nothing is sent to real people until you turn this on)
  CALL_ALERTS_LIVE=1   in Render's Environment turns the emails on. Until then requests are only saved and shown
                       on the Call requests page.
  Alerts only go out between 8 am and 8 pm Central; anything that comes in overnight goes out at 8 am.

Texting is not built yet (it needs a texting service and registration). The messages are emails for now; the
consent answer and wording are saved so texting can be added later.
"""
import os
import secrets
import time
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, abort

try:
    from zoneinfo import ZoneInfo
    CHI = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CHI = None

POOLS = [
    ("CAPTAIN", "CEP Team Captain (oversees the team)"),
    ("D1", "D1 – The Beginning"),
    ("D4", "Relationship Training"),
    ("ALL", "Everything else (also the back-up if a program has no volunteers)"),
]
MAX_PER_POOL = 6


def max_for(code):
    return 1 if code == "CAPTAIN" else MAX_PER_POOL
PAGES = {"home": "Front page", "programs": "Programs page", "relationship": "Relationship Training page", "reserve": "Reserve your spot form", "other": "Other"}
WINDOW_HOURS = 2
SEND_FROM_HOUR, SEND_TO_HOUR = 8, 20   # Central time
MAX_ALERT_ROUNDS = 4
OUTCOMES = ["Spoke with them", "Left a voicemail", "Texted \u2013 waiting for a reply", "No answer – will try again", "Not interested", "Wrong number"]

CONSENT_QUESTION = "Do we have permission to text or call you?"
CONSENT_OPTIONS = [("either", "Yes, call or text is fine"), ("text", "Yes, but text only"),
                   ("call", "Yes, but call only"), ("email", "Yes, but please email me")]
PREF_WORD = {"either": "call or text", "text": "text", "call": "call"}
PREF_SHORT = {"either": "Call or text", "text": "Text only", "call": "Call only", "email": "Email only"}
LEGACY = {"yes": "either", "no": "email"}
CONSENT_FINE = ("We call between 8 am and 8 pm Central, usually within two hours. "
                "Message and data rates may apply. Reply STOP to stop texts.")
CONSENT_WORDING = CONSENT_QUESTION + " (" + CONSENT_FINE + ")"

_ready = False
_programs_cache = {"at": 0, "rows": []}


# ---------------------------------------------------------------- helpers
def _utc_now():
    return datetime.utcnow()


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s):
    return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")


def local_text(s):
    """UTC timestamp text -> 'Oct 15, 4:08 pm' in Central time."""
    if not s:
        return ""
    try:
        d = _parse(s)
        if CHI:
            from datetime import timezone
            d = d.replace(tzinfo=timezone.utc).astimezone(CHI)
        return d.strftime("%b %-d, %-I:%M %p").replace("AM", "am").replace("PM", "pm")
    except Exception:
        return str(s)


def alerts_live():
    return (os.environ.get("CALL_ALERTS_LIVE") or "").strip() == "1"


def in_send_hours(now_utc=None):
    now_utc = now_utc or _utc_now()
    if CHI:
        from datetime import timezone
        local = now_utc.replace(tzinfo=timezone.utc).astimezone(CHI)
    else:
        local = now_utc - timedelta(hours=5)
    return SEND_FROM_HOUR <= local.hour < SEND_TO_HOUR


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


def pretty_phone(s):
    d = _digits(s)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"{d[0:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else (s or "")


def _base_url():
    return (os.environ.get("PUBLIC_BASE_URL") or request.url_root).rstrip("/")


def _program_label(code):
    for c, label in POOLS:
        if c == code:
            return label
    return code or "Discovery"


def ensure_tables(get_db):
    global _ready
    if _ready:
        return
    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS call_volunteers (
            id SERIAL PRIMARY KEY,
            program_code TEXT NOT NULL,
            name TEXT NOT NULL,
            cell_phone TEXT NOT NULL,
            email TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS call_requests (
            id SERIAL PRIMARY KEY,
            contact_id INTEGER NOT NULL,
            program_code TEXT,
            cell_phone TEXT,
            note TEXT,
            source TEXT,
            contact_pref TEXT,
            consent_text TEXT,
            consent_at TEXT,
            consent_ip TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'New',
            first_alert_at TEXT,
            last_alert_at TEXT,
            alert_rounds INTEGER NOT NULL DEFAULT 0,
            claimed_by INTEGER,
            claimed_at TEXT,
            reminder_at TEXT,
            prospect_notified_at TEXT,
            outcome TEXT,
            outcome_note TEXT,
            closed_at TEXT)""")
        conn.execute("ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS contact_pref TEXT")
        for col in ("reserve_token TEXT", "link_sent_at TEXT", "link_sent_via TEXT", "reserved_at TEXT",
                    "reserve_result TEXT", "reserve_cohort_id INTEGER", "partner_name TEXT", "partner_email TEXT"):
            conn.execute("ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS " + col)
        conn.execute("ALTER TABLE cohorts ADD COLUMN IF NOT EXISTS capacity INTEGER")
        conn.execute("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS partner_enrollment_id INTEGER")
        conn.execute("""CREATE TABLE IF NOT EXISTS call_notices (
            id SERIAL PRIMARY KEY,
            request_id INTEGER NOT NULL,
            volunteer_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            sent_at TEXT,
            send_error TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS interest_log (
            id SERIAL PRIMARY KEY,
            contact_id INTEGER NOT NULL,
            program_code TEXT,
            page TEXT,
            call_ok TEXT,
            created_at TEXT NOT NULL)""")
        conn.commit()
        _ready = True
    except Exception as e:
        conn.rollback()
        print(f"Couldn't set up the call-request tables: {e}", flush=True)
    finally:
        conn.close()



# ---------------------------------------------------------------- seats, dates, the personal reservation link
DEFAULT_CAPACITY = {"D4": 40}     # Relationship Training: 40 COUPLES unless the session says otherwise
COUPLE_PROGRAMS = ("D4",)


def capacity_of(cohort, code):
    """Seats for a session (couples for D4, people otherwise), or None for no limit."""
    try:
        cap = cohort["capacity"]
    except (KeyError, IndexError):
        cap = None
    if cap:
        return int(cap)
    return DEFAULT_CAPACITY.get(code)


def _loc(c):
    try:
        return c["location"]
    except (KeyError, IndexError):
        return None


def seats_used(conn, cohort_id, code):
    rows = conn.execute("SELECT id, partner_enrollment_id AS pe FROM enrollments WHERE cohort_id = ?",
                        (cohort_id,)).fetchall()
    if code in COUPLE_PROGRAMS:
        return sum(1 for r in rows if not r["pe"] or r["id"] < r["pe"])
    return len(rows)


def ensure_capacity_column(get_db):
    """Called by the session form so the 'seats' box works the first time it is used."""
    conn = get_db()
    try:
        conn.execute("ALTER TABLE cohorts ADD COLUMN IF NOT EXISTS capacity INTEGER")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Couldn't add cohorts.capacity: {e}", flush=True)
    finally:
        conn.close()


def span_text(c):
    def one(d):
        try:
            return datetime.strptime(str(d)[:10], "%Y-%m-%d")
        except ValueError:
            return None
    a, b = one(c["session_date"]), one(c["end_date"]) if c["end_date"] else None
    if not a:
        return str(c["session_date"])
    if not b or b == a:
        return a.strftime("%B ") + str(a.day) + a.strftime(", %Y")
    if a.month == b.month and a.year == b.year:
        return a.strftime("%B ") + f"{a.day}\u2013{b.day}" + a.strftime(", %Y")
    return a.strftime("%B ") + str(a.day) + " \u2013 " + b.strftime("%B ") + str(b.day) + b.strftime(", %Y")


def reserve_link(conn, req, base_url):
    """The person's personal Reserve link (made the first time it is needed)."""
    tok = req["reserve_token"]
    if not tok:
        tok = secrets.token_urlsafe(16)
        conn.execute("UPDATE call_requests SET reserve_token = ? WHERE id = ?", (tok, req["id"]))
        conn.commit()
    return f"{base_url}/reserve/{tok}"


def _find_or_make_contact(conn, first, last, email, phone, note):
    row = conn.execute("SELECT id FROM contacts WHERE lower(email) = lower(?) ORDER BY id LIMIT 1", (email,)).fetchone()
    if row:
        conn.execute("UPDATE contacts SET cell_phone = COALESCE(NULLIF(cell_phone, ''), ?) WHERE id = ?", (phone or None, row["id"]))
        return row["id"]
    cur = conn.execute(
        """INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, marketing_source, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", (first, last or None, email, phone or None, note, "Website", "Interested Party"))
    return cur.lastrowid


def _add_note(conn, contact_id, line):
    cur = conn.execute("SELECT notes FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    merged = ((cur["notes"] + "\n") if cur and cur["notes"] else "") + line
    conn.execute("UPDATE contacts SET notes = ? WHERE id = ?", (merged, contact_id))


def reserve_seat(get_db, req_id, cohort_id, person, partner):
    """Enrolls the person (and partner, for couples) or puts them on the waitlist when the date is full.
    Returns ("Enrolled" | "Waitlist" | "Already", cohort_row, message)."""
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM call_requests WHERE id = ?", (req_id,)).fetchone()
        coh = conn.execute("""SELECT c.*, p.code, p.name AS pname FROM cohorts c JOIN programs p ON p.id = c.program_id
                              WHERE c.id = ?""", (cohort_id,)).fetchone()
        if not coh or coh["code"] != (req["program_code"] or coh["code"]):
            return "Error", None, "Please choose one of the dates in the list."
        code = coh["code"]
        couple = code in COUPLE_PROGRAMS
        label = f"{code} #{coh['session_number']}, {span_text(coh)}"
        cid = req["contact_id"]
        conn.execute("UPDATE contacts SET first_name = ?, last_name = ?, email = ?, cell_phone = ? WHERE id = ?",
                     (person["first"], person["last"] or None, person["email"], person["phone"] or None, cid))
        pid = None
        if couple:
            pid = _find_or_make_contact(conn, partner["first"], partner["last"], partner["email"], "",
                                        f"Partner of {person['first']} {person['last']} (reserved {label} by personal link)")
            if pid == cid:
                conn.rollback()
                return "Error", None, "Your partner needs their own name and email address."
        have = conn.execute("SELECT id FROM enrollments WHERE contact_id = ? AND cohort_id = ?", (cid, cohort_id)).fetchone()
        if have:
            conn.rollback()
            return "Already", coh, ""
        cap = capacity_of(coh, code)
        used = seats_used(conn, cohort_id, code)
        result = "Enrolled"
        if cap is not None and used + 1 > cap:
            result = "Waitlist"
        else:
            e1 = conn.execute("INSERT INTO enrollments (contact_id, cohort_id) VALUES (?, ?)", (cid, cohort_id)).lastrowid
            e2 = None
            if couple:
                row = conn.execute("SELECT id FROM enrollments WHERE contact_id = ? AND cohort_id = ?", (pid, cohort_id)).fetchone()
                e2 = row["id"] if row else conn.execute(
                    "INSERT INTO enrollments (contact_id, cohort_id) VALUES (?, ?)", (pid, cohort_id)).lastrowid
                conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (e2, e1))
                conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (e1, e2))
            # two people finishing at the same moment: recount, and if we went over, step back to the waitlist
            if cap is not None and seats_used(conn, cohort_id, code) > cap:
                for e in (e1, e2):
                    if e:
                        conn.execute("DELETE FROM enrollments WHERE id = ?", (e,))
                result = "Waitlist"
        now = _ts(_utc_now())
        conn.execute("""UPDATE call_requests SET reserved_at = ?, reserve_result = ?, reserve_cohort_id = ?,
                        partner_name = ?, partner_email = ? WHERE id = ?""",
                     (now, result, cohort_id, (f"{partner['first']} {partner['last']}".strip() if couple else None),
                      (partner["email"] if couple else None), req_id))
        _add_note(conn, cid, f"{'Reserved' if result == 'Enrolled' else 'WAITLIST for'} {label} by personal link, "
                             f"{local_text(now)}" + (f" (with {partner['first']} {partner['last']})" if couple else "")
                  + ("; payment pending" if result == "Enrolled" else ""))
        conn.commit()
        if result == "Waitlist":
            _tell_waitlist(conn, req, person, partner if couple else None, label)
        return result, coh, ""
    except Exception as e:
        conn.rollback()
        print(f"Reservation failed for request {req_id}: {e}", flush=True)
        return "Error", None, "Sorry, something went wrong on our end. Please try again in a moment."
    finally:
        conn.close()


def _tell_waitlist(conn, req, person, partner, label):
    if not alerts_live():
        return
    who = f"{person['first']} {person['last']}".strip() + (f" and {partner['first']} {partner['last']}".rstrip() if partner else "")
    subject = f"Full date \u2013 {who} need another date"
    html = (f"<p><strong>{who}</strong> tried to reserve <strong>{label}</strong>, but that date is full.</p>"
            f"<p>Cell: {pretty_phone(person['phone'])} &middot; {person['email']}</p>"
            f"<p>Please reach out to find them another date. They were told a team member would.</p>")
    text = (f"{who} tried to reserve {label}, but that date is full.\nCell: {pretty_phone(person['phone'])}  {person['email']}\n"
            f"Please reach out to find them another date. They were told a team member would.")
    to = [_leadership_email()]
    if req["claimed_by"]:
        v = conn.execute("SELECT email FROM call_volunteers WHERE id = ?", (req["claimed_by"],)).fetchone()
        if v and v["email"]:
            to.append(v["email"])
    for addr in to:
        try:
            _send(addr, subject, html, text)
        except Exception as e:
            print(f"Couldn't send waitlist note to {addr}: {e}", flush=True)

# ---------------------------------------------------------------- creating a request
def create_interest(get_db, form, source):
    """Saves a person from one of the public forms. Returns (ok, how_they_want_to_be_reached, error_message)."""
    first = (form.get("first_name") or "").strip()
    last = (form.get("last_name") or "").strip() or None
    email = (form.get("email") or "").strip()
    phone = (form.get("phone") or "").strip()
    program = (form.get("program_code") or "").strip().upper()
    message = (form.get("message") or "").strip()
    consent = (form.get("call_ok") or "").strip().lower()
    consent = LEGACY.get(consent, consent)
    if consent not in PREF_SHORT:
        consent = ""
    if form.get("website"):            # hidden spam-trap field: real people leave it empty
        return True, False, None
    if not first or not email:
        return False, False, "Please enter your first name and email."
    if consent in PREF_WORD and len(_digits(phone)) < 10:
        return False, False, "To have someone call or text you, please enter your cell phone number."

    ensure_tables(get_db)
    conn = get_db()
    try:
        notes = []
        if program:
            notes.append(f"Interested in: {program}")
        if message:
            notes.append(message)
        notes.append(f"(Submitted via website: {source})")
        existing = conn.execute("SELECT id, cell_phone, notes FROM contacts WHERE lower(email) = lower(?) ORDER BY id LIMIT 1",
                                (email,)).fetchone()
        if existing:
            cid = existing["id"]
            new_notes = ((existing["notes"] or "") + "\n" if existing["notes"] else "") + "\n".join(notes)
            conn.execute("UPDATE contacts SET notes = ?, cell_phone = COALESCE(NULLIF(cell_phone, ''), ?) WHERE id = ?",
                         (new_notes, phone or None, cid))
        else:
            cur = conn.execute(
                """INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, marketing_source, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (first, last, email, phone or None, "\n".join(notes), "Website", "Interested Party"))
            cid = cur.lastrowid
        page = (form.get("page") or "").strip().lower()
        if page not in PAGES:
            page = "other"
        conn.execute("INSERT INTO interest_log (contact_id, program_code, page, call_ok, created_at) VALUES (?, ?, ?, ?, ?)",
                     (cid, program or None, page, consent or None, _ts(_utc_now())))
        req_id = None
        if consent in PREF_WORD:
            recent = conn.execute(
                """SELECT id FROM call_requests WHERE contact_id = ? AND status IN ('New', 'Claimed') AND created_at >= ?""",
                (cid, _ts(_utc_now() - timedelta(hours=24)))).fetchone()
            if not recent:
                ip = (request.headers.get("X-Forwarded-For", "") or request.remote_addr or "").split(",")[0].strip()
                now = _ts(_utc_now())
                cur = conn.execute(
                    """INSERT INTO call_requests (contact_id, program_code, cell_phone, note, source, consent_text,
                                                  consent_at, consent_ip, created_at, contact_pref)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cid, program or None, phone, message or None, source,
                     f"{CONSENT_WORDING} Answer: {dict(CONSENT_OPTIONS).get(consent, consent)}", now, ip, now, consent))
                req_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Interest form failed: {e}", flush=True)
        return False, False, "Sorry, something went wrong on our end. Please try again in a moment."
    finally:
        conn.close()
    if req_id:
        try:
            dispatch(get_db, req_id, _base_url())
        except Exception as e:
            print(f"Call alert dispatch failed: {e}", flush=True)
    return True, consent, None


# ---------------------------------------------------------------- alerts
def _pool(conn, program_code):
    rows = conn.execute("SELECT * FROM call_volunteers WHERE active = 1 AND program_code = ? ORDER BY id",
                        (program_code or "",)).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM call_volunteers WHERE active = 1 AND program_code = 'ALL' ORDER BY id").fetchall()
    return rows


def _send(to_email, subject, html, text, to_name=None):
    from email_utils import send_email
    return send_email(to_email, subject, html, to_name=to_name, text_content=text)


def _leadership_email():
    return os.environ.get("EMAIL_FROM_ADDRESS", "kent@blueskyusa.net")


def _notice_for(conn, request_id, volunteer_id):
    row = conn.execute("SELECT token FROM call_notices WHERE request_id = ? AND volunteer_id = ?",
                       (request_id, volunteer_id)).fetchone()
    if row:
        return row["token"]
    token = secrets.token_urlsafe(16)
    conn.execute("INSERT INTO call_notices (request_id, volunteer_id, token) VALUES (?, ?, ?)",
                 (request_id, volunteer_id, token))
    return token


def dispatch(get_db, request_id, base_url, force=False):
    """Alert the volunteers for a still-unclaimed request. Returns a short status word."""
    ensure_tables(get_db)
    if not alerts_live():
        return "held (sending is off)"
    if not in_send_hours():
        return "waiting for 8 am"
    conn = get_db()
    try:
        req = conn.execute("SELECT r.*, c.first_name, c.last_name FROM call_requests r JOIN contacts c ON c.id = r.contact_id "
                           "WHERE r.id = ?", (request_id,)).fetchone()
        if not req or req["status"] != "New":
            return "not needed"
        now = _utc_now()
        first_round = req["first_alert_at"] is None
        if not first_round and not force:
            if _parse(req["last_alert_at"]) > now - timedelta(hours=WINDOW_HOURS):
                return "not due yet"
            if req["alert_rounds"] >= MAX_ALERT_ROUNDS:
                return "gave up re-alerting"
        # take the round atomically so two workers don't both send it
        claimed = conn.execute(
            "UPDATE call_requests SET last_alert_at = ?, alert_rounds = alert_rounds + 1, "
            "first_alert_at = COALESCE(first_alert_at, ?) WHERE id = ? AND status = 'New' AND alert_rounds = ?",
            (_ts(now), _ts(now), request_id, req["alert_rounds"]))
        conn.commit()
        if claimed.rowcount != 1:
            return "someone else is sending"
        vols = _pool(conn, req["program_code"])
        label = _program_label(req["program_code"])
        who = f"{req['first_name']} {(req['last_name'] or '')[:1]}.".replace(" .", "")
        sent_to = 0
        for v in vols:
            token = _notice_for(conn, request_id, v["id"])
            url = f"{base_url}/call/{token}"
            again = "" if first_round else " (second reminder: no one has claimed this yet)"
            pw = PREF_WORD.get(req["contact_pref"] or "either", "call or text")
            subject = f"Please reach out to {who} within 2 hours – {label}"
            html = (f"<p>Hi {v['name'].split()[0]},</p>"
                    f"<p><strong>{who}</strong> asked to be reached about <strong>{label}</strong>{again}. "
                    f"They prefer: <strong>{PREF_SHORT.get(req['contact_pref'] or 'either')}</strong>.</p>"
                    f"<p>Can you {pw} them within two hours? Tap here to say yes:<br>"
                    f"<a href=\"{url}\">{url}</a></p>"
                    f"<p>If another volunteer already took it, the page will tell you. Thank you for helping!</p>")
            text = (f"Hi {v['name'].split()[0]},\n\n{who} asked to be reached about {label}{again}. "
                    f"They prefer: {PREF_SHORT.get(req['contact_pref'] or 'either')}.\n\n"
                    f"Can you {pw} them within two hours? Tap here to say yes:\n{url}\n\n"
                    f"If another volunteer already took it, the page will tell you. Thank you for helping!")
            try:
                _send(v["email"], subject, html, text, to_name=v["name"])
                conn.execute("UPDATE call_notices SET sent_at = ?, send_error = NULL WHERE request_id = ? AND volunteer_id = ?",
                             (_ts(_utc_now()), request_id, v["id"]))
                sent_to += 1
            except Exception as e:
                conn.execute("UPDATE call_notices SET send_error = ? WHERE request_id = ? AND volunteer_id = ?",
                             (str(e)[:200], request_id, v["id"]))
        conn.commit()
        if not first_round or not vols or sent_to == 0:
            why = ("No volunteers are set up for this program." if not vols
                   else "No volunteer could be emailed." if sent_to == 0
                   else "Nobody has claimed this request in 2 hours; the volunteers were alerted again.")
            to_list = [_leadership_email()]
            cap = conn.execute("SELECT email FROM call_volunteers WHERE active = 1 AND program_code = 'CAPTAIN' ORDER BY id LIMIT 1").fetchone()
            if cap and cap["email"] and cap["email"].lower() not in [x.lower() for x in to_list]:
                to_list.append(cap["email"])
            for addr in to_list:
                try:
                    _send(addr, f"Unclaimed call request: {who} ({label})",
                          f"<p>{why}</p><p>Open Call requests in the CRM to follow up: "
                          f"<a href=\"{base_url}/crm/call-requests\">{base_url}/crm/call-requests</a></p>",
                          f"{why}\n\nOpen Call requests in the CRM: {base_url}/crm/call-requests")
                except Exception as e:
                    print(f"Couldn't email {addr} about call request {request_id}: {e}", flush=True)
        return f"alerted {sent_to}"
    finally:
        conn.close()


def _tell_prospect(conn, req, vol, base_url):
    if not alerts_live():
        return
    contact = conn.execute("SELECT first_name, email FROM contacts WHERE id = ?", (req["contact_id"],)).fetchone()
    if not contact or not contact["email"]:
        return
    label = _program_label(req["program_code"])
    vphone = pretty_phone(vol["cell_phone"])
    pw = PREF_WORD.get(req["contact_pref"] or "either", "call or text")
    subject = f"Your Discovery team member will {pw} you"
    html = (f"<p>Hi {contact['first_name']},</p>"
            f"<p>Thank you for your interest in {label}. <strong>{vol['name']}</strong> from our Discovery team will {pw} you "
            f"from <strong>{vphone}</strong> within about two hours (between 8 am and 8 pm Central).</p>"
            f"<p>If it isn&rsquo;t a good time, just reply to this email and we&rsquo;ll find a better one.</p>"
            f"<p>We&rsquo;re looking forward to meeting you!</p><p>Warmly,<br>Coach Kent and the Discovery Team</p>")
    text = (f"Hi {contact['first_name']},\n\nThank you for your interest in {label}. {vol['name']} from our Discovery team will "
            f"{pw} you from {vphone} within about two hours (between 8 am and 8 pm Central).\n\n"
            f"If it isn't a good time, just reply to this email and we'll find a better one.\n\n"
            f"We're looking forward to meeting you!\n\nWarmly,\nCoach Kent and the Discovery Team")
    try:
        _send(contact["email"], subject, html, text, to_name=contact["first_name"])
        conn.execute("UPDATE call_requests SET prospect_notified_at = ? WHERE id = ?", (_ts(_utc_now()), req["id"]))
    except Exception as e:
        print(f"Couldn't email the interested person for request {req['id']}: {e}", flush=True)


def sweep(get_db, base_url):
    """Run every few minutes: first alerts held overnight, 2-hour re-alerts, and 'please log your call' reminders."""
    ensure_tables(get_db)
    if not alerts_live():
        return
    conn = get_db()
    try:
        open_new = [r["id"] for r in conn.execute("SELECT id FROM call_requests WHERE status = 'New' ORDER BY id").fetchall()]
        stale = conn.execute(
            "SELECT r.id, r.claimed_by, r.contact_id, r.program_code FROM call_requests r WHERE r.status = 'Claimed' "
            "AND r.reminder_at IS NULL AND r.claimed_at <= ?", (_ts(_utc_now() - timedelta(hours=WINDOW_HOURS)),)).fetchall()
    finally:
        conn.close()
    for rid in open_new:
        dispatch(get_db, rid, base_url)
    if stale and in_send_hours():
        conn = get_db()
        try:
            for r in stale:
                vol = conn.execute("SELECT * FROM call_volunteers WHERE id = ?", (r["claimed_by"],)).fetchone()
                tok = conn.execute("SELECT token FROM call_notices WHERE request_id = ? AND volunteer_id = ?",
                                   (r["id"], r["claimed_by"])).fetchone()
                done = conn.execute("UPDATE call_requests SET reminder_at = ? WHERE id = ? AND reminder_at IS NULL",
                                    (_ts(_utc_now()), r["id"]))
                conn.commit()
                if done.rowcount == 1 and vol and tok:
                    url = f"{base_url}/call/{tok['token']}"
                    try:
                        _send(vol["email"], "Please log your call",
                              f"<p>Hi {vol['name'].split()[0]},</p><p>You took a call request a couple of hours ago. "
                              f"When you&rsquo;ve called, please log how it went here:<br><a href=\"{url}\">{url}</a></p>"
                              f"<p>Thank you!</p>",
                              f"Hi {vol['name'].split()[0]},\n\nYou took a call request a couple of hours ago. When you've "
                              f"called, please log how it went here:\n{url}\n\nThank you!", to_name=vol["name"])
                    except Exception as e:
                        print(f"Reminder failed: {e}", flush=True)
        finally:
            conn.close()


# ---------------------------------------------------------------- routes
def register(app, crm, leadership_required, get_db):
    @app.context_processor
    def _inject():
        now = time.time()
        if now - _programs_cache["at"] > 300:
            try:
                conn = get_db()
                _programs_cache["rows"] = [dict(code=r["code"], name=r["name"]) for r in
                                           conn.execute("SELECT code, name FROM programs ORDER BY id").fetchall()]
                conn.close()
                _programs_cache["at"] = now
            except Exception:
                pass
        return {"call_form_programs": _programs_cache["rows"], "call_consent_question": CONSENT_QUESTION, "call_consent_fine": CONSENT_FINE, "call_consent_options": CONSENT_OPTIONS}

    @app.route("/talk-to-us", methods=["POST"])
    def public_talk_to_us():
        ok, yes, err = create_interest(get_db, request.form, "Have someone call you")
        if not ok:
            flash(err)
            return redirect(request.referrer or url_for("public_home"))
        return redirect(url_for("public_talk_thanks", c=yes or "email"))

    @app.route("/talk-to-us/thanks")
    def public_talk_thanks():
        c = request.args.get("c", "")
        c = LEGACY.get(c, c)
        return render_template("public_talk_thanks.html", pref=c if c in PREF_SHORT else "either",
                               yes=(c in PREF_WORD), pw=PREF_WORD.get(c, "call or text"))

    @app.route("/cep-training")
    def public_cep_training():
        """The CEP Team's how-to guide. Not linked from the public site; the team reaches it from their call page."""
        return render_template("public_cep_training.html")

    # ----- the volunteer's personal link
    def _load(token):
        ensure_tables(get_db)
        conn = get_db()
        n = conn.execute("SELECT * FROM call_notices WHERE token = ?", (token,)).fetchone()
        if not n:
            conn.close()
            abort(404)
        req = conn.execute("SELECT r.*, c.first_name, c.last_name, c.email FROM call_requests r "
                           "JOIN contacts c ON c.id = r.contact_id WHERE r.id = ?", (n["request_id"],)).fetchone()
        vol = conn.execute("SELECT * FROM call_volunteers WHERE id = ?", (n["volunteer_id"],)).fetchone()
        claimer = None
        if req and req["claimed_by"]:
            claimer = conn.execute("SELECT name FROM call_volunteers WHERE id = ?", (req["claimed_by"],)).fetchone()
        return conn, n, req, vol, claimer

    @app.route("/call/<token>", methods=["GET", "POST"])
    def public_call_claim(token):
        conn, n, req, vol, claimer = _load(token)
        try:
            mine = bool(req and req["claimed_by"] == vol["id"])
            if request.method == "POST" and req["status"] == "New":
                res = conn.execute("UPDATE call_requests SET status = 'Claimed', claimed_by = ?, claimed_at = ? "
                                   "WHERE id = ? AND status = 'New'", (vol["id"], _ts(_utc_now()), req["id"]))
                conn.commit()
                if res.rowcount == 1:
                    req = conn.execute("SELECT r.*, c.first_name, c.last_name, c.email FROM call_requests r "
                                       "JOIN contacts c ON c.id = r.contact_id WHERE r.id = ?", (req["id"],)).fetchone()
                    _tell_prospect(conn, req, vol, _base_url())
                    conn.commit()
                    mine = True
                else:
                    req = conn.execute("SELECT r.*, c.first_name, c.last_name, c.email FROM call_requests r "
                                       "JOIN contacts c ON c.id = r.contact_id WHERE r.id = ?", (req["id"],)).fetchone()
                    claimer = conn.execute("SELECT name FROM call_volunteers WHERE id = ?", (req["claimed_by"],)).fetchone()
            link = sms_body = None
            if mine and req["outcome"] and not req["outcome"].startswith(("Not interested", "Wrong number")):
                link = reserve_link(conn, req, _base_url())
                sms_body = (f"Hi {req['first_name']}, it's {vol['name'].split()[0]} with Dallas Discovery. Here is your personal link "
                            f"to reserve your spot in {_program_label(req['program_code'])}: {link}")
            cap = conn.execute("SELECT name, cell_phone FROM call_volunteers WHERE active = 1 AND program_code = 'CAPTAIN' ORDER BY id LIMIT 1").fetchone()
            return render_template("public_call_claim.html", req=req, vol=vol, mine=mine, claimer=claimer,
                                   captain=(dict(name=cap["name"], phone=pretty_phone(cap["cell_phone"])) if cap else None),
                                   link=link, sms_body=sms_body,
                                   label=_program_label(req["program_code"]), token=token,
                                   phone=pretty_phone(req["cell_phone"]), outcomes=OUTCOMES,
                                   pref=(req["contact_pref"] or "either"), pref_short=PREF_SHORT.get(req["contact_pref"] or "either"),
                                   tel=_digits(req["cell_phone"]),
                                   claimed_text=local_text(req["claimed_at"]))
        finally:
            conn.close()

    @app.route("/call/<token>/done", methods=["POST"])
    def public_call_done(token):
        conn, n, req, vol, claimer = _load(token)
        try:
            if req["claimed_by"] != vol["id"]:
                abort(403)
            outcome = request.form.get("outcome", "")
            note = (request.form.get("note") or "").strip()
            if outcome not in OUTCOMES:
                flash("Please choose how the call went.")
                return redirect(url_for("public_call_claim", token=token))
            keep_open = outcome.startswith(("No answer", "Texted"))
            conn.execute("UPDATE call_requests SET outcome = ?, outcome_note = ?, status = ?, closed_at = ? WHERE id = ?",
                         (outcome, note or None, "Claimed" if keep_open else "Called",
                          None if keep_open else _ts(_utc_now()), req["id"]))
            line = f"Call log ({vol['name']}, {local_text(_ts(_utc_now()))}): {outcome}" + (f" – {note}" if note else "")
            cur_notes = conn.execute("SELECT notes FROM contacts WHERE id = ?", (req["contact_id"],)).fetchone()
            merged = ((cur_notes["notes"] + "\n") if cur_notes and cur_notes["notes"] else "") + line
            conn.execute("UPDATE contacts SET notes = ? WHERE id = ?", (merged, req["contact_id"]))
            conn.commit()
            flash("Thank you! Your call has been logged.")
            return redirect(url_for("public_call_claim", token=token))
        finally:
            conn.close()

    @app.route("/call/<token>/link-sent", methods=["POST"])
    def public_call_link_sent(token):
        """The volunteer tapped 'Text it from my phone' -- we note that the link went out."""
        conn, n, req, vol, claimer = _load(token)
        try:
            if req["claimed_by"] != vol["id"]:
                abort(403)
            conn.execute("UPDATE call_requests SET link_sent_at = COALESCE(link_sent_at, ?), link_sent_via = COALESCE(link_sent_via, 'text') "
                         "WHERE id = ?", (_ts(_utc_now()), req["id"]))
            conn.commit()
            return ("", 204)
        finally:
            conn.close()

    @app.route("/call/<token>/email-link", methods=["POST"])
    def public_call_email_link(token):
        conn, n, req, vol, claimer = _load(token)
        try:
            if req["claimed_by"] != vol["id"]:
                abort(403)
            if not alerts_live():
                flash("Email sending is switched off right now, so nothing was sent. Please use the text button.")
                return redirect(url_for("public_call_claim", token=token))
            if not req["email"]:
                flash("We don't have an email address for them. Please use the text button.")
                return redirect(url_for("public_call_claim", token=token))
            link = reserve_link(conn, req, _base_url())
            label = _program_label(req["program_code"])
            subject = f"Your personal link to reserve your spot \u2013 {label}"
            html = (f"<p>Hi {req['first_name']},</p><p>It was good to connect. As promised, here is your personal link to reserve your spot in "
                    f"<strong>{label}</strong>:</p><p><a href=\"{link}\">{link}</a></p>"
                    f"<p>It already has your information filled in. Just choose your date and confirm. Payment comes later; we will send you the details.</p>"
                    f"<p>If you have any questions, reply to this email or call {vol['name'].split()[0]} at {pretty_phone(vol['cell_phone'])}.</p>"
                    f"<p>Warmly,<br>{vol['name']} and the Discovery Team</p>")
            text = (f"Hi {req['first_name']},\n\nIt was good to connect. Here is your personal link to reserve your spot in {label}:\n{link}\n\n"
                    f"It already has your information filled in. Just choose your date and confirm. Payment comes later; we will send you the details.\n\n"
                    f"Questions? Reply to this email or call {vol['name'].split()[0]} at {pretty_phone(vol['cell_phone'])}.\n\nWarmly,\n{vol['name']} and the Discovery Team")
            try:
                _send(req["email"], subject, html, text, to_name=req["first_name"])
            except Exception as e:
                print(f"Couldn't email the reserve link for request {req['id']}: {e}", flush=True)
                flash("The email didn't go through. Please use the text button.")
                return redirect(url_for("public_call_claim", token=token))
            conn.execute("UPDATE call_requests SET link_sent_at = COALESCE(link_sent_at, ?), link_sent_via = COALESCE(link_sent_via, 'email') "
                         "WHERE id = ?", (_ts(_utc_now()), req["id"]))
            conn.commit()
            flash(f"Sent to {req['email']}.")
            return redirect(url_for("public_call_claim", token=token))
        finally:
            conn.close()

    # ----- the person's own Reserve page (opened from the volunteer's link)
    @app.route("/reserve/<token>", methods=["GET", "POST"])
    def public_reserve(token):
        ensure_tables(get_db)
        conn = get_db()
        req = conn.execute("SELECT r.*, c.first_name, c.last_name, c.email, c.cell_phone AS cphone FROM call_requests r "
                           "JOIN contacts c ON c.id = r.contact_id WHERE r.reserve_token = ?", (token,)).fetchone()
        if not req:
            conn.close()
            abort(404)
        code = req["program_code"] or ""
        prog = conn.execute("SELECT name FROM programs WHERE code = ?", (code,)).fetchone()
        sessions = []
        if prog:
            for c in conn.execute("""SELECT c.*, p.code FROM cohorts c JOIN programs p ON p.id = c.program_id
                                     WHERE p.code = ? AND COALESCE(c.end_date, c.session_date) >= date('now')
                                       AND c.status != 'Cancelled' ORDER BY c.session_date""", (code,)).fetchall():
                cap = capacity_of(c, code)
                full = cap is not None and seats_used(conn, c["id"], code) >= cap
                sessions.append(dict(id=c["id"], text=span_text(c) + (f" \u2013 {_loc(c)}" if _loc(c) else ""), full=full))
        def render(**kw):
            base = dict(req=req, token=token, label=_program_label(code) if code else "Discovery", couple=(code in COUPLE_PROGRAMS),
                        sessions=sessions, result=None, vals={}, done_session=None)
            base.update(kw)
            return render_template("public_reserve.html", **base)
        try:
            if req["reserve_result"]:
                c = next((x for x in sessions if x["id"] == req["reserve_cohort_id"]), None)
                row = conn.execute("SELECT c.*, p.code FROM cohorts c JOIN programs p ON p.id = c.program_id WHERE c.id = ?",
                                   (req["reserve_cohort_id"],)).fetchone() if req["reserve_cohort_id"] else None
                return render(result=req["reserve_result"], done_session=span_text(row) if row else "")
            if request.method == "POST":
                f = request.form
                person = dict(first=(f.get("first_name") or "").strip(), last=(f.get("last_name") or "").strip(),
                              email=(f.get("email") or "").strip(), phone=(f.get("phone") or "").strip())
                partner = dict(first=(f.get("partner_first") or "").strip(), last=(f.get("partner_last") or "").strip(),
                               email=(f.get("partner_email") or "").strip())
                cohort_id = f.get("cohort_id", type=int)
                err = None
                if not person["first"] or not person["last"] or not person["email"]:
                    err = "Please fill in your first name, last name and email."
                elif len(_digits(person["phone"])) < 10:
                    err = "Please enter your cell phone number."
                elif not cohort_id or cohort_id not in [x["id"] for x in sessions]:
                    err = "Please choose a date."
                elif code in COUPLE_PROGRAMS and (not partner["first"] or not partner["email"]):
                    err = "Please tell us your partner's name and email, since this training is for couples."
                if err:
                    flash(err)
                    return render(vals=dict(person, **{"partner_first": partner["first"], "partner_last": partner["last"],
                                                       "partner_email": partner["email"], "cohort_id": cohort_id}))
                conn.close()
                result, coh, msg = reserve_seat(get_db, req["id"], cohort_id, person, partner)
                if result in ("Error", "Already"):
                    flash(msg or "You are already registered for that date. Thank you!")
                    return redirect(url_for("public_reserve", token=token))
                return redirect(url_for("public_reserve", token=token))
            return render(vals=dict(first=req["first_name"], last=req["last_name"] or "", email=req["email"] or "",
                                    phone=pretty_phone(req["cphone"] or req["cell_phone"])))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ----- CRM: the queue and the volunteer lists (Leadership)
    @crm.route("/call-requests")
    @leadership_required
    def call_requests_queue():
        ensure_tables(get_db)
        conn = get_db()
        rows = conn.execute(
            """SELECT r.*, c.first_name, c.last_name, c.email, v.name AS vol_name
               FROM call_requests r JOIN contacts c ON c.id = r.contact_id
               LEFT JOIN call_volunteers v ON v.id = r.claimed_by
               ORDER BY r.id DESC LIMIT 200""").fetchall()
        items = []
        now = _utc_now()
        for r in rows:
            d = dict(r)
            d["label"] = _program_label(r["program_code"])
            d["pref"] = PREF_SHORT.get(r["contact_pref"] or "either")
            d["when"] = local_text(r["created_at"])
            d["phone_pretty"] = pretty_phone(r["cell_phone"])
            d["claimed_when"] = local_text(r["claimed_at"])
            d["waiting_hours"] = round((now - _parse(r["created_at"])).total_seconds() / 3600, 1)
            d["overdue"] = r["status"] in ("New", "Claimed") and d["waiting_hours"] > WINDOW_HOURS
            items.append(d)
        pools = {c: conn.execute("SELECT COUNT(*) c FROM call_volunteers WHERE active = 1 AND program_code = ?", (c,)).fetchone()["c"]
                 for c, _ in POOLS}
        conn.close()
        return render_template("call_requests.html", items=items, live=alerts_live(), pools=pools, pool_list=POOLS,
                               in_hours=in_send_hours())

    @crm.route("/call-requests/<int:request_id>/resend", methods=["POST"])
    @leadership_required
    def call_request_resend(request_id):
        status = dispatch(get_db, request_id, _base_url(), force=True)
        flash(f"Alert check: {status}.")
        return redirect(url_for(".call_requests_queue"))

    @crm.route("/call-requests/<int:request_id>/close", methods=["POST"])
    @leadership_required
    def call_request_close(request_id):
        conn = get_db()
        conn.execute("UPDATE call_requests SET status = 'Closed', closed_at = ? WHERE id = ?", (_ts(_utc_now()), request_id))
        conn.commit()
        conn.close()
        return redirect(url_for(".call_requests_queue"))

    @crm.route("/call-requests/check-now", methods=["POST"])
    @leadership_required
    def call_requests_check_now():
        sweep(get_db, _base_url())
        flash("Checked for requests that need an alert or a reminder.")
        return redirect(url_for(".call_requests_queue"))

    @crm.route("/interest-report")
    @leadership_required
    def interest_report():
        ensure_tables(get_db)
        days = request.args.get("days", type=int) or 0
        conn = get_db()
        since = _ts(_utc_now() - timedelta(days=days)) if days else "0000-00-00 00:00:00"
        rows = conn.execute("SELECT * FROM interest_log WHERE created_at >= ? ORDER BY id", (since,)).fetchall()
        reached = {r["contact_id"]: r for r in conn.execute(
            "SELECT contact_id, status FROM call_requests WHERE status = 'Called'").fetchall()}
        sent_to, reserved_to, wait_to = set(), set(), set()
        for r in conn.execute("SELECT contact_id, program_code, link_sent_at, reserve_result FROM call_requests").fetchall():
            key = (r["contact_id"], r["program_code"])
            if r["link_sent_at"] or r["reserve_result"]:
                sent_to.add(key)
            if r["reserve_result"] == "Enrolled":
                reserved_to.add(key)
            if r["reserve_result"] == "Waitlist":
                wait_to.add(key)
        enrolled = set()
        for e in conn.execute("""SELECT en.contact_id, p.code FROM enrollments en JOIN cohorts c ON c.id = en.cohort_id
                                  JOIN programs p ON p.id = c.program_id""").fetchall():
            enrolled.add((e["contact_id"], e["code"]))
        names = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM programs").fetchall()}
        conn.close()

        def blank():
            return dict(total=0, yes=0, no=0, reached=0, sent=0, reserved=0, wait=0, enrolled=0)

        def add(d, r):
            d["total"] += 1
            pref = LEGACY.get(r["call_ok"], r["call_ok"])
            d["yes"] += pref in PREF_WORD
            d["no"] += pref == "email"
            if pref in PREF_SHORT:
                d.setdefault("prefs", {})[pref] = d.setdefault("prefs", {}).get(pref, 0) + 1
            d["reached"] += r["contact_id"] in reached
            k = (r["contact_id"], r["program_code"])
            d["sent"] += k in sent_to
            d["reserved"] += k in reserved_to
            d["wait"] += k in wait_to
            d["enrolled"] += k in enrolled

        by_prog, by_page, by_day, total = {}, {}, {}, blank()
        for r in rows:
            add(total, r)
            add(by_prog.setdefault(r["program_code"] or "Not sure yet", blank()), r)
            add(by_page.setdefault(PAGES.get(r["page"], "Other"), blank()), r)
            add(by_day.setdefault(local_text(r["created_at"]).split(",")[0], blank()), r)
        prog_rows = sorted(((k, (f"{k} \u2013 {names[k]}" if k in names else k), v) for k, v in by_prog.items()),
                           key=lambda x: -x[2]["total"])
        pref_rows = [(PREF_SHORT[k], total.get("prefs", {}).get(k, 0)) for k in ("either", "call", "text", "email")]
        return render_template("interest_report.html", total=total, pref_rows=pref_rows, prog_rows=prog_rows,
                               page_rows=sorted(by_page.items(), key=lambda x: -x[1]["total"]),
                               day_rows=list(by_day.items())[-14:][::-1], days=days)

    @crm.route("/call-volunteers/people")
    @leadership_required
    def call_volunteer_people():
        """Type-ahead / browse list for choosing a volunteer: team members first, then anyone in Contacts."""
        from flask import jsonify
        q = (request.args.get("q") or "").strip().lower()
        mode = request.args.get("mode", "team")          # team | all
        prog = (request.args.get("prog") or "").upper()  # limit team to people who served on this program
        conn = get_db()
        served = {}   # contact_id -> list of (session_date, code, role)
        for r in conn.execute("""SELECT cs.contact_id, cs.role, c.session_date, p.code FROM cohort_staffing cs
                                  JOIN cohorts c ON c.id = cs.cohort_id JOIN programs p ON p.id = c.program_id
                                  WHERE cs.role <> 'Trainee'""").fetchall():
            served.setdefault(r["contact_id"], []).append((str(r["session_date"] or ""), r["code"], r["role"]))
        for r in conn.execute("""SELECT sg.contact_id, sg.role, c.session_date, p.code FROM small_group_staffing sg
                                  JOIN small_groups g ON g.id = sg.small_group_id JOIN cohorts c ON c.id = g.cohort_id
                                  JOIN programs p ON p.id = c.program_id""").fetchall():
            served.setdefault(r["contact_id"], []).append((str(r["session_date"] or ""), r["code"], r["role"]))
        for r in conn.execute("SELECT contact_id FROM staff WHERE contact_id IS NOT NULL AND active = 1").fetchall():
            served.setdefault(r["contact_id"], [])
        used = {r["contact_id"] if False else r["email"].lower() for r in conn.execute(
            "SELECT email FROM call_volunteers WHERE active = 1").fetchall()}
        cols = "id, first_name, last_name, email, cell_phone"
        if mode == "team":
            ids = [i for i, v in served.items() if not prog or any(x[1] == prog for x in v)]
            rows = []
            for i in ids:
                r = conn.execute(f"SELECT {cols} FROM contacts WHERE id = ?", (i,)).fetchone()
                if r:
                    rows.append(r)
        else:
            if len(q) < 2:
                conn.close()
                return jsonify([])
            rows = conn.execute(f"SELECT {cols} FROM contacts ORDER BY last_name, first_name LIMIT 5000").fetchall()
        conn.close()
        toks = q.split()
        out = []
        for r in rows:
            name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
            hay = " ".join([name.lower(), (r["email"] or "").lower(), _digits(r["cell_phone"])])
            if toks and not all(t in hay or (_digits(t) and _digits(t) in hay) for t in toks):
                continue
            hist = sorted(served.get(r["id"], []), reverse=True)
            last = f"{hist[0][2]}, {hist[0][1]} {hist[0][0][:7]}" if hist else ""
            out.append(dict(id=r["id"], name=name, cell=pretty_phone(r["cell_phone"]) if r["cell_phone"] else "",
                            email=r["email"] or "", team=r["id"] in served, last=last, sessions=len(hist),
                            sort=(hist[0][0] if hist else ""), already=(r["email"] or "").lower() in used))
        out.sort(key=lambda x: (not x["team"], "".join(chr(255 - ord(c)) for c in x["sort"]), x["name"].lower()))
        return jsonify(out[:60])

    @crm.route("/call-volunteers", methods=["GET", "POST"])
    @leadership_required
    def call_volunteers_page():
        ensure_tables(get_db)
        conn = get_db()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                code = request.form.get("program_code", "")
                name = (request.form.get("name") or "").strip()
                phone = (request.form.get("cell_phone") or "").strip()
                email = (request.form.get("email") or "").strip()
                have = conn.execute("SELECT COUNT(*) c FROM call_volunteers WHERE active = 1 AND program_code = ?", (code,)).fetchone()["c"]
                if code not in dict(POOLS):
                    flash("Choose a program.")
                elif not (name and email and len(_digits(phone)) >= 10):
                    flash("Please enter a name, a cell phone number, and an email.")
                elif have >= max_for(code):
                    flash(f"That group already has {max_for(code)}. Remove one first.")
                else:
                    conn.execute("INSERT INTO call_volunteers (program_code, name, cell_phone, email) VALUES (?, ?, ?, ?)",
                                 (code, name, phone, email))
                    conn.commit()
                    flash(f"Added {name}.")
            elif action == "add_contact":
                code = request.form.get("program_code", "")
                ct = conn.execute("SELECT id, first_name, last_name, email, cell_phone FROM contacts WHERE id = ?",
                                  (request.form.get("contact_id", type=int),)).fetchone()
                phone = (request.form.get("cell_phone") or (ct["cell_phone"] if ct else "") or "").strip()
                email = (request.form.get("email") or (ct["email"] if ct else "") or "").strip()
                have = conn.execute("SELECT COUNT(*) c FROM call_volunteers WHERE active = 1 AND program_code = ?", (code,)).fetchone()["c"]
                if not ct or code not in dict(POOLS):
                    flash("Choose a person and a program.")
                elif len(_digits(phone)) < 10 or not email:
                    flash("That contact needs a cell phone and an email. Enter them below the name and try again.")
                elif have >= max_for(code):
                    flash(f"That group already has {max_for(code)}. Remove one first.")
                else:
                    name = f"{ct['first_name']} {ct['last_name'] or ''}".strip()
                    conn.execute("INSERT INTO call_volunteers (program_code, name, cell_phone, email) VALUES (?, ?, ?, ?)",
                                 (code, name, phone, email))
                    if not ct["cell_phone"]:
                        conn.execute("UPDATE contacts SET cell_phone = ? WHERE id = ?", (phone, ct["id"]))
                    if not ct["email"]:
                        conn.execute("UPDATE contacts SET email = ? WHERE id = ?", (email, ct["id"]))
                    conn.commit()
                    flash(f"Added {name}.")
            elif action == "remove":
                conn.execute("UPDATE call_volunteers SET active = 0 WHERE id = ?", (request.form.get("id"),))
                conn.commit()
            conn.close()
            return redirect(url_for(".call_volunteers_page"))
        groups = []
        for code, label in POOLS:
            vols = conn.execute("SELECT * FROM call_volunteers WHERE active = 1 AND program_code = ? ORDER BY id", (code,)).fetchall()
            groups.append(dict(code=code, label=label, max=max_for(code), vols=[dict(v, phone_pretty=pretty_phone(v["cell_phone"])) for v in vols]))
        conn.close()
        return render_template("call_volunteers.html", groups=groups, max_per=MAX_PER_POOL)
