from dotenv import load_dotenv
load_dotenv()  # loads DATABASE_URL from a local .env file, if present

from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, session, send_from_directory, abort, make_response
from datetime import date, datetime, timedelta
import secrets
import os
import io
import re
import mimetypes
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db, init_db
from seed import seed
from config import PROGRAM_NAME
from email_utils import send_email, EmailSendError
from email_templates import (
    longform_followup_content,
    LONGFORM_FOLLOWUP_SUBJECT,
    interested_party_welcome_content,
    INTERESTED_PARTY_WELCOME_SUBJECT,
    excitement_blast_content,
    EXCITEMENT_BLAST_SUBJECT,
    connect_request_confirmation_content,
    CONNECT_REQUEST_SUBJECT,
    longform_thankyou_content,
    LONGFORM_THANKYOU_SUBJECT,
    preliminary_plan_content,
    PRELIMINARY_PLAN_SUBJECT,
)

app = Flask(__name__)
# In production (Render), set a SECRET_KEY environment variable to a long random
# string -- this is what keeps login sessions secure. Falls back to a dev-only
# value so this still runs locally without extra setup.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# ---------- site-wide "coming soon" password gate ----------
# When a SITE_PASSWORD environment variable is set on Render, every page on
# this site (public pages and the CRM alike) requires a username/password
# before showing anything -- the browser pops up its own plain login box.
# To make the site public again, just delete the SITE_PASSWORD environment
# variable on Render and redeploy; with it unset, this check does nothing.
SITE_PASSWORD = os.environ.get("SITE_PASSWORD")
SITE_USERNAME = os.environ.get("SITE_USERNAME", "dallasdiscovery")


@app.before_request
def _require_site_password():
    if not SITE_PASSWORD:
        return  # gate is off -- site is public
    if request.path.startswith("/static/"):
        # CSS/JS/images only -- nothing sensitive in them. Some browsers
        # (Safari in particular) don't reliably resend Basic Auth
        # credentials for these background requests, which otherwise makes
        # a fully-authenticated page render with no styling at all.
        return
    auth = request.authorization
    if not auth or auth.username != SITE_USERNAME or auth.password != SITE_PASSWORD:
        return make_response(
            "<!doctype html><html><head><title>Site under development</title>"
            "<style>body{font-family:sans-serif;max-width:32rem;margin:4rem auto;"
            "padding:0 1.5rem;text-align:center;color:#333}</style></head>"
            "<body><h1>This site is under development</h1>"
            "<p>We're not quite ready to open to the public yet -- please check back soon.</p>"
            "</body></html>",
            401,
            {"WWW-Authenticate": 'Basic realm="This site is not open to the public yet."'},
        )

# ---------- contact photo uploads ----------
# Stored as bytes in the database (contacts.photo_data), not on local disk --
# Render's web server filesystem is wiped on every deploy, so anything saved
# only to disk disappears the next time the app is redeployed.
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic", "heif"}
MAX_PHOTO_DIMENSION = 800  # longest side, in pixels, after resizing


def _photo_extension(filename):
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext if ext in ALLOWED_PHOTO_EXTENSIONS else None


def process_contact_photo(file_storage):
    """Reads an uploaded photo, resized to a sane max size, and returns
    (image_bytes, content_type) ready to store in contacts.photo_data /
    photo_content_type -- or None if no valid file was provided."""
    if not file_storage or not file_storage.filename:
        return None
    ext = _photo_extension(file_storage.filename)
    if not ext:
        flash("That photo type isn't supported -- please use a JPG, PNG, WEBP, or HEIC file.")
        return None

    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass  # HEIC/HEIF uploads will fall back to being stored untouched
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)
        image.thumbnail((MAX_PHOTO_DIMENSION, MAX_PHOTO_DIMENSION), Image.LANCZOS)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        # If it can't be decoded as a real image, don't store it. Storing the
        # raw uploaded bytes with a browser-reported (attacker-controlled)
        # content type would mean trusting whatever the uploader claims the
        # file is, instead of what it verifiably is -- rejecting it here is
        # the safe default on a public, no-login form.
        flash("That photo couldn't be read -- please try a different JPG, PNG, or WEBP file.")
        return None


def upsert_contact_photo(conn, contact_id, data, content_type):
    """Stores/replaces the photo for a contact. Also sets contacts.photo_flag
    (a non-null marker -- not a real filename) so existing code/templates
    that check "does this contact have a photo" keep working unchanged."""
    conn.execute("DELETE FROM contact_photos WHERE contact_id = ?", (contact_id,))
    conn.execute(
        "INSERT INTO contact_photos (contact_id, data, content_type) VALUES (?, ?, ?)",
        (contact_id, data, content_type),
    )
    conn.execute(
        "UPDATE contacts SET photo_filename = ? WHERE id = ?",
        (secrets.token_hex(6), contact_id),
    )


# ---------- training materials library (handouts, resource sheets, etc.) ----------
# Also stored as bytes in the database (program_materials.data), for the same
# reason as contact photos above -- these are only ever sent to the browser
# through the program_material_file route below, which checks program-level
# authorization first, so they stay just as confidential as they were on disk.
ALLOWED_MATERIAL_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "jpg", "jpeg", "png"}


def _material_extension(filename):
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext if ext in ALLOWED_MATERIAL_EXTENSIONS else None


def process_program_material(file_storage):
    """Reads an uploaded handout/resource file for a program. Returns
    (file_bytes, content_type) ready to store in program_materials.data /
    content_type, or None if no valid file was provided."""
    if not file_storage or not file_storage.filename:
        return None
    ext = _material_extension(file_storage.filename)
    if not ext:
        flash("That file type isn't supported for training materials.")
        return None
    data = file_storage.stream.read()
    content_type = file_storage.mimetype or mimetypes.guess_type(file_storage.filename)[0] or "application/octet-stream"
    return data, content_type


# All existing CRM functionality (dashboard, contacts, programs, cohorts, donations)
# now lives under /crm, so it can sit alongside the public marketing site at the root.
crm = Blueprint("crm", __name__, url_prefix="/crm")

# ---------- staff login ----------
# Every /crm page requires a logged-in staff account, except the login page
# itself. Two roles: "Team" (day-to-day CRM work) and "Leadership" (also
# sees feedback results, contracts, and donations).

SESSION_IDLE_TIMEOUT_MINUTES = 30


@crm.before_request
def require_staff_login():
    if request.endpoint in ("crm.login", "crm.leadership_login"):
        return
    if not session.get("staff_id"):
        return redirect(url_for(".login", next=request.path))

    # Idle timeout: if it's been more than SESSION_IDLE_TIMEOUT_MINUTES since
    # the last request we saw from this session, log them out and send them
    # back to the login page instead of letting an old, unattended session
    # stay signed in indefinitely.
    now = datetime.utcnow()
    last_seen = session.get("last_seen")
    if last_seen:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen)
        except (TypeError, ValueError):
            last_seen_dt = None
        if last_seen_dt and now - last_seen_dt > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
            session.clear()
            flash("You were logged out after 30 minutes of inactivity -- please log back in.")
            return redirect(url_for(".login", next=request.path))
    session["last_seen"] = now.isoformat()


def leadership_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("staff_role") != "Leadership":
            flash("That page is restricted to leadership accounts.")
            return redirect(url_for(".dashboard"))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view):
    """Gates the small set of things only the account Owner can do:
    granting someone full access in one step, and making/removing other
    Owners. Leadership can run the CRM day-to-day but does not get this."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_owner"):
            flash("That's restricted to the account owner.")
            return redirect(url_for(".dashboard"))
        return view(*args, **kwargs)
    return wrapped


def leadership_required(view):
    """Gates actions that need a Leadership-level account (Leadership or
    Owner), even for a Team account that has otherwise been granted access
    to the Contacts area -- e.g. deleting a contact. A plain Team account
    never passes this, no matter what access_grants says."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not (session.get("is_owner") or session.get("staff_role") == "Leadership"):
            flash("That's restricted to Leadership accounts.")
            return redirect(url_for(".dashboard"))
        return view(*args, **kwargs)
    return wrapped


# ---------- authorized users (confidential-area access control) ----------
# A small, fixed set of "areas" -- three fixed areas plus one per training
# program (keyed by that program's code, e.g. "D1"). Each area can have up
# to 4 authorized Team accounts. Only the account Owner (is_owner=1, one
# person by default) automatically has access to every area -- Leadership
# accounts run the CRM day-to-day but see confidential areas only once
# specifically authorized, same as Team, unless the Owner grants them full
# access. Managed from the Team page's "Access" menu per row (Leadership-only
# to view and manage grants; only the Owner can grant someone *everything*).
ACCESS_AREA_MAX_GRANTS = 4
FIXED_ACCESS_AREAS = [
    ("accounting", "Accounting"),
    ("marketing", "Marketing"),
    ("contacts", "Contacts"),
    ("donations", "Donations"),
]
# Reserved program-code slots shown in the hub even before that program
# exists yet (B1-B4 are today's real programs; B5/B6 are room to grow).
RESERVED_PROGRAM_AREA_CODES = ["D1", "D2", "D3", "D4", "D5", "D6"]


def _area_labels_map(conn):
    """area_key -> human label, for every fixed area plus every program
    that actually exists today (e.g. 'B1' -> 'B1 — Bluesky 1')."""
    labels = dict(FIXED_ACCESS_AREAS)
    for row in conn.execute("SELECT code, name FROM programs").fetchall():
        labels[row["code"]] = f"{row['code']} — {row['name']}"
    return labels


def _all_areas(conn):
    """(area_key, label) for every fixed area plus all 6 program slots
    (B1-B6) -- the full set of things access can be granted for. A slot
    shows the program's real name once it exists, or just its code as a
    placeholder before then, so access can be set up ahead of time."""
    area_labels = _area_labels_map(conn)
    return list(FIXED_ACCESS_AREAS) + [
        (code, area_labels.get(code, code)) for code in RESERVED_PROGRAM_AREA_CODES
    ]


def _has_area_access(area_key):
    if session.get("is_owner"):
        return True
    conn = get_db()
    grant = conn.execute(
        "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?",
        (area_key, session.get("staff_id")),
    ).fetchone()
    conn.close()
    return bool(grant)


def _has_any_program_access():
    """True if this Team account is authorized for at least one training
    program (B1-B6), used to decide whether the Programs nav link shows."""
    if session.get("is_owner"):
        return True
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM access_grants WHERE staff_id = ? AND area_key IN ({})".format(
            ",".join("?" for _ in RESERVED_PROGRAM_AREA_CODES)
        ),
        (session.get("staff_id"), *RESERVED_PROGRAM_AREA_CODES),
    ).fetchone()
    conn.close()
    return bool(row)


def area_required(area_key):
    """Gates a fixed-area route (e.g. Contacts) to Leadership accounts plus
    any Team account specifically authorized for that area."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if _has_area_access(area_key):
                return view(*args, **kwargs)
            flash("This area is restricted. Ask a leadership account to grant you access from the Team page.")
            return redirect(url_for(".dashboard"))
        return wrapped
    return decorator


def program_area_required(view):
    """Gates a program's confidential content (training materials, music
    playlists) to the Owner, plus any Team/Leadership account authorized
    for that program's area (looked up by the program's own code, e.g.
    "D1"). Every wrapped route must take program_id as a URL argument."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        program_id = kwargs.get("program_id")
        if session.get("is_owner"):
            return view(*args, **kwargs)
        conn = get_db()
        prog = conn.execute("SELECT code FROM programs WHERE id = ?", (program_id,)).fetchone()
        area_key = prog["code"] if prog else None
        grant = conn.execute(
            "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?",
            (area_key, session.get("staff_id")),
        ).fetchone() if area_key else None
        conn.close()
        if grant:
            return view(*args, **kwargs)
        flash("This program's training materials are restricted. Ask a leadership account to grant you access from the Team page.")
        return redirect(url_for(".programs_list"))
    return wrapped


def _attempt_login(next_url):
    """Shared by both login pages -- checks credentials against the same
    staff table regardless of which page (Team or Leadership) was used."""
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE email = ? AND active = 1", (email,)).fetchone()
    conn.close()
    if staff and check_password_hash(staff["password_hash"], password):
        session["staff_id"] = staff["id"]
        session["staff_name"] = staff["name"]
        session["staff_role"] = staff["role"]
        try:
            session["is_owner"] = bool(staff["is_owner"])
        except (KeyError, IndexError):
            # is_owner column doesn't exist yet -- migrate_add_owner.py hasn't
            # been run against this database yet. Fail safe (no owner access)
            # rather than crash the login.
            session["is_owner"] = False
        return redirect(next_url)
    flash("Incorrect email or password.")
    return None


@crm.route("/login", methods=["GET", "POST"])
def login():
    if session.get("staff_id"):
        return redirect(url_for(".dashboard"))
    next_url = request.form.get("next") or request.args.get("next") or url_for(".dashboard")
    if request.method == "POST":
        result = _attempt_login(next_url)
        if result:
            return result
    return render_template("login.html", next=next_url, page_title="Team Login",
                            other_login_url=url_for(".leadership_login"), other_login_label="Leadership login")


@crm.route("/leadership-login", methods=["GET", "POST"])
def leadership_login():
    if session.get("staff_id"):
        return redirect(url_for(".dashboard"))
    next_url = request.form.get("next") or request.args.get("next") or url_for(".dashboard")
    if request.method == "POST":
        result = _attempt_login(next_url)
        if result:
            return result
    return render_template("login.html", next=next_url, page_title="Leadership Login",
                            other_login_url=url_for(".login"), other_login_label="Team login")


@crm.route("/logout")
def logout():
    session.clear()
    flash("You've been logged out.")
    return redirect(url_for(".login"))


@crm.route("/account/password", methods=["GET", "POST"])
def change_password():
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (session["staff_id"],)).fetchone()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not check_password_hash(staff["password_hash"], current):
            flash("Current password is incorrect.")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.")
        elif new != confirm:
            flash("New passwords didn't match.")
        else:
            conn.execute("UPDATE staff SET password_hash = ? WHERE id = ?", (generate_password_hash(new), staff["id"]))
            conn.commit()
            conn.close()
            flash("Password updated.")
            return redirect(url_for(".dashboard"))
    conn.close()
    return render_template("change_password.html")


@crm.route("/staff")
@leadership_required
def staff_list():
    conn = get_db()
    rows = conn.execute("SELECT * FROM staff ORDER BY name").fetchall()
    area_labels = _area_labels_map(conn)
    all_areas = _all_areas(conn)
    grants = conn.execute("SELECT staff_id, area_key FROM access_grants").fetchall()
    conn.close()
    access_by_staff = {}
    granted_keys_by_staff = {}
    for g in grants:
        access_by_staff.setdefault(g["staff_id"], []).append(area_labels.get(g["area_key"], g["area_key"]))
        granted_keys_by_staff.setdefault(g["staff_id"], set()).add(g["area_key"])
    return render_template(
        "staff_list.html", staff=rows, access_by_staff=access_by_staff,
        all_areas=all_areas, granted_keys_by_staff=granted_keys_by_staff,
    )


@crm.route("/staff/<int:staff_id>/reset-password", methods=["POST"])
@leadership_required
def staff_reset_password(staff_id):
    """Generates a fresh temporary password for this account and shows it
    once. The CRM never stores a readable password -- only a one-way hash --
    so this is the only way to see/hand out a working password after the
    account's first creation."""
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff:
        temp_password = secrets.token_urlsafe(9)
        conn.execute(
            "UPDATE staff SET password_hash = ? WHERE id = ?",
            (generate_password_hash(temp_password), staff_id),
        )
        conn.commit()
        flash(f"New password for {staff['name']} ({staff['email']}): {temp_password}")
    conn.close()
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/access", methods=["GET", "POST"])
@leadership_required
def staff_access(staff_id):
    """One place to see and set everything a single Team/Leadership
    account is authorized for, instead of visiting each area one at a
    time under Authorized Users."""
    conn = get_db()
    target = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not target:
        flash("Couldn't find that account.")
        conn.close()
        return redirect(url_for(".staff_list"))

    all_areas = _all_areas(conn)

    if request.method == "POST":
        selected = set(request.form.getlist("area_key"))
        current_grants = {
            row["area_key"]: row["id"]
            for row in conn.execute(
                "SELECT id, area_key FROM access_grants WHERE staff_id = ?", (staff_id,)
            ).fetchall()
        }
        added, removed, skipped_full = [], [], []
        for area_key, label in all_areas:
            currently_has = area_key in current_grants
            wants = area_key in selected
            if wants and not currently_has:
                count = conn.execute(
                    "SELECT COUNT(*) c FROM access_grants WHERE area_key = ?", (area_key,)
                ).fetchone()["c"]
                if count >= ACCESS_AREA_MAX_GRANTS:
                    skipped_full.append(label)
                    continue
                conn.execute("INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (area_key, staff_id))
                added.append(label)
            elif not wants and currently_has:
                conn.execute("DELETE FROM access_grants WHERE id = ?", (current_grants[area_key],))
                removed.append(label)
        conn.commit()
        conn.close()
        msg_parts = []
        if added:
            msg_parts.append(f"Granted: {', '.join(added)}")
        if removed:
            msg_parts.append(f"Removed: {', '.join(removed)}")
        if skipped_full:
            msg_parts.append(
                f"Already had {ACCESS_AREA_MAX_GRANTS} authorized users, so skipped: {', '.join(skipped_full)}"
            )
        flash(" | ".join(msg_parts) if msg_parts else "No changes made.")
        redirect_to = request.form.get("redirect_to")
        if redirect_to == "staff_list":
            return redirect(url_for(".staff_list"))
        return redirect(url_for(".staff_access", staff_id=staff_id))

    current_grants = {
        row["area_key"]
        for row in conn.execute("SELECT area_key FROM access_grants WHERE staff_id = ?", (staff_id,)).fetchall()
    }
    conn.close()
    return render_template(
        "staff_access.html", target=target, all_areas=all_areas,
        current_grants=current_grants, max_grants=ACCESS_AREA_MAX_GRANTS,
    )


@crm.route("/staff/new")
@leadership_required
def staff_new_search():
    """Adding a team member starts here: find their Contact record first,
    so every team member's name/address/emergency contact/etc lives in one
    place and matches up with participants. If they're not a contact yet,
    this sends you to the full Contact form instead of a stripped-down
    login-only form."""
    q = request.args.get("q", "").strip()
    conn = get_db()
    contacts = []
    if q:
        contacts = conn.execute(
            f"""SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts
                WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ?
                ORDER BY last_name, first_name LIMIT 25""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    conn.close()
    return render_template("staff_new_search.html", q=q, contacts=contacts)


@crm.route("/staff/new/contact/<int:contact_id>", methods=["GET", "POST"])
@leadership_required
def staff_new_for_contact(contact_id):
    conn = get_db()
    contact = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if not contact:
        flash("Couldn't find that contact.")
        conn.close()
        return redirect(url_for(".staff_new_search"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role") or "Team"
        temp_password = request.form["password"]
        if not email:
            flash("Enter an email to use for their CRM login.")
            conn.close()
            return render_template("staff_new_confirm.html", contact=contact)
        try:
            conn.execute(
                "INSERT INTO staff (name, email, password_hash, role, contact_id) VALUES (?, ?, ?, ?, ?)",
                (contact["full_name"], email, generate_password_hash(temp_password), role, contact_id),
            )
            conn.commit()
            flash(
                f"Added {contact['full_name']} as {role}. Give them this email ({email}) "
                f"and this temporary password: {temp_password}"
            )
            conn.close()
            return redirect(url_for(".staff_list"))
        except Exception:
            flash("Couldn't add that account -- that email may already be in use by another team login.")
            conn.close()
            return render_template("staff_new_confirm.html", contact=contact)

    conn.close()
    return render_template("staff_new_confirm.html", contact=contact)


@crm.route("/staff/<int:staff_id>/link-contact")
@leadership_required
def staff_link_contact_search(staff_id):
    """Matches an existing team login (one created before this Team<->Contact
    link existed) to that person's Contact record."""
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not staff:
        flash("Couldn't find that account.")
        conn.close()
        return redirect(url_for(".staff_list"))
    q = request.args.get("q", "").strip()
    contacts = []
    if q:
        contacts = conn.execute(
            f"""SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts
                WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ?
                ORDER BY last_name, first_name LIMIT 25""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    conn.close()
    return render_template("staff_link_contact.html", staff=staff, q=q, contacts=contacts)


@crm.route("/staff/<int:staff_id>/link-contact/<int:contact_id>", methods=["GET", "POST"])
@leadership_required
def staff_link_contact(staff_id, contact_id):
    conn = get_db()
    contact = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not contact or not staff:
        flash("Couldn't find that account or contact.")
        conn.close()
        return redirect(url_for(".staff_list"))
    conn.execute(
        "UPDATE staff SET contact_id = ?, name = ? WHERE id = ?",
        (contact_id, contact["full_name"], staff_id),
    )
    conn.commit()
    conn.close()
    flash(f"Linked {staff['name']}'s team login to {contact['full_name']}'s contact record.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/unlink-contact", methods=["POST"])
@leadership_required
def staff_unlink_contact(staff_id):
    conn = get_db()
    conn.execute("UPDATE staff SET contact_id = NULL WHERE id = ?", (staff_id,))
    conn.commit()
    conn.close()
    flash("Unlinked that team login from its contact record.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/edit", methods=["POST"])
@leadership_required
def staff_edit(staff_id):
    """Fixes a team member's name or email -- e.g. an account whose name
    was mistyped when it was created (by hand or auto-created while
    granting access)."""
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    if not name or not email:
        flash("Enter both a name and an email.")
        return redirect(url_for(".staff_list"))
    conn = get_db()
    try:
        conn.execute(
            "UPDATE staff SET name = ?, email = ? WHERE id = ?",
            (name, email, staff_id),
        )
        conn.commit()
        flash(f"Updated that account's name/email to {name} ({email}).")
        if staff_id == session.get("staff_id"):
            session["staff_name"] = name
    except Exception:
        flash("Couldn't save that -- that email may already be in use by another account.")
    conn.close()
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/deactivate", methods=["POST"])
@leadership_required
def staff_deactivate(staff_id):
    if staff_id == session.get("staff_id"):
        flash("You can't deactivate your own account -- ask another Owner or Leadership account to do it.")
        return redirect(url_for(".staff_list"))
    conn = get_db()
    target = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if target and target["is_owner"]:
        remaining_active_owners = conn.execute(
            "SELECT COUNT(*) c FROM staff WHERE is_owner = 1 AND active = 1"
        ).fetchone()["c"]
        if remaining_active_owners <= 1:
            flash("Can't deactivate the last remaining active Owner.")
            conn.close()
            return redirect(url_for(".staff_list"))
    conn.execute("UPDATE staff SET active = 0 WHERE id = ?", (staff_id,))
    conn.commit()
    conn.close()
    flash("Account deactivated.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/reactivate", methods=["POST"])
@leadership_required
def staff_reactivate(staff_id):
    conn = get_db()
    conn.execute("UPDATE staff SET active = 1 WHERE id = ?", (staff_id,))
    conn.commit()
    conn.close()
    flash("Account reactivated.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/grant-full-access", methods=["POST"])
@owner_required
def staff_grant_full_access(staff_id):
    """One-click version of authorizing someone in every area: makes them
    Leadership (so they can help run the CRM day-to-day) and grants them
    every fixed area plus every program that exists today. Owner-only --
    nobody but the Owner can hand out full access."""
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not staff:
        flash("Couldn't find that account.")
        conn.close()
        return redirect(url_for(".staff_list"))

    conn.execute("UPDATE staff SET role = 'Leadership' WHERE id = ?", (staff_id,))

    all_area_keys = [key for key, _ in FIXED_ACCESS_AREAS] + [
        row["code"] for row in conn.execute("SELECT code FROM programs").fetchall()
    ]
    granted, skipped_full = [], []
    for area_key in all_area_keys:
        existing = conn.execute(
            "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?", (area_key, staff_id)
        ).fetchone()
        if existing:
            continue
        count = conn.execute(
            "SELECT COUNT(*) c FROM access_grants WHERE area_key = ?", (area_key,)
        ).fetchone()["c"]
        if count >= ACCESS_AREA_MAX_GRANTS:
            skipped_full.append(area_key)
            continue
        conn.execute("INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (area_key, staff_id))
        granted.append(area_key)
    conn.commit()
    conn.close()

    msg = f"{staff['name']} is now Leadership with access to every area."
    if skipped_full:
        msg += f" ({', '.join(skipped_full)} already had 4 authorized users -- remove one there if they need it too.)"
    flash(msg)
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/make-owner", methods=["POST"])
@owner_required
def staff_make_owner(staff_id):
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff:
        conn.execute("UPDATE staff SET is_owner = 1, role = 'Leadership' WHERE id = ?", (staff_id,))
        conn.commit()
        flash(f"{staff['name']} is now an Owner -- unrestricted access, same as you.")
    conn.close()
    return redirect(url_for(".staff_list"))


@crm.route("/staff/change-ownership", methods=["POST"])
@owner_required
def change_ownership():
    """Owner-only, password-gated: makes another active staff account an
    Owner too, without touching the current Owner's own status. Requires
    re-entering your own password (not just being logged in) since this
    hands out full, unrestricted access -- the same protection level as a
    real password confirmation dialog elsewhere would give."""
    conn = get_db()
    me = conn.execute("SELECT * FROM staff WHERE id = ?", (session["staff_id"],)).fetchone()
    if not me or not check_password_hash(me["password_hash"], request.form.get("password", "")):
        flash("Incorrect password -- ownership not changed.")
        conn.close()
        return redirect(url_for(".staff_list"))
    target = conn.execute(
        "SELECT * FROM staff WHERE id = ? AND active = 1", (request.form.get("new_owner_id"),)
    ).fetchone()
    if not target:
        flash("Choose an active staff account to make an Owner.")
        conn.close()
        return redirect(url_for(".staff_list"))
    conn.execute("UPDATE staff SET is_owner = 1, role = 'Leadership' WHERE id = ?", (target["id"],))
    conn.commit()
    conn.close()
    flash(f"{target['name']} is now an Owner too -- full, unrestricted access, same as you. They'll see it the next time they log in.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/remove-owner", methods=["POST"])
@owner_required
def staff_remove_owner(staff_id):
    conn = get_db()
    remaining_owners = conn.execute("SELECT COUNT(*) c FROM staff WHERE is_owner = 1").fetchone()["c"]
    if remaining_owners <= 1:
        flash("Can't remove the last remaining Owner.")
        conn.close()
        return redirect(url_for(".staff_list"))
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff:
        conn.execute("UPDATE staff SET is_owner = 0 WHERE id = ?", (staff_id,))
        conn.commit()
        flash(f"{staff['name']} is no longer an Owner.")
    conn.close()
    return redirect(url_for(".staff_list"))


# Reusable SQL snippet: builds a display name from first_name/last_name.
# Pass a table alias (e.g. "ct") when the contacts table is joined under one.
def full_name_sql(alias=None):
    p = f"{alias}." if alias else ""
    return f"TRIM({p}first_name || ' ' || COALESCE({p}last_name, ''))"


FULL_NAME_SQL = full_name_sql()

# Server-side safety net for state fields: uppercase, 2 letters max.
# (The form also enforces this as you type, but data can arrive other ways.)
US_STATES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("DC", "District of Columbia"), ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
    ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"),
    ("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"),
    ("MD", "Maryland"), ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"),
    ("NV", "Nevada"), ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"),
    ("NY", "New York"), ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"), ("PR", "Puerto Rico"),
    ("RI", "Rhode Island"), ("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"),
    ("TX", "Texas"), ("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"),
    ("WA", "Washington"), ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
]


# The distinct kinds of "contract" a contact can have recorded, each its own
# separate free-text entry -- shown on the contact page only for contacts
# who attended Discovery, since these are Discovery training artifacts.
CONTRACT_KINDS = {
    "D1": "Your Contract",
    "D1S": "Your Stretch Song",
    "D2": "Your Poem",
    "D3": "Your Future",
    "D5": "Your Spiritual Renewal Contract",
    "D6": "Your Couples Relationship Contract",
    "D6S": "Relationship Song Chosen for your Partner",
}

# Kinds that get a single-line input instead of a multi-row textarea.
CONTRACT_SINGLE_LINE_KINDS = {"D1", "D1S", "D6S"}

# Example text shown as a placeholder for the kinds Kent gave an example
# for. Kinds not listed here just get an empty textarea -- there wasn't a
# short example that would fit as a placeholder.
CONTRACT_EXAMPLES = {
    "D1": "I am a Strong, Confident Man",
}


@app.context_processor
def inject_program_name():
    """Makes {{ program_name }} available in every template (see config.py)."""
    return {"program_name": PROGRAM_NAME}


@app.context_processor
def inject_us_states():
    """Makes the state dropdown's options available to every template
    (internal CRM pages and the public forms alike) without having to pass
    it from every individual render_template call."""
    return {"us_states": US_STATES}


_VALID_STATE_CODES = {code for code, _ in US_STATES}


def clean_state(value):
    value = (value or "").strip().upper()
    return value if value in _VALID_STATE_CODES else None


_DOB_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def clean_dob(value):
    """Normalizes a typed MM/DD/YYYY date of birth to a consistent
    zero-padded MM/DD/YYYY (e.g. "8/5/1953" -> "08/05/1953"), so every
    contact's DOB is stored the same way no matter how it was typed.
    Leaves anything that doesn't match that shape untouched (rather than
    erasing it) -- it may just be a date still being typed mid-autosave,
    or an older record saved before this field took free text; either
    way, silently discarding it would lose data instead of fixing it."""
    value = (value or "").strip()
    if not value:
        return None
    match = _DOB_PATTERN.match(value)
    if not match:
        return value
    month, day, year = match.groups()
    try:
        parsed = date(int(year), int(month), int(day))
    except ValueError:
        return value
    return parsed.strftime("%m/%d/%Y")


# Safety net for the Age field: only save it if it's actually a whole number.
def clean_age(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# The contact "profile" fields shared by the internal Add/Edit Contact form
# and the public "complete your profile" form (self-service, no login).
# Deliberately excludes `status` and `notes` -- those are administrative
# (status is an internal pipeline stage; notes may hold internal staff
# commentary about the person) and are never shown on or settable from the
# public form.
CONTACT_PROFILE_FIELDS = [
    "first_name", "last_name", "email", "email_2", "email_3", "cell_phone", "home_phone", "work_phone",
    "partner_name", "partner_cell", "partner_email",
    "emergency_contact_name", "emergency_contact_cell", "emergency_contact_home",
    "sponsor_name", "sponsor_phone", "sponsor_email",
    "sponsor_street_address", "sponsor_street_address_2", "sponsor_city", "sponsor_state", "sponsor_zip",
    "street_address", "street_address_2", "city", "state", "zip",
    "gender", "gender_self_description", "age", "dob", "ethnicity", "ethnicity_self_description",
    "marital_status", "living_with_partner",
    "children_ages", "employment_status", "occupation", "occupation_self_description", "employer",
    "spiritual_orientation", "spiritual_orientation_self_description",
    "overall_health", "education",
    "bluesky_attendance", "discovery_attendance",
    "d1_month_year", "d2_month_year", "d3_month_year",
    "refocus_month_year", "relationship_training_month_year",
    "t1_month_year", "t2_month_year", "t3_month_year",
    "bluesky_refocus_month_year", "bluesky_relationship_training_month_year",
    "marketing_source", "marketing_source_self_description",
]


def _title_case_name(value):
    """Capitalizes the first letter of each word without touching the rest
    of the word -- fixes someone typing a name in all-lowercase (e.g. "mark
    thompson" -> "Mark Thompson") while leaving a name typed correctly
    (e.g. "McDonald") alone."""
    return " ".join(word[:1].upper() + word[1:] if word else word for word in value.split())


def _clean_contact_field(name, form):
    if name in ("state", "sponsor_state"):
        return clean_state(form.get(name))
    if name == "age":
        return clean_age(form.get(name))
    if name == "first_name":
        return form["first_name"].strip()
    if name == "last_name":
        return form.get("last_name", "").strip() or None
    if name == "partner_name":
        value = form.get(name, "").strip()
        return _title_case_name(value) if value else None
    if name == "dob":
        return clean_dob(form.get(name))
    return form.get(name) or None


def contact_profile_values_from_form(form):
    """Returns {column: cleaned_value} for every CONTACT_PROFILE_FIELDS
    column, built from a submitted form (internal or public)."""
    return {name: _clean_contact_field(name, form) for name in CONTACT_PROFILE_FIELDS}


# ---------- helpers ----------

def prerequisite_status(conn, contact_id, program_id):
    """Return (ok: bool, message: str) for whether contact_id may enroll in program_id."""
    prog = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    if not prog["prerequisite_program_id"]:
        return True, ""
    prereq = conn.execute("SELECT * FROM programs WHERE id = ?", (prog["prerequisite_program_id"],)).fetchone()
    done = conn.execute(
        """SELECT e.* FROM enrollments e
           JOIN cohorts c ON c.id = e.cohort_id
           WHERE e.contact_id = ? AND c.program_id = ? AND e.attended = 1
           ORDER BY c.session_date DESC LIMIT 1""",
        (contact_id, prereq["id"]),
    ).fetchone()
    if not done:
        return False, f"Has not attended {prereq['code']} yet."
    return True, f"Attended {prereq['code']} on {done['created_at'][:10]}."


def training_path(conn, contact_id):
    """Return dict of program code -> most recent attended enrollment row, plus graduation_date if Bluesky 3 attended."""
    rows = conn.execute(
        """SELECT p.code, e.*, c.session_number, c.session_date
           FROM enrollments e
           JOIN cohorts c ON c.id = e.cohort_id
           JOIN programs p ON p.id = c.program_id
           WHERE e.contact_id = ? AND e.attended = 1
           ORDER BY c.session_date ASC""",
        (contact_id,),
    ).fetchall()
    by_code = {}
    for r in rows:
        by_code[r["code"]] = r  # keep latest
    graduation_date = by_code["D3"]["session_date"] if "D3" in by_code else None
    return by_code, graduation_date


# ---------- dashboard ----------

@crm.route("/")
def dashboard():
    conn = get_db()
    contact_count = conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
    upcoming = conn.execute(
        """SELECT c.*, p.code, p.name FROM cohorts c
           JOIN programs p ON p.id = c.program_id
           WHERE c.session_date >= date('now')
           ORDER BY c.session_date ASC LIMIT 10"""
    ).fetchall()
    programs = conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
    conn.close()
    return render_template("dashboard.html", contact_count=contact_count, upcoming=upcoming, programs=programs)


# ---------- contacts ----------

@crm.route("/contacts")
@area_required("contacts")
def contacts_list():
    q = request.args.get("q", "").strip()
    conn = get_db()
    if q:
        rows = conn.execute(
            f"""SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts
                WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ?
                ORDER BY last_name, first_name""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts ORDER BY last_name, first_name LIMIT 200"
        ).fetchall()
    conn.close()
    return render_template("contacts_list.html", contacts=rows, q=q)


@crm.route("/contacts/new", methods=["GET", "POST"])
@area_required("contacts")
def contact_new():
    if request.method == "POST":
        conn = get_db()
        values = contact_profile_values_from_form(request.form)
        values["notes"] = request.form.get("notes") or None
        values["status"] = request.form.get("status") or "Interested Party"
        columns = CONTACT_PROFILE_FIELDS + ["notes", "status"]
        placeholders = ", ".join(["?"] * len(columns))
        conn.execute(
            f"INSERT INTO contacts ({', '.join(columns)}) VALUES ({placeholders})",
            [values[c] for c in columns],
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        photo_result = process_contact_photo(request.files.get("photo"))
        if photo_result:
            upsert_contact_photo(conn, new_id, *photo_result)
            conn.commit()
        conn.close()

        if request.form.get("for_team_login"):
            link_staff_id = request.form.get("link_staff_id")
            if link_staff_id:
                return redirect(url_for(".staff_link_contact", staff_id=link_staff_id, contact_id=new_id))
            return redirect(url_for(".staff_new_for_contact", contact_id=new_id))

        flash("Contact added.")
        return redirect(url_for(".contact_detail", contact_id=new_id))
    return render_template(
        "contact_form.html",
        for_team_login=request.args.get("for_team_login"),
        link_staff_id=request.args.get("link_staff_id"),
    )


@crm.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
@area_required("contacts")
def contact_edit(contact_id):
    """The full Add-Contact form, pre-filled, for fixing or filling in
    anything on an existing contact -- name, email, address, emergency
    contact, sponsor info, personal info, all of it (unlike the piecemeal
    Sponsor/Personal/Photo forms further down this contact's page)."""
    conn = get_db()
    contact = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if not contact:
        conn.close()
        flash("Couldn't find that contact.")
        return redirect(url_for(".contacts_list"))

    if request.method == "POST":
        values = contact_profile_values_from_form(request.form)
        values["notes"] = request.form.get("notes") or None
        values["status"] = request.form.get("status") or "Interested Party"
        columns = CONTACT_PROFILE_FIELDS + ["notes", "status"]
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ?",
            [values[c] for c in columns] + [contact_id],
        )
        conn.commit()
        photo_result = process_contact_photo(request.files.get("photo"))
        if photo_result:
            upsert_contact_photo(conn, contact_id, *photo_result)
            conn.commit()
        conn.close()
        flash("Contact updated.")
        return redirect(url_for(".contact_detail", contact_id=contact_id))

    conn.close()
    return render_template("contact_form.html", contact=contact)


@crm.route("/contacts/<int:contact_id>/profile-link/new", methods=["POST"])
@area_required("contacts")
def contact_profile_link_new(contact_id):
    """Generates (once) the unguessable link that lets this person fill in
    or update their own full Contact profile with no CRM login -- e.g. to
    email past Discovery/Bluesky participants asking them to complete
    their info."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if contact and not contact["profile_token"]:
        conn.execute(
            "UPDATE contacts SET profile_token = ? WHERE id = ?",
            (secrets.token_urlsafe(24), contact_id),
        )
        conn.commit()
    conn.close()
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/send-excitement-email", methods=["POST"])
@area_required("contacts")
def contact_send_excitement_email(contact_id):
    """Sends the "Excitement is Building" outreach email to this one
    contact, on demand from their detail page -- the same copy used for
    the bulk blast, so staff can send or re-send it to one person at a
    time (new leads, someone who asks again, a bounced address that got
    corrected) without needing the Web Shell."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        abort(404)
    if not contact["email"]:
        conn.close()
        flash("Can't send -- this contact has no email address on file.")
        return redirect(url_for(".contact_detail", contact_id=contact_id))

    conn.close()

    # The "reach out to me personally" link (/connect/<token>) is left out
    # of this email for now -- Kent's shortening the letter and may want
    # to surface that request instead through the short form later. The
    # connect_url param on excitement_blast_content still works, so it's a
    # one-line change to turn back on: generate/reuse a profile_token and
    # pass connect_url=url_for("public_connect_request", token=token, _external=True).
    html_content, text_content = excitement_blast_content(contact["first_name"])
    try:
        send_email(
            to_email=contact["email"],
            to_name=contact["first_name"],
            subject=EXCITEMENT_BLAST_SUBJECT,
            html_content=html_content,
            text_content=text_content,
        )
        flash(f"Excitement email sent to {contact['first_name']} ({contact['email']}).")
    except EmailSendError as e:
        flash(f"Couldn't send the email: {e}")
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/send-business-plan", methods=["POST"])
@area_required("contacts")
@leadership_required
def contact_send_business_plan(contact_id):
    """Leadership-only: sends the Preliminary Business Plan email to this one
    contact on demand from their detail page -- the same copy people get when
    they click "Send me the Preliminary Discovery Plan" in the long-form
    thank-you email, just without needing them to ask first. Best used when
    someone has asked for it in person or by phone."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    conn.close()
    if not contact:
        abort(404)
    if not contact["email"]:
        flash("Can't send -- this contact has no email address on file.")
        return redirect(url_for(".contact_detail", contact_id=contact_id))

    html_content, text_content = preliminary_plan_content(contact["first_name"])
    try:
        send_email(
            to_email=contact["email"],
            to_name=contact["first_name"],
            subject=PRELIMINARY_PLAN_SUBJECT,
            html_content=html_content,
            text_content=text_content,
        )
        print(
            f"SENT business plan to contact {contact_id} ({contact['email']}) by {session.get('staff_name')}",
            flush=True,
        )
        flash(f"Business plan sent to {contact['first_name']} ({contact['email']}).")
    except EmailSendError as e:
        print(f"EmailSendError sending business plan to {contact['email']}: {e}", flush=True)
        flash(f"Couldn't send the business plan: {e}")
    return redirect(url_for(".contact_detail", contact_id=contact_id))


PROFILE_TOKEN_COOKIE = "bsky_profile_token"
PROFILE_TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


@app.route("/complete-profile", methods=["GET", "POST"])
def public_complete_profile_start():
    """The link meant for posting publicly (social media, a general email
    blast, etc) -- not tied to any one contact. Shows a cover letter and a
    Start button. Starting creates their record right away and remembers
    them (via a cookie) so if they leave and come back to this same public
    link later -- even without saving/bookmarking anything -- they land
    back on their own in-progress form instead of starting over."""
    existing_token = request.cookies.get(PROFILE_TOKEN_COOKIE)
    if existing_token:
        conn = get_db()
        contact = conn.execute("SELECT id FROM contacts WHERE profile_token = ?", (existing_token,)).fetchone()
        conn.close()
        if contact:
            return redirect(url_for("public_complete_profile", token=existing_token))

    if request.method == "POST":
        token = secrets.token_urlsafe(24)
        conn = get_db()
        conn.execute(
            "INSERT INTO contacts (first_name, status, notes, profile_token) VALUES ('', 'Interested Party', ?, ?)",
            ("Started via the public self-service profile form.", token),
        )
        conn.commit()
        conn.close()
        resp = make_response(redirect(url_for("public_complete_profile", token=token)))
        resp.set_cookie(PROFILE_TOKEN_COOKIE, token, max_age=PROFILE_TOKEN_COOKIE_MAX_AGE, samesite="Lax")
        return resp

    return render_template("public_complete_profile_start.html")


@app.route("/complete-profile/<token>", methods=["GET", "POST"])
def public_complete_profile(token):
    """Public, no-login page: lets someone fill in or update their own full
    Contact profile, whether they started from a personalized link (an
    existing contact) or the general public link above (a brand-new one).
    Deliberately can't set/see `status` or `notes` -- those stay
    internal-only. Saves happen constantly in the background as they type
    (see the autosave script in the template) so leaving mid-form loses
    nothing; the visible Submit button is just their "I'm done" moment."""
    conn = get_db()
    contact = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE profile_token = ?", (token,)
    ).fetchone()
    if not contact:
        conn.close()
        return render_template("public_complete_profile.html", contact=None), 404

    if request.method == "POST":
        values = contact_profile_values_from_form(request.form)
        set_clause = ", ".join(f"{c} = ?" for c in CONTACT_PROFILE_FIELDS)
        conn.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ?",
            [values[c] for c in CONTACT_PROFILE_FIELDS] + [contact["id"]],
        )
        conn.commit()
        photo_result = process_contact_photo(request.files.get("photo"))
        if photo_result:
            upsert_contact_photo(conn, contact["id"], *photo_result)
            conn.commit()
        conn.close()

        resp = make_response()
        if request.form.get("autosave"):
            # Background save from the page's own JS -- no redirect, no flash,
            # just a quiet 204 so the form isn't disturbed while they're typing.
            resp = make_response(("", 204))
        else:
            # The real "I'm done" click (not an autosave) -- thank them for
            # taking the time, and give them their personal /connect link
            # in case they'd like Kent to follow up directly. Best-effort:
            # their info is already saved either way.
            thankyou_email = values["email"] or contact["email"]
            if thankyou_email:
                connect_url = url_for("public_connect_request", token=token, _external=True)
                plan_url = url_for("request_preliminary_plan", token=token, _external=True)
                html_content, text_content = longform_thankyou_content(
                    values["first_name"], connect_url, plan_url
                )
                try:
                    send_email(
                        to_email=thankyou_email,
                        to_name=values["first_name"],
                        subject=LONGFORM_THANKYOU_SUBJECT,
                        html_content=html_content,
                        text_content=text_content,
                    )
                except EmailSendError as e:
                    print(f"EmailSendError sending long-form thank-you to {thankyou_email}: {e}", flush=True)
            flash("Thank you -- your information has been saved.")
            resp = make_response(redirect(url_for("public_complete_profile", token=token)))
        resp.set_cookie(PROFILE_TOKEN_COOKIE, token, max_age=PROFILE_TOKEN_COOKIE_MAX_AGE, samesite="Lax")
        return resp

    contracts_by_kind = {}
    for kind in CONTRACT_KINDS:
        row = conn.execute(
            "SELECT * FROM contracts WHERE contact_id = ? AND kind = ?", (contact["id"], kind)
        ).fetchone()
        contracts_by_kind[kind] = {"contract": row}
    conn.close()
    resp = make_response(render_template(
        "public_complete_profile.html",
        contact=contact,
        contract_kinds=CONTRACT_KINDS,
        contract_examples=CONTRACT_EXAMPLES,
        contract_single_line_kinds=CONTRACT_SINGLE_LINE_KINDS,
        contracts_by_kind=contracts_by_kind,
    ))
    resp.set_cookie(PROFILE_TOKEN_COOKIE, token, max_age=PROFILE_TOKEN_COOKIE_MAX_AGE, samesite="Lax")
    return resp


@app.route("/complete-profile/<token>/contract", methods=["POST"])
def public_contract_upsert(token):
    """Public, no-login counterpart to the staff-only contract_upsert --
    lets the token-holder save their own D1/D1S/D2/D3/D5/D6/D6S contract
    text (their personal statement, poem, song choice, etc.) directly from
    the long form, the same autosave-as-you-type way the rest of that form
    already works. Scoped strictly to the contact that owns this token --
    there's no contact_id in the URL, so there's nothing to guess at."""
    conn = get_db()
    contact = conn.execute("SELECT id FROM contacts WHERE profile_token = ?", (token,)).fetchone()
    if not contact:
        conn.close()
        abort(404)

    kind = request.form.get("kind", "D1")
    if kind not in CONTRACT_KINDS:
        conn.close()
        abort(404)

    contact_id = contact["id"]
    existing = conn.execute(
        "SELECT * FROM contracts WHERE contact_id = ? AND kind = ?", (contact_id, kind)
    ).fetchone()
    text = request.form.get("text", "")
    if existing:
        if text != (existing["current_text"] or ""):
            conn.execute(
                "INSERT INTO contract_revisions (contract_id, text, context) VALUES (?, ?, ?)",
                (existing["id"], text, "Submitted via the public long form"),
            )
            conn.execute("UPDATE contracts SET current_text = ? WHERE id = ?", (text, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO contracts (contact_id, kind, current_text) VALUES (?, ?, ?)",
            (contact_id, kind, text),
        )
    conn.commit()
    conn.close()

    resp = make_response(("", 204))
    resp.set_cookie(PROFILE_TOKEN_COOKIE, token, max_age=PROFILE_TOKEN_COOKIE_MAX_AGE, samesite="Lax")
    return resp


@app.route("/connect/<token>", methods=["GET", "POST"])
def public_connect_request(token):
    """Public, no-login page linked from the Excitement email: lets someone
    ask Kent to personally follow up with them (by email or phone) instead
    of replying to the email itself -- so those requests land in a simple
    CRM queue (see contact_requests below) rather than piling up in his
    personal inbox. Sends them an immediate personal auto-reply too, so
    they know it didn't just disappear."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE profile_token = ?", (token,)).fetchone()
    if not contact:
        conn.close()
        return render_template("connect.html", contact=None), 404

    if request.method == "POST":
        method = request.form.get("method")
        if method not in ("Email", "Phone"):
            conn.close()
            abort(400)
        phone = request.form.get("phone", "").strip() or None
        note = request.form.get("note", "").strip() or None
        conn.execute(
            """UPDATE contacts
               SET contact_request_method = ?, contact_request_phone = ?,
                   contact_request_note = ?, contact_requested_at = ?
               WHERE id = ?""",
            (method, phone, note, datetime.utcnow().isoformat(), contact["id"]),
        )
        conn.commit()

        if contact["email"]:
            html_content, text_content = connect_request_confirmation_content(contact["first_name"], method)
            try:
                send_email(
                    to_email=contact["email"],
                    to_name=contact["first_name"],
                    subject=CONNECT_REQUEST_SUBJECT,
                    html_content=html_content,
                    text_content=text_content,
                )
            except EmailSendError as e:
                print(f"EmailSendError sending connect-request confirmation to {contact['email']}: {e}", flush=True)
        conn.close()
        return redirect(url_for("public_connect_request", token=token, submitted="1"))

    conn.close()
    submitted = request.args.get("submitted") == "1"
    return render_template("connect.html", contact=contact, submitted=submitted)


@app.route("/discovery/plan/<token>", methods=["GET", "POST"])
def request_preliminary_plan(token):
    """Public, no-login page linked from the long-form thank-you email's
    "Send me the Preliminary Discovery Plan" button. Deliberately opt-in
    -- the (much longer) plan email is only ever sent if someone clicks
    through here and confirms, never automatically alongside the
    thank-you note."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE profile_token = ?", (token,)).fetchone()
    if not contact:
        conn.close()
        return render_template("request_plan.html", contact=None), 404

    if request.method == "POST":
        if contact["email"]:
            html_content, text_content = preliminary_plan_content(contact["first_name"])
            try:
                send_email(
                    to_email=contact["email"],
                    to_name=contact["first_name"],
                    subject=PRELIMINARY_PLAN_SUBJECT,
                    html_content=html_content,
                    text_content=text_content,
                )
            except EmailSendError as e:
                print(f"EmailSendError sending preliminary plan to {contact['email']}: {e}", flush=True)
        conn.close()
        return redirect(url_for("request_preliminary_plan", token=token, sent="1"))

    conn.close()
    sent = request.args.get("sent") == "1"
    return render_template("request_plan.html", contact=contact, sent=sent)


@crm.route("/contact-requests")
@area_required("contacts")
def contact_requests_list():
    """Simple queue of everyone who's asked Kent to personally reach out
    via the /connect page -- oldest request first, so it reads like a
    to-do list. Clearing one (see contact_request_resolve) just removes it
    from this list; the fact that they asked stays out of the contact's
    permanent notes unless staff add it themselves."""
    conn = get_db()
    rows = conn.execute(
        f"""SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts
            WHERE contact_requested_at IS NOT NULL
            ORDER BY contact_requested_at ASC"""
    ).fetchall()
    conn.close()
    return render_template("contact_requests.html", contacts=rows)


@crm.route("/contacts/<int:contact_id>/contact-request/resolve", methods=["POST"])
@area_required("contacts")
def contact_request_resolve(contact_id):
    """Marks a "please reach out to me" request as handled -- just clears
    the request fields so it drops off the queue; it doesn't touch
    anything else on the contact."""
    conn = get_db()
    conn.execute(
        """UPDATE contacts
           SET contact_request_method = NULL, contact_request_phone = NULL,
               contact_request_note = NULL, contact_requested_at = NULL
           WHERE id = ?""",
        (contact_id,),
    )
    conn.commit()
    conn.close()
    flash("Marked as followed up.")
    return redirect(request.referrer or url_for(".contact_requests_list"))


@crm.route("/contacts/<int:contact_id>")
@area_required("contacts")
def contact_detail(contact_id):
    conn = get_db()
    contact = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    enrollments = conn.execute(
        """SELECT e.*, p.code, p.name, c.session_number, c.session_date, sg.label AS group_label
           FROM enrollments e
           JOIN cohorts c ON c.id = e.cohort_id
           JOIN programs p ON p.id = c.program_id
           LEFT JOIN small_groups sg ON sg.id = e.small_group_id
           WHERE e.contact_id = ? ORDER BY c.session_date DESC""",
        (contact_id,),
    ).fetchall()
    # D1 Contract, D2 "Your Poem", D6 "Spiritual Contract" -- each a separate
    # kind of the same contract/revisions structure, one per contact per kind.
    contracts_by_kind = {}
    for kind in CONTRACT_KINDS:
        row = conn.execute(
            "SELECT * FROM contracts WHERE contact_id = ? AND kind = ?", (contact_id, kind)
        ).fetchone()
        kind_revisions = []
        if row:
            kind_revisions = conn.execute(
                "SELECT * FROM contract_revisions WHERE contract_id = ? ORDER BY revised_at", (row["id"],)
            ).fetchall()
        contracts_by_kind[kind] = {"contract": row, "revisions": kind_revisions}
    donations = conn.execute(
        "SELECT * FROM donations WHERE contact_id = ? ORDER BY donation_date DESC", (contact_id,)
    ).fetchall()
    staffing = conn.execute(
        """SELECT cs.role, p.code, c.session_number, c.session_date
           FROM cohort_staffing cs
           JOIN cohorts c ON c.id = cs.cohort_id
           JOIN programs p ON p.id = c.program_id
           WHERE cs.contact_id = ?
           UNION ALL
           SELECT sgs.role, p.code, c.session_number, c.session_date
           FROM small_group_staffing sgs
           JOIN small_groups sg ON sg.id = sgs.small_group_id
           JOIN cohorts c ON c.id = sg.cohort_id
           JOIN programs p ON p.id = c.program_id
           WHERE sgs.contact_id = ?
           ORDER BY session_date DESC""",
        (contact_id, contact_id),
    ).fetchall()
    path, grad_date = training_path(conn, contact_id)
    conn.close()
    return render_template(
        "contact_detail.html",
        contact=contact,
        enrollments=enrollments,
        contract_kinds=CONTRACT_KINDS,
        contract_examples=CONTRACT_EXAMPLES,
        contract_single_line_kinds=CONTRACT_SINGLE_LINE_KINDS,
        contracts_by_kind=contracts_by_kind,
        donations=donations,
        staffing=staffing,
        path=path,
        grad_date=grad_date,
        today=date.today().isoformat(),
    )


# ---------- "enrolled by" and the TA / Team Captain requirement ----------
# To TA in D1 or the Relationship training a person must have brought in at
# least TA_MIN_ENROLLED people; to be a Team Captain, at least
# CAPTAIN_MIN_ENROLLED. These are lifetime counts of DIFFERENT people recorded
# as "brought in by" that person on an enrollment (anyone who enrolled counts,
# whether or not they attended). The requirement only produces a warning --
# leadership can always make an exception.
TA_MIN_ENROLLED = 1
CAPTAIN_MIN_ENROLLED = 2


def people_enrolled_count(conn, contact_id):
    """How many different people this contact has brought into a training."""
    row = conn.execute(
        """SELECT COUNT(DISTINCT contact_id) c FROM enrollments
           WHERE enrolled_by_contact_id = ? AND contact_id != ?""",
        (contact_id, contact_id),
    ).fetchone()
    return row["c"]


def staffing_requirement(role, duty):
    """Minimum people-brought-in for a staffing assignment (0 = none)."""
    if duty == "Team Captain":
        return CAPTAIN_MIN_ENROLLED
    if role == "TA":
        return TA_MIN_ENROLLED
    return 0


def requirement_warning(conn, contact_id, role, duty):
    """A plain-English heads-up if this person hasn't met the requirement for
    the assignment just made, or None if they have (or it has none)."""
    need = staffing_requirement(role, duty)
    if not need:
        return None
    have = people_enrolled_count(conn, contact_id)
    if have >= need:
        return None
    who = conn.execute(
        f"SELECT {FULL_NAME_SQL} AS n FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    label = "Team Captain" if duty == "Team Captain" else "TA"
    return (
        f"Heads-up: {who['n']} has brought in {have} "
        f"{'person' if have == 1 else 'people'}; the {label} requirement is {need}. "
        f"Assigned anyway -- record who they brought in on the cohort roster to update this."
    )


def parse_contact_pick(conn, raw):
    """Turns what a 'Brought in by' box holds ('Full Name [#123]', picked from
    the autocomplete list) into (contact_id, error). Blank -> (None, None)."""
    import re as _re
    raw = (raw or "").strip()
    if not raw:
        return None, None
    m = _re.search(r"\[#(\d+)\]\s*$", raw)
    if not m:
        return None, "Please pick a name from the list that appears as you type."
    cid = int(m.group(1))
    if not conn.execute("SELECT 1 FROM contacts WHERE id = ?", (cid,)).fetchone():
        return None, "That contact wasn't found."
    return cid, None


def _contact_delete_blockers(conn, contact_id):
    """Returns a list of plain-English reasons this contact can't be safely
    deleted yet -- real training/financial history that would otherwise be
    silently destroyed. Empty list means it's safe to delete."""
    reasons = []
    counts = [
        ("enrollments", "enrolled in a training cohort"),
        ("contracts", "a saved contract (D1/D2/D6)"),
        ("donations", "a recorded donation"),
        ("cohort_staffing", "listed as cohort staff"),
        ("small_group_staffing", "listed as small-group staff"),
    ]
    for table, phrase in counts:
        row = conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE contact_id = ?", (contact_id,)).fetchone()
        if row["c"]:
            reasons.append(f"has {phrase}")
    led = conn.execute(
        "SELECT COUNT(*) c FROM contracts WHERE led_by_contact_id = ? OR assisted_by_contact_id = ?",
        (contact_id, contact_id),
    ).fetchone()
    if led["c"]:
        reasons.append("is credited as a contract facilitator/assistant for someone else")
    brought = conn.execute(
        "SELECT COUNT(*) c FROM enrollments WHERE enrolled_by_contact_id = ?", (contact_id,)
    ).fetchone()
    if brought["c"]:
        reasons.append("is recorded as having brought other people into a training")
    staff_row = conn.execute("SELECT 1 FROM staff WHERE contact_id = ?", (contact_id,)).fetchone()
    if staff_row:
        reasons.append("is linked to a CRM staff login (unlink it from the Team page first)")
    return reasons


def _contact_history_counts(conn, contact_id):
    """How many rows of training/financial history hang off this contact --
    shown on the delete page so leadership sees exactly what 'Clear history'
    will erase. Returns a list of (plain-English label, count) with zeros
    left out."""
    items = []
    for label, sql, params in [
        ("training enrollments", "SELECT COUNT(*) c FROM enrollments WHERE contact_id = ?", (contact_id,)),
        ("saved contracts (D1/D2/D6)", "SELECT COUNT(*) c FROM contracts WHERE contact_id = ?", (contact_id,)),
        ("recorded donations", "SELECT COUNT(*) c FROM donations WHERE contact_id = ?", (contact_id,)),
        ("cohort staff assignments", "SELECT COUNT(*) c FROM cohort_staffing WHERE contact_id = ?", (contact_id,)),
        ("small-group staff assignments", "SELECT COUNT(*) c FROM small_group_staffing WHERE contact_id = ?", (contact_id,)),
        (
            "contracts where they are credited as facilitator/assistant (the credit is removed, the other person's contract is kept)",
            "SELECT COUNT(*) c FROM contracts WHERE led_by_contact_id = ? OR assisted_by_contact_id = ?",
            (contact_id, contact_id),
        ),
        (
            "enrollments where they are recorded as having brought the person in (the credit is removed, the other person's enrollment is kept)",
            "SELECT COUNT(*) c FROM enrollments WHERE enrolled_by_contact_id = ?",
            (contact_id,),
        ),
    ]:
        n = conn.execute(sql, params).fetchone()["c"]
        if n:
            items.append((label, n))
    return items


def _clear_contact_history(conn, contact_id):
    """Erases this contact's training and financial history so the contact
    itself can be kept (or deleted). Never deletes anyone else's records: if
    this contact is credited as facilitator/assistant on another person's
    contract, only that credit is cleared. Does NOT touch a linked CRM staff
    login. Caller commits."""
    # Things hanging off this contact's enrollments and contracts first
    conn.execute(
        "DELETE FROM feedback_responses WHERE enrollment_id IN (SELECT id FROM enrollments WHERE contact_id = ?)",
        (contact_id,),
    )
    conn.execute(
        "DELETE FROM training_profiles WHERE enrollment_id IN (SELECT id FROM enrollments WHERE contact_id = ?)",
        (contact_id,),
    )
    conn.execute(
        "DELETE FROM contract_revisions WHERE contract_id IN (SELECT id FROM contracts WHERE contact_id = ?)",
        (contact_id,),
    )
    conn.execute("DELETE FROM contracts WHERE contact_id = ?", (contact_id,))
    conn.execute("UPDATE contracts SET led_by_contact_id = NULL WHERE led_by_contact_id = ?", (contact_id,))
    conn.execute("UPDATE contracts SET assisted_by_contact_id = NULL WHERE assisted_by_contact_id = ?", (contact_id,))
    conn.execute("UPDATE enrollments SET enrolled_by_contact_id = NULL WHERE enrolled_by_contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM enrollments WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM donations WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM cohort_staffing WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM small_group_staffing WHERE contact_id = ?", (contact_id,))


def _typed_name_matches(contact, form):
    typed = " ".join((form.get("confirm_name") or "").split()).lower()
    return bool(typed) and typed == " ".join((contact["full_name"] or "").split()).lower()


@crm.route("/contacts/<int:contact_id>/delete", methods=["GET"])
@area_required("contacts")
@leadership_required
def contact_delete_confirm(contact_id):
    conn = get_db()
    contact = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if not contact:
        conn.close()
        flash("Couldn't find that contact.")
        return redirect(url_for(".contacts_list"))
    blockers = _contact_delete_blockers(conn, contact_id)
    history = _contact_history_counts(conn, contact_id)
    has_staff_link = bool(conn.execute("SELECT 1 FROM staff WHERE contact_id = ?", (contact_id,)).fetchone())
    conn.close()
    return render_template(
        "contact_delete_confirm.html",
        contact=contact,
        blockers=blockers,
        history=history,
        has_staff_link=has_staff_link,
    )


@crm.route("/contacts/<int:contact_id>/clear-history", methods=["POST"])
@area_required("contacts")
@leadership_required
def contact_clear_history(contact_id):
    """Leadership-only: wipes this contact's training/financial history but
    keeps the contact. Requires typing the contact's full name."""
    conn = get_db()
    contact = conn.execute(f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        flash("Couldn't find that contact.")
        return redirect(url_for(".contacts_list"))
    if not _typed_name_matches(contact, request.form):
        conn.close()
        flash("The name you typed didn't match -- nothing was cleared.")
        return redirect(url_for(".contact_delete_confirm", contact_id=contact_id))
    history = _contact_history_counts(conn, contact_id)
    _clear_contact_history(conn, contact_id)
    conn.commit()
    conn.close()
    summary = ", ".join(f"{n} {label.split(' (')[0]}" for label, n in history) or "nothing to clear"
    print(f"CLEARED HISTORY for contact {contact_id} ({contact['full_name']}) by {session.get('staff_name')}: {summary}", flush=True)
    flash(f"Cleared history for {contact['full_name']}: {summary}.")
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@area_required("contacts")
@leadership_required
def contact_delete(contact_id):
    conn = get_db()
    contact = conn.execute(f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        flash("Couldn't find that contact.")
        return redirect(url_for(".contacts_list"))
    cleared_summary = None
    if request.form.get("clear_history") == "1":
        # Leadership chose "clear history and delete": needs the typed name,
        # and a linked CRM staff login still has to be unlinked on the Team
        # page first (we never delete a login as a side effect).
        if not _typed_name_matches(contact, request.form):
            conn.close()
            flash("The name you typed didn't match -- nothing was cleared or deleted.")
            return redirect(url_for(".contact_delete_confirm", contact_id=contact_id))
        if conn.execute("SELECT 1 FROM staff WHERE contact_id = ?", (contact_id,)).fetchone():
            conn.close()
            flash("This contact is linked to a CRM staff login -- unlink it from the Team page first. Nothing was changed.")
            return redirect(url_for(".contact_delete_confirm", contact_id=contact_id))
        history = _contact_history_counts(conn, contact_id)
        _clear_contact_history(conn, contact_id)
        cleared_summary = ", ".join(f"{n} {label.split(' (')[0]}" for label, n in history) or "no history"
    else:
        blockers = _contact_delete_blockers(conn, contact_id)
        if blockers:
            conn.close()
            flash("Couldn't delete -- this contact " + "; and ".join(blockers) + ". Remove that first, or ask for help.")
            return redirect(url_for(".contact_detail", contact_id=contact_id))
    name = contact["full_name"]
    conn.execute("DELETE FROM contact_photos WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()
    if cleared_summary is not None:
        print(f"CLEARED HISTORY AND DELETED contact {contact_id} ({name}) by {session.get('staff_name')}: {cleared_summary}", flush=True)
        flash(f"Deleted {name} (history cleared first: {cleared_summary}).")
    else:
        flash(f"Deleted {name}.")
    return redirect(url_for(".contacts_list"))


@crm.route("/contacts/<int:contact_id>/sponsor", methods=["POST"])
@area_required("contacts")
def set_sponsor_name(contact_id):
    conn = get_db()
    conn.execute(
        """UPDATE contacts SET sponsor_name = ?, sponsor_phone = ?, sponsor_email = ?,
                                sponsor_street_address = ?, sponsor_street_address_2 = ?,
                                sponsor_city = ?, sponsor_state = ?, sponsor_zip = ?
           WHERE id = ?""",
        (
            request.form.get("sponsor_name", "").strip() or None,
            request.form.get("sponsor_phone", "").strip() or None,
            request.form.get("sponsor_email", "").strip() or None,
            request.form.get("sponsor_street_address", "").strip() or None,
            request.form.get("sponsor_street_address_2", "").strip() or None,
            request.form.get("sponsor_city", "").strip() or None,
            clean_state(request.form.get("sponsor_state")),
            request.form.get("sponsor_zip", "").strip() or None,
            contact_id,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/photo", methods=["POST"])
@area_required("contacts")
def contact_photo_upload(contact_id):
    conn = get_db()
    photo_result = process_contact_photo(request.files.get("photo"))
    if photo_result:
        upsert_contact_photo(conn, contact_id, *photo_result)
        conn.commit()
        flash("Photo updated.")
    conn.close()
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/photo-file")
@area_required("contacts")
def contact_photo_file(contact_id):
    """Serves a contact's photo bytes straight from the database -- never
    from local disk, which Render wipes on every deploy."""
    conn = get_db()
    row = conn.execute(
        "SELECT data, content_type FROM contact_photos WHERE contact_id = ?", (contact_id,)
    ).fetchone()
    conn.close()
    if not row:
        abort(404)
    resp = make_response(bytes(row["data"]))
    resp.headers["Content-Type"] = row["content_type"] or "image/jpeg"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@crm.route("/contacts/<int:contact_id>/personal", methods=["POST"])
@area_required("contacts")
def set_personal_info(contact_id):
    conn = get_db()
    conn.execute(
        """UPDATE contacts SET gender = ?, gender_self_description = ?, age = ?, dob = ?, ethnicity = ?, ethnicity_self_description = ?, marital_status = ?,
                                living_with_partner = ?, children_ages = ?, employment_status = ?, occupation = ?, occupation_self_description = ?, employer = ?,
                                spiritual_orientation = ?, spiritual_orientation_self_description = ?, overall_health = ?,
                                education = ?, bluesky_attendance = ?, discovery_attendance = ?,
                                d1_month_year = ?, d2_month_year = ?, d3_month_year = ?,
                                refocus_month_year = ?, relationship_training_month_year = ?,
                                t1_month_year = ?, t2_month_year = ?, t3_month_year = ?,
                                bluesky_refocus_month_year = ?, bluesky_relationship_training_month_year = ?
           WHERE id = ?""",
        (
            request.form.get("gender", "").strip() or None,
            request.form.get("gender_self_description", "").strip() or None,
            clean_age(request.form.get("age")),
            clean_dob(request.form.get("dob")),
            request.form.get("ethnicity", "").strip() or None,
            request.form.get("ethnicity_self_description", "").strip() or None,
            request.form.get("marital_status", "").strip() or None,
            request.form.get("living_with_partner") or None,
            request.form.get("children_ages", "").strip() or None,
            request.form.get("employment_status", "").strip() or None,
            request.form.get("occupation", "").strip() or None,
            request.form.get("occupation_self_description", "").strip() or None,
            request.form.get("employer", "").strip() or None,
            request.form.get("spiritual_orientation", "").strip() or None,
            request.form.get("spiritual_orientation_self_description", "").strip() or None,
            request.form.get("overall_health", "").strip() or None,
            request.form.get("education", "").strip() or None,
            request.form.get("bluesky_attendance") or None,
            request.form.get("discovery_attendance") or None,
            request.form.get("d1_month_year", "").strip() or None,
            request.form.get("d2_month_year", "").strip() or None,
            request.form.get("d3_month_year", "").strip() or None,
            request.form.get("refocus_month_year", "").strip() or None,
            request.form.get("relationship_training_month_year", "").strip() or None,
            request.form.get("t1_month_year", "").strip() or None,
            request.form.get("t2_month_year", "").strip() or None,
            request.form.get("t3_month_year", "").strip() or None,
            request.form.get("bluesky_refocus_month_year", "").strip() or None,
            request.form.get("bluesky_relationship_training_month_year", "").strip() or None,
            contact_id,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/contract", methods=["POST"])
@leadership_required
def contract_upsert(contact_id):
    kind = request.form.get("kind", "D1")
    if kind not in CONTRACT_KINDS:
        abort(404)
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM contracts WHERE contact_id = ? AND kind = ?", (contact_id, kind)
    ).fetchone()
    text = request.form["text"]
    context = request.form.get("context") or None
    if existing:
        # Only record a revision (and update) when the text actually changed --
        # autosave fires repeatedly as someone types, and we don't want a
        # revision row for every pause, only for actual edits.
        if text != (existing["current_text"] or ""):
            conn.execute(
                "INSERT INTO contract_revisions (contract_id, text, context) VALUES (?, ?, ?)",
                (existing["id"], text, context),
            )
            conn.execute("UPDATE contracts SET current_text = ? WHERE id = ?", (text, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO contracts (contact_id, kind, current_text) VALUES (?, ?, ?)",
            (contact_id, kind, text),
        )
    conn.commit()
    conn.close()
    if request.form.get("autosave"):
        # Background save from the page's own JS -- no redirect, no flash.
        return ("", 204)
    flash(f"{CONTRACT_KINDS[kind]} saved.")
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/donation", methods=["POST"])
@leadership_required
def donation_new(contact_id):
    conn = get_db()
    conn.execute(
        "INSERT INTO donations (contact_id, amount, fund, donation_date, payment_method) VALUES (?, ?, ?, ?, ?)",
        (
            contact_id,
            float(request.form["amount"]),
            request.form.get("fund") or "General Operating",
            request.form.get("donation_date") or date.today().isoformat(),
            request.form.get("payment_method") or None,
        ),
    )
    conn.commit()
    conn.close()
    flash("Donation recorded.")
    return redirect(url_for(".contact_detail", contact_id=contact_id))


# ---------- programs & cohorts ----------

@crm.route("/programs")
def programs_list():
    conn = get_db()
    programs = conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
    cohorts_by_program = {}
    for p in programs:
        cohorts_by_program[p["id"]] = conn.execute(
            "SELECT * FROM cohorts WHERE program_id = ? ORDER BY session_number DESC", (p["id"],)
        ).fetchall()
    conn.close()
    return render_template("programs_list.html", programs=programs, cohorts_by_program=cohorts_by_program)


@crm.route("/programs/new", methods=["POST"])
def program_new():
    conn = get_db()
    code = request.form["code"].strip()
    name = request.form["name"].strip()
    description = request.form.get("description", "").strip() or None
    prereq = request.form.get("prerequisite_program_id") or None
    conn.execute(
        "INSERT INTO programs (code, name, description, prerequisite_program_id) VALUES (?, ?, ?, ?)",
        (code, name, description, prereq),
    )
    conn.commit()
    conn.close()
    flash(f"Program {code} added.")
    return redirect(url_for(".programs_list"))


@crm.route("/programs/<int:program_id>/edit", methods=["POST"])
def program_edit(program_id):
    conn = get_db()
    code = request.form["code"].strip()
    name = request.form["name"].strip()
    description = request.form.get("description", "").strip() or None
    prereq = request.form.get("prerequisite_program_id") or None
    if prereq and int(prereq) == program_id:
        flash("A program can't require itself as a prerequisite.")
        conn.close()
        return redirect(url_for(".programs_list"))
    conn.execute(
        "UPDATE programs SET code = ?, name = ?, description = ?, prerequisite_program_id = ? WHERE id = ?",
        (code, name, description, prereq, program_id),
    )
    conn.commit()
    conn.close()
    flash(f"Program {code} updated.")
    return redirect(url_for(".programs_list"))


@crm.route("/programs/<int:program_id>/delete", methods=["POST"])
def program_delete(program_id):
    conn = get_db()
    prog = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    if not prog:
        conn.close()
        flash("That program no longer exists.")
        return redirect(url_for(".programs_list"))

    cohort_count = conn.execute(
        "SELECT COUNT(*) AS n FROM cohorts WHERE program_id = ?", (program_id,)
    ).fetchone()["n"]
    if cohort_count:
        conn.close()
        flash(
            f"Can't delete {prog['code']} — it still has {cohort_count} session(s) scheduled. "
            f"Delete or reassign those sessions first."
        )
        return redirect(url_for(".programs_list"))

    # Clear any other program that lists this one as a prerequisite, then delete.
    conn.execute(
        "UPDATE programs SET prerequisite_program_id = NULL WHERE prerequisite_program_id = ?",
        (program_id,),
    )
    conn.execute("DELETE FROM programs WHERE id = ?", (program_id,))
    conn.commit()
    conn.close()
    flash(f"Program {prog['code']} deleted.")
    return redirect(url_for(".programs_list"))


DAY_LABEL_ORDER = ["Friday Night", "Saturday", "Sunday", "General"]


@crm.route("/programs/<int:program_id>/materials")
@program_area_required
def program_materials(program_id):
    conn = get_db()
    program = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    rows = conn.execute(
        "SELECT * FROM program_materials WHERE program_id = ? ORDER BY uploaded_at", (program_id,)
    ).fetchall()
    conn.close()
    by_day = {}
    for r in rows:
        label = r["day_label"] or "General"
        by_day.setdefault(label, []).append(r)
    ordered_days = [d for d in DAY_LABEL_ORDER if d in by_day]
    ordered_days += [d for d in by_day if d not in DAY_LABEL_ORDER]
    return render_template(
        "program_materials.html", program=program, by_day=by_day, ordered_days=ordered_days,
        day_label_choices=DAY_LABEL_ORDER,
    )


@crm.route("/programs/<int:program_id>/materials/<int:material_id>/file")
@program_area_required
def program_material_file(program_id, material_id):
    """Serves a training-material file's bytes straight from the database
    (never from local disk, which Render wipes on every deploy) after
    checking program-level authorization -- never through the public
    /static/ route."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM program_materials WHERE id = ? AND program_id = ?", (material_id, program_id)
    ).fetchone()
    conn.close()
    if not row or not row["data"]:
        abort(404)
    resp = make_response(bytes(row["data"]))
    resp.headers["Content-Type"] = row["content_type"] or "application/octet-stream"
    resp.headers["Content-Disposition"] = f'inline; filename="{row["original_filename"]}"'
    return resp


@crm.route("/programs/<int:program_id>/materials/new", methods=["POST"])
@program_area_required
def program_material_new(program_id):
    title = request.form.get("title", "").strip()
    day_label = request.form.get("day_label") or None
    material_result = process_program_material(request.files.get("file"))
    if material_result:
        data, content_type = material_result
        original_filename = request.files["file"].filename
        filename = f"program_{program_id}_{secrets.token_hex(6)}"
        conn = get_db()
        conn.execute(
            """INSERT INTO program_materials
               (program_id, day_label, title, filename, original_filename, data, content_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (program_id, day_label, title or original_filename, filename, original_filename, data, content_type),
        )
        conn.commit()
        conn.close()
        flash("Material uploaded.")
    return redirect(url_for(".program_materials", program_id=program_id))


@crm.route("/programs/<int:program_id>/materials/<int:material_id>/delete", methods=["POST"])
@program_area_required
def program_material_delete(program_id, material_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM program_materials WHERE id = ?", (material_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM program_materials WHERE id = ?", (material_id,))
        conn.commit()
        flash("Material deleted.")
    conn.close()
    return redirect(url_for(".program_materials", program_id=program_id))


# ---------- music / playlists ----------

@crm.route("/programs/<int:program_id>/playlists")
@program_area_required
def program_playlists(program_id):
    conn = get_db()
    program = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    playlists = conn.execute(
        "SELECT * FROM program_playlists WHERE program_id = ? ORDER BY id", (program_id,)
    ).fetchall()
    songs_by_playlist = {}
    for pl in playlists:
        songs_by_playlist[pl["id"]] = conn.execute(
            "SELECT * FROM playlist_songs WHERE playlist_id = ? ORDER BY position, id", (pl["id"],)
        ).fetchall()
    conn.close()
    by_day = {}
    for pl in playlists:
        label = pl["day_label"] or "General"
        by_day.setdefault(label, []).append(pl)
    ordered_days = [d for d in DAY_LABEL_ORDER if d in by_day]
    ordered_days += [d for d in by_day if d not in DAY_LABEL_ORDER]
    return render_template(
        "program_playlists.html", program=program, by_day=by_day, ordered_days=ordered_days,
        day_label_choices=DAY_LABEL_ORDER, songs_by_playlist=songs_by_playlist,
    )


@crm.route("/programs/<int:program_id>/playlists/new", methods=["POST"])
@program_area_required
def program_playlist_new(program_id):
    category = request.form.get("category", "").strip()
    day_label = request.form.get("day_label") or None
    if category:
        conn = get_db()
        conn.execute(
            "INSERT INTO program_playlists (program_id, day_label, category) VALUES (?, ?, ?)",
            (program_id, day_label, category),
        )
        conn.commit()
        conn.close()
        flash("Playlist created.")
    return redirect(url_for(".program_playlists", program_id=program_id))


@crm.route("/programs/<int:program_id>/playlists/<int:playlist_id>/delete", methods=["POST"])
@program_area_required
def program_playlist_delete(program_id, playlist_id):
    conn = get_db()
    conn.execute("DELETE FROM playlist_songs WHERE playlist_id = ?", (playlist_id,))
    conn.execute("DELETE FROM program_playlists WHERE id = ?", (playlist_id,))
    conn.commit()
    conn.close()
    flash("Playlist deleted.")
    return redirect(url_for(".program_playlists", program_id=program_id))


@crm.route("/programs/<int:program_id>/playlists/<int:playlist_id>/songs/new", methods=["POST"])
@program_area_required
def playlist_song_new(program_id, playlist_id):
    title = request.form.get("title", "").strip()
    if title:
        conn = get_db()
        max_pos = conn.execute(
            "SELECT MAX(position) m FROM playlist_songs WHERE playlist_id = ?", (playlist_id,)
        ).fetchone()["m"]
        next_pos = (max_pos or 0) + 1
        conn.execute(
            """INSERT INTO playlist_songs (playlist_id, position, title, artist, album, duration, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                playlist_id, next_pos, title,
                request.form.get("artist", "").strip() or None,
                request.form.get("album", "").strip() or None,
                request.form.get("duration", "").strip() or None,
                request.form.get("notes", "").strip() or None,
            ),
        )
        conn.commit()
        conn.close()
    return redirect(url_for(".program_playlists", program_id=program_id))


@crm.route("/programs/<int:program_id>/playlists/<int:playlist_id>/songs/<int:song_id>/delete", methods=["POST"])
@program_area_required
def playlist_song_delete(program_id, playlist_id, song_id):
    conn = get_db()
    conn.execute("DELETE FROM playlist_songs WHERE id = ?", (song_id,))
    conn.commit()
    conn.close()
    return redirect(url_for(".program_playlists", program_id=program_id))


@crm.route("/programs/<int:program_id>/playlists/<int:playlist_id>/songs/<int:song_id>/move", methods=["POST"])
@program_area_required
def playlist_song_move(program_id, playlist_id, song_id):
    direction = request.form.get("direction")
    conn = get_db()
    songs = conn.execute(
        "SELECT id, position FROM playlist_songs WHERE playlist_id = ? ORDER BY position, id", (playlist_id,)
    ).fetchall()
    ids = [s["id"] for s in songs]
    if song_id in ids:
        idx = ids.index(song_id)
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(ids):
            a, b = songs[idx], songs[swap_idx]
            conn.execute("UPDATE playlist_songs SET position = ? WHERE id = ?", (b["position"], a["id"]))
            conn.execute("UPDATE playlist_songs SET position = ? WHERE id = ?", (a["position"], b["id"]))
            conn.commit()
    conn.close()
    return redirect(url_for(".program_playlists", program_id=program_id))


# ---------- Authorized Users hub (who can see each confidential area) ----------

@crm.route("/authorized-users")
@leadership_required
def authorized_users_hub():
    conn = get_db()
    existing_programs = {
        row["code"]: row["name"] for row in conn.execute("SELECT code, name FROM programs").fetchall()
    }
    conn.close()
    areas = list(FIXED_ACCESS_AREAS)
    for code in RESERVED_PROGRAM_AREA_CODES:
        label = code if code not in existing_programs else f"{code} — {existing_programs[code]}"
        areas.append((code, label))
    return render_template("authorized_users_hub.html", areas=areas)


@crm.route("/authorized-users/<area_key>")
@leadership_required
def authorized_users_area(area_key):
    area_label = dict(FIXED_ACCESS_AREAS).get(area_key, area_key)
    conn = get_db()
    grants = conn.execute(
        """SELECT ag.id AS grant_id, s.id AS staff_id, s.name, s.email, s.role
           FROM access_grants ag JOIN staff s ON s.id = ag.staff_id
           WHERE ag.area_key = ? ORDER BY ag.id""",
        (area_key,),
    ).fetchall()
    granted_staff_ids = [g["staff_id"] for g in grants]
    available_staff = conn.execute("SELECT * FROM staff WHERE active = 1 ORDER BY name").fetchall()
    available_staff = [s for s in available_staff if s["id"] not in granted_staff_ids]
    granted_emails = {g["email"].lower() for g in grants if g["email"]}
    available_contacts = conn.execute(
        f"""SELECT id, {FULL_NAME_SQL} AS full_name, email FROM contacts
           WHERE email IS NOT NULL AND email != '' ORDER BY last_name, first_name"""
    ).fetchall()
    available_contacts = [c for c in available_contacts if c["email"].lower() not in granted_emails]
    conn.close()
    slots = list(grants) + [None] * (ACCESS_AREA_MAX_GRANTS - len(grants))
    return render_template(
        "authorized_users_area.html", area_key=area_key, area_label=area_label,
        slots=slots, available_staff=available_staff, available_contacts=available_contacts,
        at_max=len(grants) >= ACCESS_AREA_MAX_GRANTS,
    )


@crm.route("/authorized-users/<area_key>/assign", methods=["POST"])
@leadership_required
def authorized_users_assign(area_key):
    """Authorizes someone for this area by name + email. If that email
    doesn't match an existing Team account, one is created on the spot
    (with a generated temporary password shown to Leadership once) --
    Kent doesn't have to visit the Team page first."""
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) c FROM access_grants WHERE area_key = ?", (area_key,)
    ).fetchone()["c"]
    if count >= ACCESS_AREA_MAX_GRANTS:
        flash(f"This area already has {ACCESS_AREA_MAX_GRANTS} authorized users -- remove one first.")
        conn.close()
        return redirect(url_for(".authorized_users_area", area_key=area_key))

    existing_staff_id = request.form.get("existing_staff_id")
    existing_contact_id = request.form.get("existing_contact_id")
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    already_flashed = False

    if existing_contact_id:
        contact = conn.execute(
            f"SELECT {FULL_NAME_SQL} AS full_name, email FROM contacts WHERE id = ?", (existing_contact_id,)
        ).fetchone()
        if not contact or not contact["email"]:
            flash("Couldn't find that contact's email.")
            conn.close()
            return redirect(url_for(".authorized_users_area", area_key=area_key))
        name, email = contact["full_name"], contact["email"].strip().lower()

    if existing_staff_id:
        staff_id = existing_staff_id
    elif email:
        staff = conn.execute("SELECT * FROM staff WHERE email = ?", (email,)).fetchone()
        if staff:
            staff_id = staff["id"]
            if not staff["active"]:
                conn.execute("UPDATE staff SET active = 1 WHERE id = ?", (staff_id,))
                conn.commit()
                flash(f"{staff['name']}'s account was deactivated -- reactivated it, and granted access.")
                already_flashed = True
        else:
            if not name:
                flash("Enter a name along with the email to create their login.")
                conn.close()
                return redirect(url_for(".authorized_users_area", area_key=area_key))
            temp_password = secrets.token_urlsafe(9)
            cur = conn.execute(
                "INSERT INTO staff (name, email, password_hash, role, active) VALUES (?, ?, ?, 'Team', 1)",
                (name, email, generate_password_hash(temp_password)),
            )
            conn.commit()
            staff_id = cur.lastrowid
            flash(
                f"Created a Team login for {name} and authorized them here. "
                f"Give them their email ({email}) and this temporary password: {temp_password}"
            )
            already_flashed = True
    else:
        flash("Enter a name and email, or choose an existing account.")
        conn.close()
        return redirect(url_for(".authorized_users_area", area_key=area_key))

    existing_grant = conn.execute(
        "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?", (area_key, staff_id)
    ).fetchone()
    if not existing_grant:
        conn.execute(
            "INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (area_key, staff_id)
        )
        conn.commit()
        if not already_flashed:
            flash("Access granted.")
    elif already_flashed:
        pass  # account was just created/reactivated but somehow already had this exact grant -- unusual, nothing more to say
    conn.close()
    return redirect(url_for(".authorized_users_area", area_key=area_key))


@crm.route("/authorized-users/<area_key>/<int:grant_id>/remove", methods=["POST"])
@leadership_required
def authorized_users_remove(area_key, grant_id):
    conn = get_db()
    conn.execute("DELETE FROM access_grants WHERE id = ?", (grant_id,))
    conn.commit()
    conn.close()
    flash("Access removed.")
    return redirect(url_for(".authorized_users_area", area_key=area_key))


@crm.route("/authorized-users/<area_key>/<int:staff_id>/reset-password", methods=["POST"])
@leadership_required
def authorized_users_reset_password(area_key, staff_id):
    """Generates a fresh temporary password for this Team account and
    shows it once. The CRM never stores a readable password -- only a
    one-way hash -- so this is the way to see/hand out a working password
    after the account's first creation."""
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff:
        temp_password = secrets.token_urlsafe(9)
        conn.execute(
            "UPDATE staff SET password_hash = ? WHERE id = ?",
            (generate_password_hash(temp_password), staff_id),
        )
        conn.commit()
        flash(f"New password for {staff['name']} ({staff['email']}): {temp_password}")
    conn.close()
    return redirect(url_for(".authorized_users_area", area_key=area_key))


@crm.route("/programs/<int:program_id>/cohorts/new", methods=["POST"])
@program_area_required
def cohort_new(program_id):
    conn = get_db()
    max_num = conn.execute(
        "SELECT COALESCE(MAX(session_number), 0) m FROM cohorts WHERE program_id = ?", (program_id,)
    ).fetchone()["m"]
    session_date = request.form["session_date"]
    conn.execute(
        "INSERT INTO cohorts (program_id, session_number, session_date) VALUES (?, ?, ?)",
        (program_id, max_num + 1, session_date),
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=new_id))


@crm.route("/cohorts/<int:cohort_id>")
@area_required("contacts")
def cohort_detail(cohort_id):
    conn = get_db()
    cohort = conn.execute(
        """SELECT c.*, p.code, p.name, p.id AS program_id FROM cohorts c
           JOIN programs p ON p.id = c.program_id WHERE c.id = ?""",
        (cohort_id,),
    ).fetchone()
    roster = conn.execute(
        f"""SELECT e.*, {full_name_sql('ct')} AS full_name,
                   ct.id AS contact_id, sg.label AS group_label,
                   fr.id AS feedback_response_id,
                   {full_name_sql('eb')} AS enrolled_by_name
           FROM enrollments e
           JOIN contacts ct ON ct.id = e.contact_id
           LEFT JOIN contacts eb ON eb.id = e.enrolled_by_contact_id
           LEFT JOIN small_groups sg ON sg.id = e.small_group_id
           LEFT JOIN feedback_responses fr ON fr.enrollment_id = e.id
           WHERE e.cohort_id = ? ORDER BY ct.last_name, ct.first_name""",
        (cohort_id,),
    ).fetchall()
    small_groups = conn.execute("SELECT * FROM small_groups WHERE cohort_id = ? ORDER BY label", (cohort_id,)).fetchall()
    staffing = conn.execute(
        f"""SELECT cs.*, {full_name_sql('ct')} AS full_name
           FROM cohort_staffing cs
           JOIN contacts ct ON ct.id = cs.contact_id WHERE cs.cohort_id = ?""",
        (cohort_id,),
    ).fetchall()
    sg_staffing = conn.execute(
        f"""SELECT sgs.*, {full_name_sql('ct')} AS full_name,
                   sg.label
           FROM small_group_staffing sgs
           JOIN contacts ct ON ct.id = sgs.contact_id
           JOIN small_groups sg ON sg.id = sgs.small_group_id
           WHERE sg.cohort_id = ?""",
        (cohort_id,),
    ).fetchall()
    all_contacts = conn.execute(
        f"SELECT id, {FULL_NAME_SQL} AS full_name FROM contacts ORDER BY last_name, first_name"
    ).fetchall()
    enrolled_counts = {
        r["cid"]: r["n"]
        for r in conn.execute(
            """SELECT enrolled_by_contact_id AS cid, COUNT(DISTINCT contact_id) n
               FROM enrollments
               WHERE enrolled_by_contact_id IS NOT NULL AND contact_id != enrolled_by_contact_id
               GROUP BY enrolled_by_contact_id"""
        ).fetchall()
    }
    conn.close()
    return render_template(
        "cohort_detail.html",
        cohort=cohort,
        roster=roster,
        small_groups=small_groups,
        staffing=staffing,
        sg_staffing=sg_staffing,
        all_contacts=all_contacts,
        enrolled_counts=enrolled_counts,
        TA_MIN=TA_MIN_ENROLLED,
        CAPTAIN_MIN=CAPTAIN_MIN_ENROLLED,
    )


@crm.route("/cohorts/<int:cohort_id>/enroll", methods=["POST"])
@area_required("contacts")
def enroll(cohort_id):
    conn = get_db()
    cohort = conn.execute("SELECT * FROM cohorts WHERE id = ?", (cohort_id,)).fetchone()

    contact_id = request.form.get("contact_id")
    if not contact_id:
        # quick-add a new contact inline (single "full name" box, split into first/last)
        name = request.form["new_contact_name"].strip()
        if " " in name:
            first, last = name.split(" ", 1)
        else:
            first, last = name, None
        conn.execute(
            "INSERT INTO contacts (first_name, last_name, status) VALUES (?, ?, 'Registered')", (first, last)
        )
        contact_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    else:
        contact_id = int(contact_id)

    ok, msg = prerequisite_status(conn, contact_id, cohort["program_id"])
    if not ok:
        flash(f"Not enrolled: prerequisite not met — {msg}")
        conn.close()
        return redirect(url_for(".cohort_detail", cohort_id=cohort_id))

    brought_by, pick_error = parse_contact_pick(conn, request.form.get("enrolled_by_pick"))
    if pick_error:
        flash(f"Not enrolled: {pick_error}")
        conn.close()
        return redirect(url_for(".cohort_detail", cohort_id=cohort_id))
    if brought_by == contact_id:
        brought_by = None  # nobody brings themselves in

    try:
        conn.execute(
            "INSERT INTO enrollments (contact_id, cohort_id, enrolled_by_contact_id) VALUES (?, ?, ?)",
            (contact_id, cohort_id, brought_by),
        )
        conn.commit()
        flash("Enrolled.")
    except Exception:
        flash("This contact is already enrolled in this session.")
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/enrollments/<int:enrollment_id>/update", methods=["POST"])
@area_required("contacts")
def enrollment_update(enrollment_id):
    conn = get_db()
    enr = conn.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
    fields = {}
    if "questionnaire_done" in request.form:
        fields["questionnaire_completed_at"] = date.today().isoformat()
    if "payment_done" in request.form:
        fields["payment_completed_at"] = date.today().isoformat()
    if "attended" in request.form:
        fields["attended"] = 1
    if request.form.get("small_group_id"):
        fields["small_group_id"] = request.form["small_group_id"]
    if "set_enrolled_by" in request.form:
        brought_by, pick_error = parse_contact_pick(conn, request.form.get("enrolled_by_pick"))
        if pick_error:
            flash(pick_error)
        elif brought_by == enr["contact_id"]:
            flash("Someone can't be recorded as bringing themselves in.")
        else:
            fields["enrolled_by_contact_id"] = brought_by
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE enrollments SET {set_clause} WHERE id = ?", (*fields.values(), enrollment_id))
        conn.commit()
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=enr["cohort_id"]))


@crm.route("/enrollments/<int:enrollment_id>/feedback-link/new", methods=["POST"])
@area_required("contacts")
def feedback_link_new(enrollment_id):
    conn = get_db()
    enr = conn.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
    if not enr["feedback_token"]:
        conn.execute(
            "UPDATE enrollments SET feedback_token = ? WHERE id = ?",
            (secrets.token_urlsafe(24), enrollment_id),
        )
        conn.commit()
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=enr["cohort_id"]))


# Fields every program's profile asks (Bluesky 1/2/3 and Squeeze).
COMMON_PROFILE_FIELDS = ["first_time_attending", "training_goals", "other_info"]

# Fields that only make sense for a couple attending together — Squeeze only.
COUPLES_ONLY_FIELDS = [
    "relationship_length", "how_met", "childhood_patterns", "emotional_milestone",
    "relationship_strengths", "relationship_weaknesses", "stress_points",
    "song_artist", "song_feelings",
]

# Full column order, kept for reference / SELECT * consumers.
RELATIONSHIP_PROFILE_FIELDS = [
    "first_time_attending", "training_goals", "relationship_length", "how_met",
    "childhood_patterns", "emotional_milestone", "relationship_strengths",
    "relationship_weaknesses", "stress_points", "song_artist", "song_feelings", "other_info",
]


@crm.route("/enrollments/<int:enrollment_id>/relationship-profile", methods=["GET", "POST"])
@area_required("contacts")
def relationship_profile(enrollment_id):
    conn = get_db()
    enr = conn.execute(
        f"""SELECT e.*, {full_name_sql('ct')} AS full_name, ct.id AS contact_id,
                   p.code, p.name AS program_name, c.session_number, c.session_date
           FROM enrollments e
           JOIN contacts ct ON ct.id = e.contact_id
           JOIN cohorts c ON c.id = e.cohort_id
           JOIN programs p ON p.id = c.program_id
           WHERE e.id = ?""",
        (enrollment_id,),
    ).fetchone()
    if enr is None:
        conn.close()
        flash("Enrollment not found.")
        return redirect(url_for(".contacts_list"))

    is_couples = enr["code"] == "D4"
    active_fields = RELATIONSHIP_PROFILE_FIELDS if is_couples else COMMON_PROFILE_FIELDS

    if request.method == "POST":
        values = [request.form.get(f, "").strip() or None for f in active_fields]
        existing = conn.execute(
            "SELECT id FROM training_profiles WHERE enrollment_id = ?", (enrollment_id,)
        ).fetchone()
        if existing:
            set_clause = ", ".join(f"{f} = ?" for f in active_fields)
            conn.execute(
                f"UPDATE training_profiles SET {set_clause}, updated_at = datetime('now') "
                f"WHERE enrollment_id = ?",
                (*values, enrollment_id),
            )
        else:
            cols = ", ".join(active_fields)
            placeholders = ", ".join("?" for _ in active_fields)
            conn.execute(
                f"INSERT INTO training_profiles (enrollment_id, {cols}) VALUES (?, {placeholders})",
                (enrollment_id, *values),
            )
        conn.commit()
        conn.close()
        flash("Relationship profile saved.")
        return redirect(url_for(".contact_detail", contact_id=enr["contact_id"]))

    profile = conn.execute(
        "SELECT * FROM training_profiles WHERE enrollment_id = ?", (enrollment_id,)
    ).fetchone()
    conn.close()
    return render_template(
        "relationship_profile.html", enrollment=enr, profile=profile, is_couples=is_couples
    )


@crm.route("/cohorts/<int:cohort_id>/small-groups/new", methods=["POST"])
@area_required("contacts")
def small_group_new(cohort_id):
    conn = get_db()
    conn.execute("INSERT INTO small_groups (cohort_id, label) VALUES (?, ?)", (cohort_id, request.form["label"]))
    conn.commit()
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/cohorts/<int:cohort_id>/staffing/new", methods=["POST"])
@area_required("contacts")
def staffing_new(cohort_id):
    conn = get_db()
    conn.execute(
        "INSERT INTO cohort_staffing (cohort_id, contact_id, role, duty) VALUES (?, ?, ?, ?)",
        (cohort_id, request.form["contact_id"], request.form["role"], request.form.get("duty") or None),
    )
    conn.commit()
    warning = requirement_warning(conn, int(request.form["contact_id"]), request.form["role"], request.form.get("duty") or None)
    if warning:
        flash(warning)
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/small-groups/<int:small_group_id>/staffing/new", methods=["POST"])
@area_required("contacts")
def sg_staffing_new(small_group_id):
    conn = get_db()
    sg = conn.execute("SELECT * FROM small_groups WHERE id = ?", (small_group_id,)).fetchone()
    conn.execute(
        "INSERT INTO small_group_staffing (small_group_id, contact_id, role, duty) VALUES (?, ?, 'TA', ?)",
        (small_group_id, request.form["contact_id"], request.form.get("duty") or None),
    )
    conn.commit()
    warning = requirement_warning(conn, int(request.form["contact_id"]), "TA", request.form.get("duty") or None)
    if warning:
        flash(warning)
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=sg["cohort_id"]))


# ---------- leadership pipeline ----------

@crm.route("/leadership-pipeline")
@area_required("contacts")
def leadership_pipeline():
    """Who has brought people into the training, and who is serving as a TA or
    Team Captain -- with a plain check against the requirement (TA: brought in
    at least TA_MIN_ENROLLED people; Team Captain: at least CAPTAIN_MIN_ENROLLED)."""
    conn = get_db()
    people = {}

    for r in conn.execute(
        """SELECT enrolled_by_contact_id AS cid,
                  COUNT(DISTINCT contact_id) AS n,
                  COUNT(DISTINCT CASE WHEN attended = 1 THEN contact_id END) AS attended
           FROM enrollments
           WHERE enrolled_by_contact_id IS NOT NULL AND contact_id != enrolled_by_contact_id
           GROUP BY enrolled_by_contact_id"""
    ).fetchall():
        people[r["cid"]] = {"enrolled": r["n"], "attended": r["attended"], "ta_times": 0, "captain_times": 0}

    def entry(cid):
        return people.setdefault(cid, {"enrolled": 0, "attended": 0, "ta_times": 0, "captain_times": 0})

    for r in conn.execute(
        """SELECT contact_id AS cid, COUNT(*) AS n,
                  SUM(CASE WHEN duty = 'Team Captain' THEN 1 ELSE 0 END) AS caps
           FROM small_group_staffing GROUP BY contact_id"""
    ).fetchall():
        e = entry(r["cid"])
        e["ta_times"] += r["n"]
        e["captain_times"] += r["caps"] or 0
    for r in conn.execute(
        "SELECT contact_id AS cid, COUNT(*) AS n FROM cohort_staffing WHERE role = 'TA' GROUP BY contact_id"
    ).fetchall():
        entry(r["cid"])["ta_times"] += r["n"]

    names = {
        r["id"]: r["n"]
        for r in conn.execute(f"SELECT id, {FULL_NAME_SQL} AS n FROM contacts").fetchall()
    }
    conn.close()

    rows = []
    for cid, e in people.items():
        e["id"] = cid
        e["name"] = names.get(cid, "(unknown)")
        e["ta_ok"] = e["enrolled"] >= TA_MIN_ENROLLED
        e["captain_ok"] = e["enrolled"] >= CAPTAIN_MIN_ENROLLED
        e["needs_attention"] = (e["ta_times"] and not e["ta_ok"]) or (e["captain_times"] and not e["captain_ok"])
        rows.append(e)
    rows.sort(key=lambda e: (-e["enrolled"], e["name"].lower()))
    return render_template(
        "leadership_pipeline.html", rows=rows, TA_MIN=TA_MIN_ENROLLED, CAPTAIN_MIN=CAPTAIN_MIN_ENROLLED
    )


# ---------- donations ----------

@crm.route("/donations")
@area_required("donations")
def donations_list():
    conn = get_db()
    rows = conn.execute(
        f"""SELECT d.*, {full_name_sql('c')} AS full_name FROM donations d
           JOIN contacts c ON c.id = d.contact_id
           ORDER BY d.donation_date DESC"""
    ).fetchall()
    total = conn.execute("SELECT COALESCE(SUM(amount),0) t FROM donations").fetchone()["t"]
    conn.close()
    return render_template("donations_list.html", donations=rows, total=total)


# ---------- feedback (leadership only -- not linked from the main nav) ----------

@crm.route("/feedback")
@leadership_required
def feedback_list():
    conn = get_db()
    rows = conn.execute(
        f"""SELECT fr.*, e.id AS enrollment_id, e.small_group_id, e.cohort_id,
                   {full_name_sql('ct')} AS full_name,
                   p.code, p.name AS program_name, c.session_number, c.session_date
           FROM feedback_responses fr
           JOIN enrollments e ON e.id = fr.enrollment_id
           JOIN contacts ct ON ct.id = e.contact_id
           JOIN cohorts c ON c.id = e.cohort_id
           JOIN programs p ON p.id = c.program_id
           ORDER BY fr.submitted_at DESC"""
    ).fetchall()

    responses = []
    for r in rows:
        facilitators = conn.execute(
            f"""SELECT {full_name_sql('ct')} AS full_name FROM cohort_staffing cs
               JOIN contacts ct ON ct.id = cs.contact_id
               WHERE cs.cohort_id = ? AND cs.role = 'Facilitator'""",
            (r["cohort_id"],),
        ).fetchall()
        tas = []
        if r["small_group_id"]:
            tas = conn.execute(
                f"""SELECT {full_name_sql('ct')} AS full_name FROM small_group_staffing sgs
                   JOIN contacts ct ON ct.id = sgs.contact_id
                   WHERE sgs.small_group_id = ?""",
                (r["small_group_id"],),
            ).fetchall()
        responses.append({
            "row": r,
            "facilitators": ", ".join(f["full_name"] for f in facilitators) or "—",
            "tas": ", ".join(t["full_name"] for t in tas) or "—",
        })
    conn.close()
    return render_template("feedback_list.html", responses=responses)


# ---------- public marketing site (blueskyusa.net root) ----------

@app.route("/")
def public_home():
    return render_template("public_home.html")


@app.route("/our-story")
def public_our_story():
    return render_template("public_our_story.html")


@app.route("/about-the-program")
def public_about_program():
    return render_template("public_about_program.html")


@app.route("/programs")
def public_programs():
    conn = get_db()
    programs = conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
    conn.close()
    return render_template("public_programs.html", programs=programs)


@app.route("/register", methods=["GET", "POST"])
def public_register():
    conn = get_db()
    if request.method == "POST":
        program_code = request.form.get("program_code") or ""
        message = request.form.get("message", "").strip()
        note_parts = []
        if program_code:
            note_parts.append(f"Interested in: {program_code}")
        if message:
            note_parts.append(message)
        note_parts.append("(Submitted via website registration interest form)")
        conn.execute(
            """INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, marketing_source, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                request.form["first_name"].strip(),
                request.form.get("last_name", "").strip() or None,
                request.form.get("email", "").strip() or None,
                request.form.get("phone", "").strip() or None,
                "\n".join(note_parts),
                "Website",
                "Interested Party",
            ),
        )
        conn.commit()
        conn.close()
        flash("Thanks! We've got your info and someone from our team will be in touch soon.")
        return redirect(url_for("public_register"))

    programs = conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
    upcoming = conn.execute(
        """SELECT c.*, p.code, p.name FROM cohorts c
           JOIN programs p ON p.id = c.program_id
           WHERE c.session_date >= date('now')
           ORDER BY c.session_date ASC LIMIT 20"""
    ).fetchall()
    conn.close()
    return render_template("public_register.html", programs=programs, upcoming=upcoming)


@app.route("/contact", methods=["GET", "POST"])
def public_contact():
    if request.method == "POST":
        conn = get_db()
        message = request.form.get("message", "").strip()
        note_parts = ["Contact form inquiry submitted via website"]
        if message:
            note_parts.append(message)
        conn.execute(
            """INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, marketing_source, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                request.form["first_name"].strip(),
                request.form.get("last_name", "").strip() or None,
                request.form.get("email", "").strip() or None,
                request.form.get("phone", "").strip() or None,
                "\n".join(note_parts),
                "Website",
                "Interested Party",
            ),
        )
        conn.commit()
        conn.close()
        flash("Thanks for reaching out! Someone from our team will get back to you soon.")
        return redirect(url_for("public_contact"))
    return render_template("public_contact.html")


@app.route("/discovery", methods=["GET", "POST"])
def public_discovery():
    """Standalone invitation page for past Discovery volunteers/TAs to add
    themselves to the contact list. Deliberately not built on base.html --
    no site nav, no footer, no links to Programs/Donate/CRM login -- just
    the invitation and the sign-up form, so it can be shared as its own
    focused link."""
    if request.method == "POST":
        conn = get_db()
        message = request.form.get("message", "").strip()

        types = request.form.getlist("volunteer_type[]")
        months = request.form.getlist("volunteer_month[]")
        years_in = request.form.getlist("volunteer_year[]")

        # "Interested Party" is one of the choices in the role dropdown, but
        # it isn't a real past role -- pull it out on its own so it never
        # ends up listed alongside Facilitator/TA/etc. in the notes, and so
        # it doesn't count toward making someone a "Volunteer" below.
        role_lines = []
        selected_interested_party = False
        for i, role_type in enumerate(types):
            role_type = (role_type or "").strip()
            if not role_type:
                continue
            if role_type == "Interested Party":
                selected_interested_party = True
                continue
            month = months[i].strip() if i < len(months) else ""
            year = years_in[i].strip() if i < len(years_in) else ""
            when = " ".join(part for part in [month, year] if part)
            role_lines.append(f"{role_type} ({when})" if when else role_type)

        note_parts = ["Dallas Discovery Volunteer sign-up submitted via website"]
        if role_lines:
            note_parts.append("Roles: " + "; ".join(role_lines))
        elif selected_interested_party:
            note_parts.append("Selected \"Interested Party\" -- no past role with Discovery")
        if message:
            note_parts.append(message)

        first_name = request.form["first_name"].strip()
        last_name = request.form.get("last_name", "").strip() or None
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        wants_longform = bool(request.form.get("wants_longform"))
        new_notes = "\n".join(note_parts)

        # A real past role (Facilitator, TA, Sound Tech, etc.) makes them a
        # Volunteer; checking "Interested Party" (or leaving both blank)
        # makes them an Interested Party -- so the two groups can be emailed
        # separately later, instead of every /discovery signup landing in
        # the same bucket regardless of history.
        new_status = "Volunteer" if role_lines else "Interested Party"

        # Many of the people filling this out are already in the CRM from
        # past Discovery involvement -- match them so their existing record
        # (photo, history, etc.) gets updated instead of a duplicate contact
        # being created. Matching requires BOTH the name (first + last,
        # case-insensitive) AND the email to line up -- the email just has
        # to match ANY of their three known addresses (email/email_2/
        # email_3), since someone may sign up again under an older or
        # different personal address than the one on file. A name match
        # alone, or an email match alone, is treated as a different person
        # and creates a new contact.
        existing = None
        submitted_email = (email or "").strip().lower()
        submitted_first = first_name.strip().lower()
        submitted_last = (last_name or "").strip().lower()
        if submitted_email:
            candidates = conn.execute(
                """SELECT id, notes, first_name, last_name FROM contacts
                   WHERE LOWER(email) = ? OR LOWER(email_2) = ? OR LOWER(email_3) = ?
                   ORDER BY id""",
                (submitted_email, submitted_email, submitted_email),
            ).fetchall()
            for candidate in candidates:
                if (
                    (candidate["first_name"] or "").strip().lower() == submitted_first
                    and (candidate["last_name"] or "").strip().lower() == submitted_last
                ):
                    existing = candidate
                    break

        if existing:
            new_id = existing["id"]
            combined_notes = (existing["notes"] + "\n\n" + new_notes) if existing["notes"] else new_notes
            conn.execute(
                """UPDATE contacts
                   SET first_name = ?, last_name = ?, cell_phone = ?, notes = ?,
                       marketing_source = ?, status = ?
                   WHERE id = ?""",
                (
                    first_name,
                    last_name,
                    phone,
                    combined_notes,
                    "Dallas Discovery Volunteer",
                    new_status,
                    new_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, marketing_source, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    first_name,
                    last_name,
                    email,
                    phone,
                    new_notes,
                    "Dallas Discovery Volunteer",
                    new_status,
                ),
            )
            new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        conn.commit()

        # Both the blocks below reuse one profile_token per contact rather
        # than each generating its own -- the contact can only have one
        # profile_token at a time, so if both emails fired (e.g. someone
        # selects "Interested Party" but also checks "Volunteer"), a second
        # token would silently break the link already sent in the first
        # email. existing_token is reused if present; otherwise we generate
        # exactly one for this whole request.
        existing_token = conn.execute(
            "SELECT profile_token FROM contacts WHERE id = ?", (new_id,)
        ).fetchone()["profile_token"]

        # They checked "Volunteer" -- send them the long-form link right away
        # so they can share more detail for the volunteer teams. This is a
        # best-effort send: if it fails for any reason, their short-form
        # info is already saved either way, and Kent can generate/send the
        # link manually later from their contact page.
        emailed = False  # set True only when a follow-up email actually went out
        if wants_longform and email:
            token = existing_token or secrets.token_urlsafe(24)
            if token != existing_token:
                conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (token, new_id))
                conn.commit()
                existing_token = token
            longform_url = url_for("public_complete_profile", token=token, _external=True)
            html_content, text_content = longform_followup_content(first_name, longform_url)
            try:
                send_email(
                    to_email=email,
                    to_name=first_name,
                    subject=LONGFORM_FOLLOWUP_SUBJECT,
                    html_content=html_content,
                    text_content=text_content,
                )
                emailed = True
            except EmailSendError as e:
                print(f"EmailSendError sending long-form link to {email}: {e}", flush=True)

        # They picked "Interested Party" rather than a past role -- send a
        # short welcome email thanking them and sharing a bit of our
        # personal story and the heart behind the program. Separate from
        # the volunteer long-form follow-up above, and only sent when they
        # have no real past role (someone who's both a past volunteer and
        # curious just gets treated as a Volunteer).
        if selected_interested_party and not role_lines and email:
            ip_token = existing_token or secrets.token_urlsafe(24)
            if ip_token != existing_token:
                conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (ip_token, new_id))
                conn.commit()
                existing_token = ip_token
            connect_url = url_for("public_connect_request", token=ip_token, _external=True)
            html_content, text_content = interested_party_welcome_content(first_name, connect_url)
            try:
                send_email(
                    to_email=email,
                    to_name=first_name,
                    subject=INTERESTED_PARTY_WELCOME_SUBJECT,
                    html_content=html_content,
                    text_content=text_content,
                )
                emailed = True
            except EmailSendError as e:
                print(f"EmailSendError sending Interested Party welcome to {email}: {e}", flush=True)

        conn.close()
        if emailed:
            return redirect(url_for("public_discovery", submitted="1", emailed="1"))
        return redirect(url_for("public_discovery", submitted="1"))
    submitted = request.args.get("submitted") == "1"
    emailed_flag = submitted and request.args.get("emailed") == "1"
    current_year = date.today().year
    # Goes back to 1990 -- the program had been running for a while before
    # Kent and Pamela got involved in 1996, so past volunteers from those
    # earlier years need to be able to pick their actual year too.
    years = list(range(current_year, 1989, -1))
    return render_template("public_discovery.html", submitted=submitted, emailed=emailed_flag, years=years)


@app.route("/donate", methods=["GET", "POST"])
def public_donate():
    if request.method == "POST":
        conn = get_db()
        amount_range = request.form.get("amount_range") or ""
        message = request.form.get("message", "").strip()
        note_parts = ["Donation interest submitted via website"]
        if amount_range:
            note_parts.append(f"Considering: {amount_range}")
        if message:
            note_parts.append(message)
        conn.execute(
            """INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, marketing_source, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                request.form["first_name"].strip(),
                request.form.get("last_name", "").strip() or None,
                request.form.get("email", "").strip() or None,
                request.form.get("phone", "").strip() or None,
                "\n".join(note_parts),
                "Website",
                "Interested Party",
            ),
        )
        conn.commit()
        conn.close()
        flash("Thank you! We'll be in touch about how to make your gift.")
        return redirect(url_for("public_donate"))
    return render_template("public_donate.html")


# Every column of feedback_responses that holds a 1-5 rating, and every column
# that holds a free-text answer -- used together by the submit route below so
# the INSERT and the form's field names always stay in sync with schema.sql.
FEEDBACK_RATING_FIELDS = [
    "facilitator_clear", "facilitator_safe", "facilitator_responsive",
    "ta_safe", "ta_focused", "ta_individual_attention",
    "practical_tools", "emotionally_safe", "would_recommend", "future_training_likelihood",
]
FEEDBACK_TEXT_FIELDS = [
    "facilitator_did_well", "facilitator_could_improve",
    "ta_did_well", "ta_could_improve",
    "uncomfortable_notes", "other_comments",
]


def clean_rating(value):
    value = (value or "").strip()
    if value not in {"1", "2", "3", "4", "5"}:
        return None
    return int(value)


@app.route("/feedback/<token>", methods=["GET", "POST"])
def public_feedback(token):
    conn = get_db()
    enr = conn.execute(
        f"""SELECT e.id, e.cohort_id, {full_name_sql('ct')} AS full_name,
                   p.code, p.name AS program_name, c.session_number, c.session_date
           FROM enrollments e
           JOIN contacts ct ON ct.id = e.contact_id
           JOIN cohorts c ON c.id = e.cohort_id
           JOIN programs p ON p.id = c.program_id
           WHERE e.feedback_token = ?""",
        (token,),
    ).fetchone()
    if enr is None:
        conn.close()
        return render_template("public_feedback.html", enr=None, already_submitted=False), 404

    already = conn.execute(
        "SELECT id FROM feedback_responses WHERE enrollment_id = ?", (enr["id"],)
    ).fetchone()

    if request.method == "POST":
        if already:
            conn.close()
            flash("This feedback form has already been submitted -- thank you again!")
            return redirect(url_for("public_feedback", token=token))
        rating_values = [clean_rating(request.form.get(f)) for f in FEEDBACK_RATING_FIELDS]
        text_values = [request.form.get(f, "").strip() or None for f in FEEDBACK_TEXT_FIELDS]
        cols = "enrollment_id, " + ", ".join(FEEDBACK_RATING_FIELDS + FEEDBACK_TEXT_FIELDS)
        placeholders = ", ".join(["?"] * (1 + len(FEEDBACK_RATING_FIELDS) + len(FEEDBACK_TEXT_FIELDS)))
        conn.execute(
            f"INSERT INTO feedback_responses ({cols}) VALUES ({placeholders})",
            (enr["id"], *rating_values, *text_values),
        )
        conn.commit()
        conn.close()
        flash("Thank you for your honest feedback -- it genuinely helps us improve.")
        return redirect(url_for("public_feedback", token=token))

    conn.close()
    return render_template("public_feedback.html", enr=enr, already_submitted=bool(already))


@crm.context_processor
def inject_area_access():
    if not session.get("staff_id"):
        return {"has_contacts_access": False, "has_program_access": False, "has_donations_access": False}
    return {
        "has_contacts_access": _has_area_access("contacts"),
        "has_program_access": _has_any_program_access(),
        "has_donations_access": _has_area_access("donations"),
    }


app.register_blueprint(crm)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5050, debug=True)
