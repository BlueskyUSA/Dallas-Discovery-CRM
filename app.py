from dotenv import load_dotenv
load_dotenv()  # loads DATABASE_URL from a local .env file, if present

from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, session, send_from_directory, abort, make_response, g
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
    SESSION_LETTERS, SESSION_LETTER_ORDER, SESSION_TRAINEE_LETTERS, session_letter_content,
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
    CONTRACT_REQUEST_SUBJECT,
    contract_request_content,
    completion_letter_content,
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
ALLOWED_MATERIAL_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "jpg", "jpeg", "png", "mp3", "m4a", "wav"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "wav"}
# The database server is small (256 MB of memory), and a file is held in memory several times over while it is
# saved, so a big upload can make the database drop its connection. Keep audio/video small; use links for big films.
MAX_AUDIO_BYTES = 8 * 1024 * 1024



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
    if ext in AUDIO_EXTENSIONS and len(data) > MAX_AUDIO_BYTES:
        flash(f"{file_storage.filename} is over 8 MB, so it was not uploaded.")
        return None
    if ext in AUDIO_EXTENSIONS:
        file_storage_type = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"}[ext]
        return data, file_storage_type
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
    if request.endpoint in ("crm.login", "crm.team_login", "crm.leadership_login", "crm.accounting_login", "crm.owner_login"):
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


# ---------- roles ----------
# staff.role holds one of three values: "Team" (shown as Volunteer Team in the
# welcome letter), "Leadership", and "Accounting" (shown as "Accounting & Legal":
# the accountant, bookkeeper and attorney). The Owner is a flag (is_owner) on a
# Leadership account, not a separate role value.
ROLE_LABELS = {"Team": "Team", "Leadership": "Leadership", "Accounting": "Accounting & Legal"}
ACCOUNTING_ROLE = "Accounting"
# The only areas an Accounting & Legal login can ever be given (the Owner picks
# which of these each person gets). Program materials and marketing are never offered.
ACCOUNTING_AREAS = ["accounting", "donations", "contracts", "contacts"]
# Accounting & Legal logins are view-only: any form submission is refused except
# signing in/out and changing their own password.
ACCOUNTING_WRITE_ALLOWED = ("crm.login", "crm.team_login", "crm.leadership_login", "crm.accounting_login", "crm.owner_login",
                            "crm.logout", "crm.change_password")


def role_label(role):
    return ROLE_LABELS.get(role, role)


@crm.before_request
def accounting_view_only():
    if session.get("staff_role") != ACCOUNTING_ROLE or session.get("is_owner"):
        return
    if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint in ACCOUNTING_WRITE_ALLOWED:
        return
    flash("Your Accounting & Legal login is view-only. Nothing was changed.")
    return redirect(url_for(".dashboard"))


def _accounting_target_blocked(staff_id):
    """Only the Owner may change an Accounting & Legal login (edit, deactivate,
    reactivate, link to a contact). True (after flashing why) if this account is
    one and the person asking is not the Owner."""
    if session.get("is_owner"):
        return False
    conn = get_db()
    row = conn.execute("SELECT role FROM staff WHERE id = ?", (staff_id,)).fetchone()
    conn.close()
    if row and row["role"] == ACCOUNTING_ROLE:
        flash("Only the Owner can change Accounting & Legal logins.")
        return True
    return False


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
    ("contracts", "Contracts & business plan"),
    ("session_team", "Session Team (on each training session page)"),
    ("trainee_roster", "Trainee Roster (on each training session page)"),
]
# Ticking either session box also gives Contacts automatically, since the session pages
# live under Contacts. These two (and the Contacts that comes with them) have no 4-person limit.
SESSION_VIEW_AREAS = ("session_team", "trainee_roster")
UNCAPPED_AREAS = set(SESSION_VIEW_AREAS)


def _can_see_session_part(part):
    """Whether this login sees the Session Team / Trainee Roster on a session page.
    Owner always. Otherwise it needs that box ticked -- except accounts that already had
    Contacts before these boxes existed (they have neither box) keep seeing both."""
    if session.get("is_owner"):
        return True
    conn = get_db()
    keys = {r["area_key"] for r in conn.execute(
        "SELECT area_key FROM access_grants WHERE staff_id = ?", (session.get("staff_id"),)).fetchall()}
    conn.close()
    if part in keys:
        return True
    return "contacts" in keys and not (keys & set(SESSION_VIEW_AREAS))


def _grant_contacts_for_session_areas(conn, staff_id):
    """Adds Contacts for this person if they hold a session box but not Contacts yet. True if added."""
    has = {r["area_key"] for r in conn.execute(
        "SELECT area_key FROM access_grants WHERE staff_id = ?", (staff_id,)).fetchall()}
    if (has & set(SESSION_VIEW_AREAS)) and "contacts" not in has:
        conn.execute("INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", ("contacts", staff_id))
        return True
    return False
# Reserved program-code slots shown in the hub even before that program
# exists yet (B1-B4 are today's real programs; B5/B6 are room to grow).
RESERVED_PROGRAM_AREA_CODES = ["D1", "D2", "D3", "D4", "D5", "D6"]

# Every program's training materials are filed under these four headings,
# and access is granted per heading per program: the access_grants area_key
# for "D1's Syllabus" is "D1:Syllabus". (Before this, one grant on "D1"
# opened all four; those older grants are split into four automatically --
# see _split_program_grants_by_heading.)
MATERIAL_CATEGORIES = ["Syllabus", "Music", "Handouts", "Other"]
PROGRAM_HEADING_AREA_KEYS = [f"{code}:{h}" for code in RESERVED_PROGRAM_AREA_CODES for h in MATERIAL_CATEGORIES]


def program_heading_key(program_code, heading):
    return f"{program_code}:{heading}"


def _area_labels_map(conn):
    """area_key -> human label, for every fixed area plus every program
    that actually exists today (e.g. 'B1' -> 'B1 — Bluesky 1')."""
    labels = dict(FIXED_ACCESS_AREAS)
    labels[ACCOUNTING_LOGIN_GRANT] = "Accounting & Legal login"
    for code in RESERVED_PROGRAM_AREA_CODES:
        for h in MATERIAL_CATEGORIES:
            labels[program_heading_key(code, h)] = f"{code}: {h}"
    for row in conn.execute("SELECT code, name FROM programs").fetchall():
        labels[row["code"]] = f"{row['code']} — {row['name']}"
        for h in MATERIAL_CATEGORIES:
            labels[program_heading_key(row["code"], h)] = f"{row['code']} — {row['name']}: {h}"
    return labels


def _all_areas(conn):
    """(area_key, label) for every fixed area plus all 6 program slots
    (B1-B6) -- the full set of things access can be granted for. A slot
    shows the program's real name once it exists, or just its code as a
    placeholder before then, so access can be set up ahead of time."""
    area_labels = _area_labels_map(conn)
    return list(FIXED_ACCESS_AREAS) + [
        (key, area_labels.get(key, key)) for key in PROGRAM_HEADING_AREA_KEYS
    ]


def _program_area_groups(conn):
    """[(program label, [(area_key, heading), ...]), ...] -- one group per
    program slot (D1-D6), for showing the four heading checkboxes together."""
    area_labels = _area_labels_map(conn)
    return [
        (area_labels.get(code, code), [(program_heading_key(code, h), h) for h in MATERIAL_CATEGORIES])
        for code in RESERVED_PROGRAM_AREA_CODES
    ]


def _allowed_headings(conn, program_code):
    """Which of Syllabus / Music / Handouts / Other the signed-in account may
    see for this program, in that order. The Owner sees all four."""
    if session.get("is_owner"):
        return list(MATERIAL_CATEGORIES)
    granted = {
        row["area_key"]
        for row in conn.execute(
            "SELECT area_key FROM access_grants WHERE staff_id = ?", (session.get("staff_id"),)
        ).fetchall()
    }
    return [h for h in MATERIAL_CATEGORIES if program_heading_key(program_code, h) in granted]


def _split_program_grants_by_heading(conn):
    """One-time conversion: an older grant on a whole program (area_key "D1")
    becomes four grants, one per heading ("D1:Syllabus", "D1:Music", ...), so
    nobody loses access they already had. Afterwards each heading can be
    taken away or given separately. Harmless to repeat."""
    codes = set(RESERVED_PROGRAM_AREA_CODES) | {
        row["code"] for row in conn.execute("SELECT code FROM programs").fetchall()
    }
    for row in conn.execute("SELECT id, area_key, staff_id FROM access_grants").fetchall():
        if row["area_key"] not in codes:
            continue
        for h in MATERIAL_CATEGORIES:
            key = program_heading_key(row["area_key"], h)
            exists = conn.execute(
                "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?", (key, row["staff_id"])
            ).fetchone()
            if not exists:
                conn.execute("INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (key, row["staff_id"]))
        conn.execute("DELETE FROM access_grants WHERE id = ?", (row["id"],))
    conn.commit()


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
            ",".join("?" for _ in PROGRAM_HEADING_AREA_KEYS)
        ),
        (session.get("staff_id"), *PROGRAM_HEADING_AREA_KEYS),
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


def _headings_needed(conn, program_id, kwargs):
    """Which heading(s) the current request touches, so access can be checked
    per heading. An empty list means "any heading" (the materials page then
    opens the first heading this account is allowed to see)."""
    endpoint = request.endpoint or ""
    if "playlist" in endpoint:
        return ["Music"]
    if endpoint == "crm.program_materials":
        cat = request.args.get("category", "").strip().title()
        return [cat] if cat in MATERIAL_CATEGORIES else []
    if endpoint == "crm.program_material_new":
        cat = request.form.get("category", "")
        return [cat if cat in MATERIAL_CATEGORIES else "Other"]
    needed = []
    material_id = kwargs.get("material_id")
    if material_id is not None:
        _ensure_material_categories(conn)
        row = conn.execute(
            "SELECT title, original_filename, category FROM program_materials WHERE id = ? AND program_id = ?",
            (material_id, program_id),
        ).fetchone()
        if row:
            needed.append(_material_category(row))
    if endpoint == "crm.program_material_move":
        target = request.form.get("category", "")
        if target in MATERIAL_CATEGORIES:
            needed.append(target)
    return needed


def program_area_required(view):
    """Gates a program's confidential training materials. The Owner sees
    everything; anyone else needs a grant for each heading involved
    (e.g. "D1:Syllabus"), set by the Owner from Site Access. Every wrapped
    route must take program_id as a URL argument. Sets g.allowed_headings."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        program_id = kwargs.get("program_id")
        conn = get_db()
        try:
            prog = conn.execute("SELECT code FROM programs WHERE id = ?", (program_id,)).fetchone()
            if not prog:
                abort(404)
            allowed = _allowed_headings(conn, prog["code"])
            needed = _headings_needed(conn, program_id, kwargs)
        finally:
            conn.close()
        if allowed and all(h in allowed for h in needed):
            if request.method == "POST" and not _can_edit_materials():
                flash("Team accounts can view training materials but can't upload, move, delete or change them.")
                return redirect(request.referrer or url_for(".programs_list"))
            g.allowed_headings = allowed
            return view(*args, **kwargs)
        flash("You don't have access to that part of this program's training materials. Ask the Owner to grant it from Site Access.")
        return redirect(url_for(".programs_list"))
    return wrapped


ACCOUNTING_LOGIN_GRANT = "accounting_login"   # access_grants key: lets a Leadership account use the Accounting & Legal door


def _door_allows(door, staff, is_owner):
    """Which accounts may sign in at which login page. The Owner may use any door.
    Team door: Team, Leadership. Leadership door: Leadership. Owner door: the Owner only.
    Accounting & Legal door: Accounting & Legal logins, plus a Leadership account only
    if the Owner has granted it."""
    if is_owner:
        return True
    role = staff["role"]
    if door == "team":
        return role in ("Team", "Leadership")
    if door == "leadership":
        return role == "Leadership"
    if door == "accounting":
        if role == ACCOUNTING_ROLE:
            return True
        if role == "Leadership":
            conn = get_db()
            g = conn.execute(
                "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?",
                (ACCOUNTING_LOGIN_GRANT, staff["id"]),
            ).fetchone()
            conn.close()
            return bool(g)
        return False
    return False  # owner door


def _home_door_title(staff):
    return {"Team": "Volunteer Team Login", "Leadership": "Leadership Login", ACCOUNTING_ROLE: "Accounting & Legal Login"}.get(
        staff["role"], "Volunteer Team Login")


def _attempt_login(next_url, door="team"):
    """Shared by both login pages -- checks credentials against the same
    staff table regardless of which page (Team or Leadership) was used."""
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE email = ? AND active = 1", (email,)).fetchone()
    conn.close()
    if staff and check_password_hash(staff["password_hash"], password):
        try:
            owner_flag = bool(staff["is_owner"])
        except (KeyError, IndexError):
            owner_flag = False
        if not _door_allows(door, staff, owner_flag):
            title = next(t for k, _, t in LOGIN_PAGES if k == door)
            if door == "owner":
                flash("The Owner Login is for the account Owner only.")
            elif door == "accounting" and staff["role"] == "Leadership":
                flash("The Owner has not given your account access to the Accounting & Legal Login. Ask the Owner.")
            else:
                flash(f"That account can't sign in at the {title}. Please use the {_home_door_title(staff)} page.")
            return None
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


LOGIN_PAGES = [
    ("team", "team_login", "Volunteer Team Login"),
    ("leadership", "leadership_login", "Leadership Login"),
    ("accounting", "accounting_login", "Accounting & Legal Login"),
    ("owner", "owner_login", "Owner Login"),
]


def _clean_next(n):
    """Never send someone back to the logout page after they sign in (it would sign them straight out)."""
    if n and n.rstrip("/").endswith("/logout"):
        return None
    return n


def _login_page(kind):
    """One sign-in page per door (Team, Leadership, Professional Team, Owner).
    All four check the same accounts -- the email and password are the security;
    the page just tells people which door is theirs."""
    if session.get("staff_id"):
        return redirect(url_for(".dashboard"))
    next_url = _clean_next(request.form.get("next") or request.args.get("next")) or url_for(".dashboard")
    if request.method == "POST":
        result = _attempt_login(next_url, kind)
        if result:
            return result
    title = next(t for k, _, t in LOGIN_PAGES if k == kind)
    return render_template(
        "login.html", next=next_url, page_title=title,
        # TEMPORARY: the Owner door pre-fills Kent's email. Delete these two lines to undo.
        prefill_email=(request.form.get("email") or "kent@blueskyusa.net") if kind == "owner" else request.form.get("email", ""),
        logins=[(LOGIN_BUTTON_TEXT[k], url_for("." + ep), k == kind) for k, ep, t in LOGIN_PAGES],
    )


LOGIN_BUTTON_TEXT = {
    "team": "Volunteer Team Members Login Here",
    "leadership": "Leadership Login Here",
    "accounting": "Accounting & Legal Login Here",
    "owner": "Owner Login Here",
}


@crm.route("/login", methods=["GET", "POST"])
def login():
    """The front door: a page with one button per login, so each person picks
    theirs. (A form submitted straight to this address is treated as a Volunteer
    Team sign-in, which keeps older bookmarks and saved forms working.)"""
    if session.get("staff_id"):
        return redirect(url_for(".dashboard"))
    if request.method == "POST":
        return _login_page("team")
    next_url = _clean_next(request.args.get("next"))
    logins = [
        (LOGIN_BUTTON_TEXT[k], url_for("." + ep, next=next_url) if next_url else url_for("." + ep))
        for k, ep, t in LOGIN_PAGES
    ]
    return render_template("login_choose.html", logins=logins)


@crm.route("/team-login", methods=["GET", "POST"])
def team_login():
    return _login_page("team")


@crm.route("/leadership-login", methods=["GET", "POST"])
def leadership_login():
    return _login_page("leadership")


@crm.route("/accounting-login", methods=["GET", "POST"])
def accounting_login():
    return _login_page("accounting")


@crm.route("/owner-login", methods=["GET", "POST"])
def owner_login():
    return _login_page("owner")


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
@owner_required
def staff_list():
    conn = get_db()
    rows = conn.execute("SELECT * FROM staff ORDER BY name").fetchall()
    area_labels = _area_labels_map(conn)
    all_areas = _all_areas(conn)
    grants = conn.execute("SELECT staff_id, area_key FROM access_grants").fetchall()
    program_groups = _program_area_groups(conn)
    conn.close()
    access_by_staff = {}
    granted_keys_by_staff = {}
    for g in grants:
        access_by_staff.setdefault(g["staff_id"], []).append(area_labels.get(g["area_key"], g["area_key"]))
        granted_keys_by_staff.setdefault(g["staff_id"], set()).add(g["area_key"])
    return render_template(
        "staff_list.html", staff=rows, access_by_staff=access_by_staff,
        all_areas=all_areas, granted_keys_by_staff=granted_keys_by_staff,
        accounting_areas=[a for a in all_areas if a[0] in ACCOUNTING_AREAS],
        fixed_areas=FIXED_ACCESS_AREAS, program_groups=program_groups,
        door_staff_ids={g["staff_id"] for g in grants if g["area_key"] == ACCOUNTING_LOGIN_GRANT},
    )


@crm.route("/staff/<int:staff_id>/reset-password", methods=["POST"])
@owner_required
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
@owner_required
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
    if target["role"] == ACCOUNTING_ROLE:
        all_areas = [a for a in all_areas if a[0] in ACCOUNTING_AREAS]

    if request.method == "POST":
        selected = set(request.form.getlist("area_key"))
        auto_contacts = False
        if selected & set(SESSION_VIEW_AREAS) and "contacts" not in selected:
            selected.add("contacts")          # the session boxes bring Contacts with them
            auto_contacts = True
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
                if count >= ACCESS_AREA_MAX_GRANTS and area_key not in UNCAPPED_AREAS \
                        and not (area_key == "contacts" and auto_contacts):
                    skipped_full.append(label)
                    continue
                conn.execute("INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (area_key, staff_id))
                added.append(label + (" (added automatically)" if area_key == "contacts" and auto_contacts else ""))
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
    program_groups = [] if target["role"] == ACCOUNTING_ROLE else _program_area_groups(conn)
    conn.close()
    return render_template(
        "staff_access.html", target=target, all_areas=all_areas,
        fixed_areas=[a for a in all_areas if a[0] in dict(FIXED_ACCESS_AREAS)], program_groups=program_groups,
        current_grants=current_grants, max_grants=ACCESS_AREA_MAX_GRANTS,
    )


@crm.route("/staff/new")
@owner_required
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
        _search_sql, _search_params = _contact_search_clause(q)
        contacts = conn.execute(
            f"""SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts
                WHERE {_search_sql}
                ORDER BY last_name, first_name LIMIT 25""",
            _search_params,
        ).fetchall()
    conn.close()
    return render_template("staff_new_search.html", q=q, contacts=contacts)


@crm.route("/staff/new/contact/<int:contact_id>", methods=["GET", "POST"])
@owner_required
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
        if role not in ROLE_LABELS:
            role = "Team"
        if role == ACCOUNTING_ROLE and not session.get("is_owner"):
            flash("Only the Owner can create Accounting & Legal logins. Nothing was added.")
            conn.close()
            return render_template("staff_new_confirm.html", contact=contact)
        temp_password = request.form["password"]
        if not email:
            flash("Enter an email to use for their ERP login.")
            conn.close()
            return render_template("staff_new_confirm.html", contact=contact)
        try:
            conn.execute(
                "INSERT INTO staff (name, email, password_hash, role, contact_id) VALUES (?, ?, ?, ?, ?)",
                (contact["full_name"], email, generate_password_hash(temp_password), role, contact_id),
            )
            conn.commit()
            flash(
                f"Added {contact['full_name']} as {role_label(role)}. Give them this email ({email}) "
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
@owner_required
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
        _search_sql, _search_params = _contact_search_clause(q)
        contacts = conn.execute(
            f"""SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts
                WHERE {_search_sql}
                ORDER BY last_name, first_name LIMIT 25""",
            _search_params,
        ).fetchall()
    conn.close()
    return render_template("staff_link_contact.html", staff=staff, q=q, contacts=contacts)


@crm.route("/staff/<int:staff_id>/link-contact/<int:contact_id>", methods=["GET", "POST"])
@owner_required
def staff_link_contact(staff_id, contact_id):
    if _accounting_target_blocked(staff_id):
        return redirect(url_for(".staff_list"))
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
@owner_required
def staff_unlink_contact(staff_id):
    if _accounting_target_blocked(staff_id):
        return redirect(url_for(".staff_list"))
    conn = get_db()
    conn.execute("UPDATE staff SET contact_id = NULL WHERE id = ?", (staff_id,))
    conn.commit()
    conn.close()
    flash("Unlinked that team login from its contact record.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/edit", methods=["POST"])
@owner_required
def staff_edit(staff_id):
    """Fixes a team member's name or email -- e.g. an account whose name
    was mistyped when it was created (by hand or auto-created while
    granting access)."""
    if _accounting_target_blocked(staff_id):
        return redirect(url_for(".staff_list"))
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
@owner_required
def staff_deactivate(staff_id):
    if _accounting_target_blocked(staff_id):
        return redirect(url_for(".staff_list"))
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
@owner_required
def staff_reactivate(staff_id):
    if _accounting_target_blocked(staff_id):
        return redirect(url_for(".staff_list"))
    conn = get_db()
    conn.execute("UPDATE staff SET active = 1 WHERE id = ?", (staff_id,))
    conn.commit()
    conn.close()
    flash("Account reactivated.")
    return redirect(url_for(".staff_list"))


@crm.route("/staff/<int:staff_id>/accounting-door", methods=["POST"])
@owner_required
def staff_accounting_door(staff_id):
    """Owner lets a Leadership account (or takes away) sign in at the Accounting & Legal
    login. It opens the door only; what they can then see still follows their area grants."""
    conn = get_db()
    staff = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not staff or staff["role"] != "Leadership":
        conn.close()
        flash("Only Leadership accounts need this permission.")
        return redirect(url_for(".staff_list"))
    has = conn.execute(
        "SELECT id FROM access_grants WHERE area_key = ? AND staff_id = ?", (ACCOUNTING_LOGIN_GRANT, staff_id)
    ).fetchone()
    if request.form.get("allow") and not has:
        conn.execute("INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (ACCOUNTING_LOGIN_GRANT, staff_id))
        flash(f"{staff['name']} can now sign in at the Accounting & Legal Login.")
    elif not request.form.get("allow") and has:
        conn.execute("DELETE FROM access_grants WHERE id = ?", (has["id"],))
        flash(f"{staff['name']} can no longer sign in at the Accounting & Legal Login.")
    conn.commit()
    conn.close()
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

    if staff["role"] == ACCOUNTING_ROLE:
        flash("Accounting & Legal logins can't be given full access. Choose their areas one by one under Access.")
        conn.close()
        return redirect(url_for(".staff_list"))
    conn.execute("UPDATE staff SET role = 'Leadership' WHERE id = ?", (staff_id,))

    all_area_keys = [key for key, _ in FIXED_ACCESS_AREAS] + [
        program_heading_key(row["code"], h)
        for row in conn.execute("SELECT code FROM programs").fetchall()
        for h in MATERIAL_CATEGORIES
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
        if count >= ACCESS_AREA_MAX_GRANTS and area_key not in UNCAPPED_AREAS:
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


def _contact_search_clause(q):
    """SQL + params for a name/email search. Every word typed must match the first
    name, last name, or email, so "Max Thompson" finds Max Thompson (and "thompson max"
    does too), while a single word or part of an email works as before."""
    words = q.split() or [q]
    parts, params = [], []
    for w in words:
        parts.append("(first_name LIKE ? OR last_name LIKE ? OR email LIKE ?)")
        params += [f"%{w}%", f"%{w}%", f"%{w}%"]
    return " AND ".join(parts), params


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
    "D1B": "The Name of your Buddy in D1",
    "D1P": "Your Pseudo Name",
    "D1": "Your Contract",
    "D1S": "Your Stretch Song",
    "D2": "Your Poem",
    "D2G": "The Five Gifts I see in You",
    "D3": "The Vision you have for your Future",
    "D6": "Your Couples Relationship Contract",
    "D6S": "Relationship Song Chosen for your Partner",
    "D5": "Spiritual Contract",
}

# The numbers people see in headings. Saved contracts keep their original internal keys (so no stored data
# had to change); the training order is D1, D2, D3, 4 = Relationship Training, 5 = Refocus, 6 = Spiritual.
CONTRACT_DISPLAY_CODES = {"D1B": "D1", "D1P": "D1", "D2G": "D2", "D6": "D4", "D6S": "D4S", "D5": "D6"}

# Kinds that get a single-line input instead of a multi-row textarea.
CONTRACT_SINGLE_LINE_KINDS = {"D1B", "D1P", "D1", "D1S", "D6S"}

# Example text shown as a placeholder for the kinds Kent gave an example
# for. Kinds not listed here just get an empty textarea -- there wasn't a
# short example that would fit as a placeholder.
CONTRACT_EXAMPLES = {
    "D1": "I am a Strong, Confident Man",
}


app.jinja_env.globals["contract_code"] = lambda kind: CONTRACT_DISPLAY_CODES.get(kind, kind)


_MONTH_ABBR = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]


def fdate_text(value):
    """'2027-08-15' (or a timestamp starting with that) -> 'Aug. 15, 2027'. Anything else is returned unchanged."""
    if not value:
        return value or ""
    try:
        d = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return value
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}, {d.year}"


app.jinja_env.filters["fdate"] = fdate_text


@app.template_filter("date_span")
def date_span(row):
    """A session's date for display: '2027-03-12', or '2027-03-12 to
    2027-03-14' when it runs more than one day."""
    start = row["session_date"]
    try:
        end = row["end_date"]
    except (KeyError, IndexError):
        end = None
    return f"{fdate_text(start)} to {fdate_text(end)}" if end and end != start else fdate_text(start)


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


# Sponsor address pieces are not asked on the public long form (staff keep them on the Sponsor card).
PUBLIC_FORM_SKIPPED_FIELDS = {
    "sponsor_street_address", "sponsor_street_address_2", "sponsor_city", "sponsor_state", "sponsor_zip",
}


def contact_profile_values_from_form(form):
    """Returns {column: cleaned_value} for every CONTACT_PROFILE_FIELDS
    column, built from a submitted form (internal or public)."""
    return {name: _clean_contact_field(name, form) for name in CONTACT_PROFILE_FIELDS}


def _create_past_sponsor(conn, name, phone, email, note):
    """Adds a sponsor who is not yet in the database as a new contact (status Sponsor) on the
    'Past sponsors' list, so they are never mistaken for a new prospect. Returns the new id."""
    parts = name.split()
    first, last = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (name, "")
    conn.execute(
        "INSERT INTO contacts (first_name, last_name, email, cell_phone, notes, status) VALUES (?,?,?,?,?,'Sponsor')",
        (first, last or None, email or None, phone or None, note),
    )
    new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    lst = conn.execute("SELECT id FROM contact_lists WHERE LOWER(name) = 'past sponsors'").fetchone()
    if not lst:
        conn.execute("INSERT INTO contact_lists (name, created_at) VALUES (?, ?)", ("Past sponsors", date.today().isoformat()))
        lst = conn.execute("SELECT id FROM contact_lists WHERE LOWER(name) = 'past sponsors'").fetchone()
    conn.execute("INSERT INTO contact_list_members (contact_id, list_id, added_at) VALUES (?,?,?)",
                 (new_id, lst["id"], date.today().isoformat()))
    return new_id


def _auto_link_or_add_sponsor(conn, contact_id):
    """Run when someone finishes the public long form. If they gave a Sponsor name plus a cell or
    email, link that sponsor to an existing contact (matched by email, then phone, then an exact
    unique name) or, failing that, add them as a new 'Sponsor' contact. Never duplicates, and does
    nothing without a name AND some way to reach them. Returns the sponsor's contact id or None."""
    c = conn.execute(
        f"SELECT id, {FULL_NAME_SQL} AS full_name, email, email_2, email_3, cell_phone, home_phone, work_phone, "
        "sponsor_name, sponsor_phone, sponsor_email, sponsor_contact_id FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if not c or c["sponsor_contact_id"]:
        return None
    name = (c["sponsor_name"] or "").strip()
    phone = (c["sponsor_phone"] or "").strip()
    email = (c["sponsor_email"] or "").strip()
    if not name or not (phone or email):
        return None
    # Someone typing their own email or phone as the "sponsor's" is a slip, not a sponsor.
    own_emails = {(c[k] or "").strip().lower() for k in ("email", "email_2", "email_3")} - {""}
    own_phones = {(c[k] or "").strip() for k in ("cell_phone", "home_phone", "work_phone")} - {""}
    if (email and email.lower() in own_emails) or (phone and phone in own_phones):
        return None
    match = None
    if email:
        row = conn.execute(
            """SELECT id FROM contacts WHERE id != ? AND (LOWER(email) = LOWER(?) OR LOWER(email_2) = LOWER(?)
               OR LOWER(email_3) = LOWER(?)) ORDER BY id LIMIT 1""", (contact_id, email, email, email)
        ).fetchone()
        match = row["id"] if row else None
    if not match and phone:
        row = conn.execute(
            "SELECT id FROM contacts WHERE id != ? AND (cell_phone = ? OR home_phone = ? OR work_phone = ?) ORDER BY id LIMIT 1",
            (contact_id, phone, phone, phone),
        ).fetchone()
        match = row["id"] if row else None
    if not match:
        rows = conn.execute(
            f"SELECT id FROM contacts WHERE id != ? AND LOWER({FULL_NAME_SQL}) = LOWER(?)", (contact_id, name)
        ).fetchall()
        match = rows[0]["id"] if len(rows) == 1 else None
    if not match:
        match = _create_past_sponsor(
            conn, name, phone, email, f"Added automatically from {c['full_name']}'s long form (their Sponsor)."
        )
    conn.execute("UPDATE contacts SET sponsor_contact_id = ? WHERE id = ?", (match, contact_id))
    return match


def _clean_sponsor_pick(conn, values, self_id=None):
    """The staff form's Sponsor name box offers contacts as 'Full Name [#123]'. Turn that into the
    plain name (and fill a blank sponsor phone/email from that contact). Returns the picked
    contact's id, or None when the name was simply typed."""
    import re as _re
    raw = (values.get("sponsor_name") or "").strip()
    m = _re.search(r"\[#(\d+)\]\s*$", raw)
    if not m:
        return None
    pick = int(m.group(1))
    row = conn.execute(
        f"SELECT {FULL_NAME_SQL} AS n, email, cell_phone, home_phone, work_phone FROM contacts WHERE id = ?", (pick,)
    ).fetchone()
    if not row or pick == self_id:
        values["sponsor_name"] = _re.sub(r"\s*\[#\d+\]\s*$", "", raw).strip() or None
        return None
    values["sponsor_name"] = row["n"]
    if not values.get("sponsor_phone"):
        values["sponsor_phone"] = row["cell_phone"] or row["home_phone"] or row["work_phone"] or None
    if not values.get("sponsor_email"):
        values["sponsor_email"] = row["email"] or None
    return pick


def _sponsor_choices(conn, exclude_id=None):
    return conn.execute(
        f"SELECT id, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id != ? ORDER BY last_name, first_name",
        (exclude_id or 0,),
    ).fetchall()


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


# ---------- mission ----------

@crm.route("/mission")
def mission_page():
    return render_template("mission.html")


# ---------- dashboard ----------

@crm.route("/")
def dashboard():
    conn = get_db()
    contact_count = conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
    upcoming = conn.execute(
        """SELECT c.*, p.code, p.name FROM cohorts c
           JOIN programs p ON p.id = c.program_id
           WHERE COALESCE(c.end_date, c.session_date) >= date('now')
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
    list_id = request.args.get("list", type=int)
    conn = get_db()
    where, params = [], []
    if q:
        _search_sql, _search_params = _contact_search_clause(q)
        where.append("(" + _search_sql + ")")
        params += _search_params
    if list_id:
        where.append("id IN (SELECT contact_id FROM contact_list_members WHERE list_id = ?)")
        params.append(list_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    limit_sql = "" if (q or list_id) else "LIMIT 200"
    rows = conn.execute(
        f"SELECT *, {FULL_NAME_SQL} AS full_name FROM contacts {where_sql} "
        f"ORDER BY last_name, first_name {limit_sql}",
        params,
    ).fetchall()
    all_lists = conn.execute("SELECT id, name FROM contact_lists ORDER BY name").fetchall()
    conn.close()
    return render_template("contacts_list.html", contacts=rows, q=q, list_id=list_id, all_lists=all_lists)


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
        pick_id = _clean_sponsor_pick(conn, values)
        conn.execute(
            f"INSERT INTO contacts ({', '.join(columns)}) VALUES ({placeholders})",
            [values[c] for c in columns],
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        link_id = pick_id or _resolve_sponsor_link(conn, new_id, values.get("sponsor_name") or "")[0]
        conn.execute("UPDATE contacts SET sponsor_contact_id = ? WHERE id = ?", (link_id, new_id))
        conn.commit()
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
    conn = get_db()
    choices = _sponsor_choices(conn)
    conn.close()
    return render_template(
        "contact_form.html",
        for_team_login=request.args.get("for_team_login"),
        link_staff_id=request.args.get("link_staff_id"),
        sponsor_choices=choices,
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
        pick_id = _clean_sponsor_pick(conn, values, self_id=contact_id)
        # The staff form no longer asks for the sponsor's address; saved details stay as they are.
        # email_2 / email_3 are no longer on this form: leave any saved values untouched (they still help
        # match a returning person to their record).
        columns = [c for c in CONTACT_PROFILE_FIELDS if c not in PUBLIC_FORM_SKIPPED_FIELDS and c not in ("email_2", "email_3")] + ["notes", "status"]
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ?",
            [values[c] for c in columns] + [contact_id],
        )
        link_id = pick_id or _resolve_sponsor_link(conn, contact_id, values.get("sponsor_name") or "")[0]
        sp_name = (values.get("sponsor_name") or "").strip()
        if not link_id and sp_name and request.form.get("create_sponsor") and "[#" not in sp_name:
            # The sponsor isn't in the database yet (e.g. someone from years ago): add them as a
            # contact on the "Past sponsors" list and link them.
            link_id = _create_past_sponsor(
                conn, sp_name, values.get("sponsor_phone") or "", values.get("sponsor_email") or "",
                "Added from a contact's Edit page (a past sponsor).",
            )
            flash(f"Added {sp_name} as a new contact (on the Past sponsors list) and linked as the Sponsor.")
        conn.execute("UPDATE contacts SET sponsor_contact_id = ? WHERE id = ?", (link_id, contact_id))
        conn.commit()
        photo_result = process_contact_photo(request.files.get("photo"))
        if photo_result:
            upsert_contact_photo(conn, contact_id, *photo_result)
            conn.commit()
        conn.close()
        flash("Contact updated.")
        return redirect(url_for(".contact_detail", contact_id=contact_id))

    choices = _sponsor_choices(conn, contact_id)
    sponsor_link = conn.execute(
        f"SELECT s.id, {full_name_sql('s')} AS full_name FROM contacts c JOIN contacts s ON s.id = c.sponsor_contact_id WHERE c.id = ?",
        (contact_id,),
    ).fetchone()
    linked_sponsors = conn.execute(
        f"""SELECT DISTINCT b.id, {full_name_sql('b')} AS full_name
            FROM enrollments e JOIN contacts b ON b.id = e.enrolled_by_contact_id
            WHERE e.contact_id = ? ORDER BY full_name""",
        (contact_id,),
    ).fetchall()
    contracts_by_kind = {}
    for kind in CONTRACT_KINDS:
        row = conn.execute("SELECT * FROM contracts WHERE contact_id = ? AND kind = ?", (contact_id, kind)).fetchone()
        contracts_by_kind[kind] = {"contract": row}
    rel_profiles = _relationship_profiles_for(conn, contact_id)
    conn.close()
    return render_template(
        "contact_form.html", rel_profiles=rel_profiles, contact=contact, sponsor_choices=choices, sponsor_link=sponsor_link,
        linked_sponsors=linked_sponsors, contract_kinds=CONTRACT_KINDS, contract_examples=CONTRACT_EXAMPLES,
        contract_single_line_kinds=CONTRACT_SINGLE_LINE_KINDS, contracts_by_kind=contracts_by_kind,
    )


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


def _mark_business_plan_sent(contact_id):
    """Records today's date as the last time the Preliminary Business Plan
    went out to this contact. Date only -- the plan changes over time, so we
    don't try to track which version. Best-effort: never blocks a send."""
    try:
        conn = get_db()
        conn.execute(
            "UPDATE contacts SET business_plan_sent_at = ? WHERE id = ?",
            (date.today().isoformat(), contact_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # e.g. column not migrated yet
        print(f"Couldn't record business-plan sent date for contact {contact_id}: {e}", flush=True)


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
        _mark_business_plan_sent(contact_id)
        print(
            f"SENT business plan to contact {contact_id} ({contact['email']}) by {session.get('staff_name')}",
            flush=True,
        )
        flash(f"Business plan sent to {contact['first_name']} ({contact['email']}).")
    except EmailSendError as e:
        print(f"EmailSendError sending business plan to {contact['email']}: {e}", flush=True)
        flash(f"Couldn't send the business plan: {e}")
    return redirect(url_for(".contact_detail", contact_id=contact_id))


def _render_letter_preview(contact_id, title, subject, letter_html, send_endpoint, send_label, leadership_only=False):
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    conn.close()
    if not contact:
        abort(404)
    return render_template(
        "contract_request_preview.html", contact=contact, title=title, subject=subject, letter_html=letter_html,
        send_endpoint=send_endpoint, send_label=send_label,
    )


def _contract_link_for(contact_id):
    conn = get_db()
    c = conn.execute("SELECT profile_token FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    conn.close()
    if c and c["profile_token"]:
        return url_for("public_complete_profile", token=c["profile_token"], contracts=1, _external=True)
    return request.host_url.rstrip("/") + "/complete-profile/(their-private-link)?contracts=1"


def _first_name_of(contact_id):
    conn = get_db()
    c = conn.execute("SELECT first_name FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    conn.close()
    if not c:
        abort(404)
    return c["first_name"]


@crm.route("/contacts/<int:contact_id>/preview-contract-request")
@area_required("contacts")
@leadership_required
def contact_preview_contract_request(contact_id):
    """Shows Leadership exactly what the Contract Information Request email says, without sending anything."""
    html_content, _t = contract_request_content(_first_name_of(contact_id), _contract_link_for(contact_id))
    return _render_letter_preview(contact_id, "Contract Information Request", CONTRACT_REQUEST_SUBJECT, html_content,
                                  ".contact_send_contract_request", "Send Contract Information Request")


@crm.route("/contacts/<int:contact_id>/preview-excitement-email")
@area_required("contacts")
def contact_preview_excitement_email(contact_id):
    """Shows the Excitement email exactly as it would be sent, without sending anything."""
    html_content, _t = excitement_blast_content(_first_name_of(contact_id))
    return _render_letter_preview(contact_id, "Excitement email", EXCITEMENT_BLAST_SUBJECT, html_content,
                                  ".contact_send_excitement_email", "Send excitement email")


@crm.route("/contacts/<int:contact_id>/preview-business-plan")
@area_required("contacts")
@leadership_required
def contact_preview_business_plan(contact_id):
    """Shows the business-plan email exactly as it would be sent, without sending anything."""
    html_content, _t = preliminary_plan_content(_first_name_of(contact_id))
    return _render_letter_preview(contact_id, "Business plan email", PRELIMINARY_PLAN_SUBJECT, html_content,
                                  ".contact_send_business_plan", "Send business plan")


@crm.route("/contacts/<int:contact_id>/send-contract-request", methods=["POST"])
@area_required("contacts")
@leadership_required
def contact_send_contract_request(contact_id):
    """Leadership-only: emails this person the "Contract Information Request" -- a favor-asking letter
    with their personal link to a page where they type in their own Contract, Stretch Song, Poem and
    the rest, so Discovery staff don't have to."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        abort(404)
    if not contact["email"]:
        conn.close()
        flash("Can't send -- this contact has no email address on file.")
        return redirect(url_for(".contact_detail", contact_id=contact_id))
    token = contact["profile_token"]
    if not token:
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (token, contact_id))
        conn.commit()
    conn.close()
    contracts_url = url_for("public_complete_profile", token=token, contracts=1, _external=True)
    html_content, text_content = contract_request_content(contact["first_name"], contracts_url)
    try:
        send_email(
            to_email=contact["email"],
            to_name=contact["first_name"],
            subject=CONTRACT_REQUEST_SUBJECT,
            html_content=html_content,
            text_content=text_content,
        )
        print(f"SENT contract information request to contact {contact_id} ({contact['email']}) by {session.get('staff_name')}", flush=True)
        flash(f"Contract Information Request sent to {contact['first_name']} ({contact['email']}).")
    except EmailSendError as e:
        print(f"EmailSendError sending contract request to {contact['email']}: {e}", flush=True)
        flash(f"Couldn't send the request: {e}")
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
        # The public form no longer asks for the sponsor's address, so those saved details
        # (kept on the staff Sponsor details card) must be left exactly as they are.
        saved_fields = [c for c in CONTACT_PROFILE_FIELDS if c not in PUBLIC_FORM_SKIPPED_FIELDS]
        set_clause = ", ".join(f"{c} = ?" for c in saved_fields)
        conn.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ?",
            [values[c] for c in saved_fields] + [contact["id"]],
        )
        # Keep the Sponsor pipeline link in step with the typed sponsor name.
        link_id, _note = _resolve_sponsor_link(conn, contact["id"], values.get("sponsor_name") or "")
        conn.execute("UPDATE contacts SET sponsor_contact_id = ? WHERE id = ?", (link_id, contact["id"]))
        if not request.form.get("autosave"):
            # They clicked Submit (not a background save): make sure their sponsor is a contact.
            _auto_link_or_add_sponsor(conn, contact["id"])
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
        force_contracts=bool(request.args.get("contracts")),
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

        emailed = False
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
                emailed = True
            except EmailSendError as e:
                print(f"EmailSendError sending connect-request confirmation to {contact['email']}: {e}", flush=True)
        conn.close()
        if emailed:
            return redirect(url_for("public_connect_request", token=token, submitted="1", emailed="1"))
        return redirect(url_for("public_connect_request", token=token, submitted="1"))

    conn.close()
    submitted = request.args.get("submitted") == "1"
    emailed_flag = submitted and request.args.get("emailed") == "1"
    return render_template("connect.html", contact=contact, submitted=submitted, emailed=emailed_flag)


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
                _mark_business_plan_sent(contact["id"])
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
    member_lists = conn.execute(
        """SELECT l.id, l.name, m.added_at FROM contact_list_members m
           JOIN contact_lists l ON l.id = m.list_id
           WHERE m.contact_id = ? ORDER BY l.name""",
        (contact_id,),
    ).fetchall()
    sponsor_choices = conn.execute(
        f"SELECT id, {FULL_NAME_SQL} AS full_name FROM contacts WHERE id != ? ORDER BY last_name, first_name",
        (contact_id,),
    ).fetchall()
    sponsor_link = conn.execute(
        f"SELECT s.id, {full_name_sql('s')} AS full_name FROM contacts c JOIN contacts s ON s.id = c.sponsor_contact_id WHERE c.id = ?",
        (contact_id,),
    ).fetchone()
    linked_sponsors = conn.execute(
        f"""SELECT DISTINCT b.id, {full_name_sql('b')} AS full_name
            FROM enrollments e JOIN contacts b ON b.id = e.enrolled_by_contact_id
            WHERE e.contact_id = ? ORDER BY full_name""",
        (contact_id,),
    ).fetchall()
    on_ids = {r["id"] for r in member_lists}
    addable_lists = [
        r for r in conn.execute("SELECT id, name FROM contact_lists ORDER BY name").fetchall()
        if r["id"] not in on_ids
    ]
    try:
        _ensure_completion_letters_table(conn)
        letters_sent = conn.execute(
            "SELECT program_code, sent_at FROM completion_letters WHERE contact_id = ? ORDER BY sent_at", (contact_id,)
        ).fetchall()
    except Exception:
        letters_sent = []
    conn.close()
    return render_template(
        "contact_detail.html",
        letters_sent=letters_sent, auto_letters_on=bool(_auto_letters_start()),
        member_lists=member_lists, linked_sponsors=linked_sponsors, sponsor_choices=sponsor_choices, sponsor_link=sponsor_link,
        addable_lists=addable_lists,
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
        """SELECT COUNT(DISTINCT trainee) c FROM (
               SELECT contact_id AS trainee FROM enrollments
               WHERE enrolled_by_contact_id = ? AND contact_id != ?
               UNION ALL
               SELECT id AS trainee FROM contacts
               WHERE sponsor_contact_id = ? AND id != ?
           ) x""",
        (contact_id, contact_id, contact_id, contact_id),
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
    label = "TA Team Captain" if duty == "Team Captain" else "TA"
    return (
        f"Heads-up: {who['n']} has brought in {have} "
        f"{'person' if have == 1 else 'people'}; the {label} requirement is {need}. "
        f"Assigned anyway -- record who they brought in on the session roster to update this."
    )


def _fill_sponsor_card(conn, trainee_id, sponsor_id):
    """When a session roster records who sponsored someone, fill that person's
    Sponsor details card from the sponsor's own contact record -- so it's entered
    once. Never overwrites a Sponsor card that already has a name."""
    t = conn.execute("SELECT sponsor_name FROM contacts WHERE id = ?", (trainee_id,)).fetchone()
    if not t or (t["sponsor_name"] or "").strip() or not sponsor_id:
        return False
    s = conn.execute(
        f"""SELECT {FULL_NAME_SQL} AS full_name, email, cell_phone, home_phone, work_phone,
                   street_address, street_address_2, city, state, zip
            FROM contacts WHERE id = ?""",
        (sponsor_id,),
    ).fetchone()
    if not s:
        return False
    conn.execute(
        """UPDATE contacts SET sponsor_name = ?, sponsor_phone = ?, sponsor_email = ?,
                               sponsor_street_address = ?, sponsor_street_address_2 = ?,
                               sponsor_city = ?, sponsor_state = ?, sponsor_zip = ?
           WHERE id = ?""",
        (s["full_name"], s["cell_phone"] or s["home_phone"] or s["work_phone"], s["email"],
         s["street_address"], s["street_address_2"], s["city"], s["state"], s["zip"], trainee_id),
    )
    return True


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


def _resolve_sponsor_link(conn, contact_id, raw_name):
    """What contact (if any) the Sponsor name on a card refers to. A name picked from the
    autocomplete ('Full Name [#123]') links directly. Otherwise a typed name that matches
    exactly ONE contact's full name is linked automatically; anything else stays plain text
    (and doesn't count toward anyone's pipeline). Returns (contact_id_or_None, note_or_None)."""
    import re as _re
    if not raw_name:
        return None, None
    m = _re.search(r"\[#(\d+)\]\s*$", raw_name)
    if m:
        cid = int(m.group(1))
        if cid != contact_id and conn.execute("SELECT 1 FROM contacts WHERE id = ?", (cid,)).fetchone():
            return cid, None
        return None, "That sponsor couldn't be linked, so it was saved as plain text."
    current = conn.execute(
        f"""SELECT s.id FROM contacts c JOIN contacts s ON s.id = c.sponsor_contact_id
            WHERE c.id = ? AND LOWER({full_name_sql('s')}) = LOWER(?)""",
        (contact_id, raw_name),
    ).fetchone()
    if current:
        return current["id"], None  # unchanged name: keep the existing link
    rows = conn.execute(
        f"SELECT id FROM contacts WHERE LOWER({FULL_NAME_SQL}) = LOWER(?) AND id != ?",
        (raw_name, contact_id),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["id"], None
    if len(rows) > 1:
        return None, ("More than one contact has that name, so the Sponsor was saved as plain text and does not "
                      "count toward a pipeline yet. Start typing the name and pick the right person from the list.")
    return None, ("Saved as plain text: no contact has exactly that name, so it does not count toward the Sponsor "
                  "pipeline. To count it, add the sponsor as a contact and pick them from the list.")


def _contact_delete_blockers(conn, contact_id):
    """Returns a list of plain-English reasons this contact can't be safely
    deleted yet -- real training/financial history that would otherwise be
    silently destroyed. Empty list means it's safe to delete."""
    reasons = []
    counts = [
        ("enrollments", "enrolled in a training session"),
        ("contracts", "a saved contract (D1/D2/D6)"),
        ("donations", "a recorded donation"),
        ("cohort_staffing", "listed as session staff"),
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
    sponsored = conn.execute(
        "SELECT COUNT(*) c FROM contacts WHERE sponsor_contact_id = ?", (contact_id,)
    ).fetchone()
    if sponsored["c"]:
        reasons.append("is recorded as the Sponsor of other people")
    staff_row = conn.execute("SELECT 1 FROM staff WHERE contact_id = ?", (contact_id,)).fetchone()
    if staff_row:
        reasons.append("is linked to an ERP login (unlink it from Site Access first)")
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
        (
            "people whose Sponsor card names them (the link is removed, the other person's contact is kept)",
            "SELECT COUNT(*) c FROM contacts WHERE sponsor_contact_id = ?",
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
    conn.execute("UPDATE contacts SET sponsor_contact_id = NULL WHERE sponsor_contact_id = ?", (contact_id,))
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
            flash("This contact is linked to an ERP login -- unlink it from Site Access first. Nothing was changed.")
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
    conn.execute("DELETE FROM contact_list_members WHERE contact_id = ?", (contact_id,))
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
    raw_name = request.form.get("sponsor_name", "").strip()
    link_id, link_note = _resolve_sponsor_link(conn, contact_id, raw_name)
    vals = {k: request.form.get(k, "").strip() for k in ("sponsor_phone", "sponsor_email")}
    name_to_save = raw_name
    if not link_id and raw_name and request.form.get("create_contact") and "[#" not in raw_name:
        # The sponsor isn't in the database yet (e.g. someone who sponsored them years ago):
        # add them as a contact, put them on the "Past sponsors" list, and link them.
        link_id = _create_past_sponsor(
            conn, raw_name, vals["sponsor_phone"], vals["sponsor_email"],
            "Added from a Sponsor details card (a past sponsor).",
        )
        flash(f"Added {raw_name} as a new contact (on the Past sponsors list) and linked as the Sponsor.")
        link_note = None
    if link_id:
        # Picked (or uniquely matched) from contacts: save the plain name, and fill any
        # detail left blank from the sponsor's own contact record.
        sp = conn.execute(
            f"""SELECT {FULL_NAME_SQL} AS full_name, email, cell_phone, home_phone, work_phone
                FROM contacts WHERE id = ?""",
            (link_id,),
        ).fetchone()
        name_to_save = sp["full_name"]
        fill = {
            "sponsor_phone": sp["cell_phone"] or sp["home_phone"] or sp["work_phone"],
            "sponsor_email": sp["email"],
        }
        for field, val in fill.items():
            if not vals[field] and val:
                vals[field] = val
    if link_note:
        flash(link_note)
    conn.execute(
        """UPDATE contacts SET sponsor_name = ?, sponsor_phone = ?, sponsor_email = ?,
                                sponsor_contact_id = ?
           WHERE id = ?""",
        (name_to_save or None, vals["sponsor_phone"] or None, vals["sponsor_email"] or None, link_id, contact_id),
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
    """Front page of Programs: one line per program, click a name to open it."""
    conn = get_db()
    programs = conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
    upcoming = {}
    for p in programs:
        upcoming[p["id"]] = conn.execute(
            "SELECT COUNT(*) AS n FROM cohorts WHERE program_id = ? AND COALESCE(end_date, session_date) >= date('now')",
            (p["id"],),
        ).fetchone()["n"]
    conn.close()
    return render_template("programs_home.html", programs=programs, upcoming=upcoming)


@crm.route("/programs/<int:program_id>")
def program_open(program_id):
    """One program: its materials, music, sessions and (for Leadership) scheduling and settings."""
    _ensure_cohort_location()
    conn = get_db()
    all_programs = conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
    programs = [p for p in all_programs if p["id"] == program_id]
    if not programs:
        conn.close()
        flash("That program no longer exists.")
        return redirect(url_for(".programs_list"))
    headings_by_program = {p["id"]: _allowed_headings(conn, p["code"]) for p in programs}
    cohorts_by_program = {}
    for p in programs:
        cohorts_by_program[p["id"]] = conn.execute(
            "SELECT * FROM cohorts WHERE program_id = ? ORDER BY session_number ASC", (p["id"],)
        ).fetchall()
    conn.close()
    today_iso = date.today().isoformat()
    upcoming_by_program, past_by_program = {}, {}
    for pid, rows in cohorts_by_program.items():
        upcoming_by_program[pid] = [c for c in rows if (c["end_date"] or c["session_date"]) >= today_iso]
        past_by_program[pid] = [c for c in rows if (c["end_date"] or c["session_date"]) < today_iso]
    cal_year = max(2000, min(2100, request.args.get("cal_year", type=int) or date.today().year))
    pick_cal = _calendar_data(cal_year, public=False)
    holiday_map = {}
    for y in range(date.today().year - 1, date.today().year + 4):
        for d, (n, _kind) in _holidays_for_year(y)[0].items():
            holiday_map[d.isoformat()] = n
    return render_template(
        "programs_list.html", upcoming_by_program=upcoming_by_program, past_by_program=past_by_program, holiday_map=holiday_map, pick_cal=pick_cal,
        pick_prev=url_for(".program_open", program_id=program_id, cal_year=cal_year - 1) + "#sched",
        pick_next=url_for(".program_open", program_id=program_id, cal_year=cal_year + 1) + "#sched",
        pick_today=url_for(".program_open", program_id=program_id) + "#sched", programs=programs, all_programs=all_programs, cohorts_by_program=cohorts_by_program,
        headings_by_program=headings_by_program,
        default_days=lambda code: DEFAULT_SESSION_DAYS.get(code, DEFAULT_SESSION_DAYS_OTHER),
        usual_weekday=USUAL_START_WEEKDAY,
    )


@crm.route("/programs/new", methods=["POST"])
@leadership_required
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
@leadership_required
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
    return redirect(url_for(".program_open", program_id=program_id))


@crm.route("/programs/<int:program_id>/delete", methods=["POST"])
@leadership_required
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


def _music_day_labels(program):
    """Sections on a program's Music page: Friday Night / Saturday / Sunday for every program,
    plus Saturday Evening Dance for Relationship Training. (General appears only when something is filed there.)"""
    labels = ["Friday Night", "Saturday", "Sunday"]
    if "relationship" in ((program["name"] if program else "") or "").lower():
        labels.insert(2, "Saturday Evening Dance")
    return labels

# Every program's training materials are filed under one of these four
# headings -- they are the four links shown under each program on the
# Programs page, and the four tabs across the top of each materials page.
MATERIAL_CATEGORY_BLURBS = {
    "Syllabus": "The run sheet for the training: the schedule and order of activities that facilitators and TAs follow.",
    "Music": "Song lists used during the training, organized by day and purpose, plus any music files (song sheets, assignment sheets).",
    "Handouts": "Handouts, worksheets, and resource sheets given to trainees, organized by which day they're used.",
    "Other": "Anything else for this training that isn't a syllabus, music, or a handout.",
}
_SYLLABUS_WORDS = ("syllab", "run sheet", "run-sheet", "runsheet")
_MUSIC_WORDS = ("song", "music", "playlist")
_material_category_ready = False


def _guess_material_category(title, original_filename):
    """Best guess at the heading for a file that was uploaded before the
    four headings existed (or by an import script that doesn't set one)."""
    text = f"{title or ''} {original_filename or ''}".lower()
    if any(w in text for w in _SYLLABUS_WORDS):
        return "Syllabus"
    if any(w in text for w in _MUSIC_WORDS):
        return "Music"
    return "Handouts"


def _material_category(row):
    cat = row["category"]
    return cat if cat in MATERIAL_CATEGORIES else _guess_material_category(row["title"], row["original_filename"])


def _ensure_material_categories(conn):
    """Adds the program_materials.category column the first time it's needed
    and files every older upload under a heading, so no separate migration
    has to be run by hand after deploying. Runs once per server start and is
    harmless to repeat."""
    global _material_category_ready
    if _material_category_ready:
        return
    conn.execute("ALTER TABLE program_materials ADD COLUMN IF NOT EXISTS category TEXT")
    for row in conn.execute(
        "SELECT id, title, original_filename FROM program_materials WHERE category IS NULL"
    ).fetchall():
        conn.execute(
            "UPDATE program_materials SET category = ? WHERE id = ?",
            (_guess_material_category(row["title"], row["original_filename"]), row["id"]),
        )
    conn.commit()
    _material_category_ready = True


_materials_setup_done = False


@crm.before_request
def _materials_one_time_setup():
    """Runs the two small database upgrades for training materials (the
    heading column, and splitting whole-program grants into per-heading
    grants) once per server start, so nothing has to be run by hand."""
    global _materials_setup_done
    if _materials_setup_done or not session.get("staff_id"):
        return
    try:
        conn = get_db()
        try:
            _ensure_material_categories(conn)
            _split_program_grants_by_heading(conn)
        finally:
            conn.close()
        _materials_setup_done = True
    except Exception:
        app.logger.exception("Training-materials setup failed; will retry on the next request.")


IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}


def _can_edit_materials():
    """Uploading, moving, deleting and changing training materials (files and
    song lists) is for the Owner and Leadership accounts. Team accounts with
    access can only view."""
    return bool(session.get("is_owner")) or session.get("staff_role") == "Leadership"


@crm.context_processor
def _inject_materials_edit():
    return {"can_edit_materials": _can_edit_materials()}


def _can_download_materials():
    """Only the Owner may download training-material files. Everyone else
    with access can view them on the page but gets no download link."""
    return bool(session.get("is_owner"))


def _materials_url(program_id, category):
    """Where a heading lives: Music shares a page with the song lists, the
    other three each get their own page of files."""
    if category == "Music":
        return url_for(".program_playlists", program_id=program_id)
    return url_for(".program_materials", program_id=program_id, category=category)


def _load_materials(conn, program_id):
    """Every material for a program (without the file bytes -- those are only
    read when a file is actually opened), grouped by heading and then by day.
    Returns (by_category, counts)."""
    _ensure_material_categories(conn)
    rows = conn.execute(
        """SELECT id, program_id, day_label, title, original_filename, uploaded_at, category
           FROM program_materials WHERE program_id = ? ORDER BY uploaded_at, id""",
        (program_id,),
    ).fetchall()
    by_category = {c: {} for c in MATERIAL_CATEGORIES}
    counts = {c: 0 for c in MATERIAL_CATEGORIES}
    for r in rows:
        cat = _material_category(r)
        by_category[cat].setdefault(r["day_label"] or "General", []).append(r)
        counts[cat] += 1
    return by_category, counts


def _ordered_days(by_day):
    return [d for d in DAY_LABEL_ORDER if d in by_day] + [d for d in by_day if d not in DAY_LABEL_ORDER]


@crm.route("/programs/<int:program_id>/materials")
@program_area_required
def program_materials(program_id):
    allowed = g.allowed_headings
    requested = request.args.get("category", "").strip().title()
    category = requested if requested in allowed else allowed[0]
    if category == "Music":
        return redirect(_materials_url(program_id, category))
    conn = get_db()
    program = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    if not program:
        conn.close()
        abort(404)
    by_category, counts = _load_materials(conn, program_id)
    conn.close()
    by_day = by_category[category]
    return render_template(
        "program_materials.html", program=program, by_day=by_day, ordered_days=_ordered_days(by_day),
        day_label_choices=DAY_LABEL_ORDER, category=category, categories=allowed,
        category_counts=counts, category_blurb=MATERIAL_CATEGORY_BLURBS[category],
        can_download=_can_download_materials(),
    )


@crm.route("/programs/<int:program_id>/materials/<int:material_id>/file")
@program_area_required
def program_material_file(program_id, material_id):
    """Serves a training-material file's bytes straight from the database
    (never from local disk, which Render wipes on every deploy) after
    checking program-level authorization -- never through the public
    /static/ route."""
    import doc_reader
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM program_materials WHERE id = ? AND program_id = ?", (material_id, program_id)
    ).fetchone()
    conn.close()
    if not row or not row["data"]:
        abort(404)
    if not _can_download_materials():
        # View-only accounts: the raw file is only handed to the on-page
        # viewer (a PDF drawn by the viewer script, or an <img> for a
        # picture) -- never to a link, a typed-in address, or "save as".
        ext = doc_reader.extension(row["original_filename"])
        dest = request.headers.get("Sec-Fetch-Dest", "")
        same_site = request.headers.get("Sec-Fetch-Site", "") == "same-origin"
        viewer_request = same_site and ((ext == "pdf" and dest == "empty") or (ext in IMAGE_EXTENSIONS and dest == "image"))
        if not viewer_request:
            flash("Training materials can be viewed here but not downloaded.")
            return redirect(url_for(".program_material_read", program_id=program_id, material_id=material_id))
    if doc_reader.extension(row["original_filename"]) in AUDIO_EXTENSIONS:
        # Audio is sent with Range support so the play bar can skip around in a song.
        from flask import send_file
        resp = send_file(io.BytesIO(bytes(row["data"])), mimetype=row["content_type"] or "application/octet-stream", conditional=True)
    else:
        resp = make_response(bytes(row["data"]))
        resp.headers["Content-Type"] = row["content_type"] or "application/octet-stream"
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if _can_download_materials():
        disposition = "attachment" if request.args.get("download") else "inline"
        safe_name = row["original_filename"].replace('"', "")
        resp.headers["Content-Disposition"] = f'{disposition}; filename="{safe_name}"'
    else:
        resp.headers["Content-Disposition"] = "inline"
    return resp


@crm.route("/programs/<int:program_id>/materials/<int:material_id>/read")
@program_area_required
def program_material_read(program_id, material_id):
    """Shows a Word / Excel / PowerPoint material as a readable page (converted on the fly from the stored file)."""
    import doc_reader
    conn = get_db()
    _ensure_material_categories(conn)
    row = conn.execute(
        "SELECT * FROM program_materials WHERE id = ? AND program_id = ?", (material_id, program_id)
    ).fetchone()
    program = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    conn.close()
    if not row or not row["data"] or not program:
        abort(404)
    ext = doc_reader.extension(row["original_filename"])
    body = doc_reader.to_html(bytes(row["data"]), row["original_filename"])
    if body:
        kind = "html"
    elif ext == "txt":
        import html as _html
        body = "<pre style=\"white-space:pre-wrap\">" + _html.escape(bytes(row["data"]).decode("utf-8", "replace")) + "</pre>"
        kind = "html"
    elif ext == "pdf":
        kind = "pdf"
    elif ext in IMAGE_EXTENSIONS:
        kind = "image"
    else:
        kind = "none"
    category = _material_category(row)
    return render_template("material_read.html", program=program, material=row, body=body, kind=kind, ext=ext,
                           is_old_word=ext == "doc", can_download=_can_download_materials(),
                           category=category, back_url=_materials_url(program_id, category))


@crm.route("/programs/<int:program_id>/materials/new", methods=["POST"])
@program_area_required
def program_material_new(program_id):
    title = request.form.get("title", "").strip()
    day_label = request.form.get("day_label") or None
    category = request.form.get("category", "")
    if category not in MATERIAL_CATEGORIES:
        category = "Other"
    material_result = process_program_material(request.files.get("file"))
    if material_result:
        data, content_type = material_result
        original_filename = request.files["file"].filename
        filename = f"program_{program_id}_{secrets.token_hex(6)}"
        conn = get_db()
        _ensure_material_categories(conn)
        conn.execute(
            """INSERT INTO program_materials
               (program_id, day_label, title, filename, original_filename, data, content_type, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (program_id, day_label, title or original_filename, filename, original_filename, data, content_type, category),
        )
        conn.commit()
        conn.close()
        flash(f"Uploaded to {category}.")
    return redirect(_materials_url(program_id, category))


@crm.route("/programs/<int:program_id>/materials/<int:material_id>/move", methods=["POST"])
@program_area_required
def program_material_move(program_id, material_id):
    """Re-files a material under a different heading (e.g. Handouts -> Syllabus)."""
    target = request.form.get("category", "")
    back = request.form.get("back", "")
    conn = get_db()
    _ensure_material_categories(conn)
    row = conn.execute(
        "SELECT id, title FROM program_materials WHERE id = ? AND program_id = ?", (material_id, program_id)
    ).fetchone()
    if row and target in MATERIAL_CATEGORIES:
        conn.execute("UPDATE program_materials SET category = ? WHERE id = ?", (target, material_id))
        conn.commit()
        flash(f"Moved “{row['title']}” to {target}.")
    conn.close()
    return redirect(_materials_url(program_id, back if back in MATERIAL_CATEGORIES else MATERIAL_CATEGORIES[0]))


@crm.route("/programs/<int:program_id>/materials/<int:material_id>/delete", methods=["POST"])
@program_area_required
def program_material_delete(program_id, material_id):
    conn = get_db()
    _ensure_material_categories(conn)
    row = conn.execute(
        "SELECT id, title, original_filename, category FROM program_materials WHERE id = ? AND program_id = ?",
        (material_id, program_id),
    ).fetchone()
    category = MATERIAL_CATEGORIES[0]
    if row:
        category = _material_category(row)
        conn.execute("DELETE FROM program_materials WHERE id = ?", (material_id,))
        conn.commit()
        flash("Material deleted.")
    conn.close()
    return redirect(_materials_url(program_id, category))


# ---------- music / playlists ----------

@crm.route("/programs/<int:program_id>/playlists")
@program_area_required
def program_playlists(program_id):
    conn = get_db()
    program = conn.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    if not program:
        conn.close()
        abort(404)
    playlists = conn.execute(
        "SELECT * FROM program_playlists WHERE program_id = ? ORDER BY id", (program_id,)
    ).fetchall()
    songs_by_playlist = {}
    for pl in playlists:
        songs_by_playlist[pl["id"]] = conn.execute(
            "SELECT * FROM playlist_songs WHERE playlist_id = ? ORDER BY position, id", (pl["id"],)
        ).fetchall()
    by_category, counts = _load_materials(conn, program_id)
    conn.close()
    by_day = {}
    for pl in playlists:
        label = pl["day_label"] or "General"
        by_day.setdefault(label, []).append(pl)
    files_by_day = by_category["Music"]
    # Every section shows, even when empty, so it is clear where to add songs and music files.
    labels = _music_day_labels(program)
    extra_days = [d for d in list(by_day) + list(files_by_day) if d not in labels]
    all_days = labels + [d for i, d in enumerate(extra_days) if d not in extra_days[:i]]
    by_day = {d: by_day.get(d, []) for d in all_days}
    files_by_day = {d: files_by_day.get(d, []) for d in all_days}
    return render_template(
        "program_playlists.html", program=program, by_day=by_day, ordered_days=all_days,
        day_label_choices=all_days, songs_by_playlist=songs_by_playlist,
        files_by_day=files_by_day, files_ordered_days=all_days,
        category="Music", categories=g.allowed_headings, category_counts=counts,
        category_blurb=MATERIAL_CATEGORY_BLURBS["Music"], can_download=_can_download_materials(),
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
@owner_required
def authorized_users_hub():
    conn = get_db()
    program_groups = _program_area_groups(conn)
    conn.close()
    return render_template("authorized_users_hub.html", areas=list(FIXED_ACCESS_AREAS), program_groups=program_groups)


@crm.route("/authorized-users/<area_key>")
@owner_required
def authorized_users_area(area_key):
    conn = get_db()
    area_label = _area_labels_map(conn).get(area_key, area_key)
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
@owner_required
def authorized_users_assign(area_key):
    """Authorizes someone for this area by name + email. If that email
    doesn't match an existing Team account, one is created on the spot
    (with a generated temporary password shown to Leadership once) --
    Kent doesn't have to visit the Team page first."""
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) c FROM access_grants WHERE area_key = ?", (area_key,)
    ).fetchone()["c"]
    if count >= ACCESS_AREA_MAX_GRANTS and area_key not in UNCAPPED_AREAS:
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

    target_row = conn.execute("SELECT role FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if target_row and target_row["role"] == ACCOUNTING_ROLE and area_key not in ACCOUNTING_AREAS:
        flash("Accounting & Legal logins can only be given Accounting, Donations, Contracts, or Contacts.")
        conn.close()
        return redirect(url_for(".authorized_users_area", area_key=area_key))
    existing_grant = conn.execute(
        "SELECT 1 FROM access_grants WHERE area_key = ? AND staff_id = ?", (area_key, staff_id)
    ).fetchone()
    if not existing_grant:
        conn.execute(
            "INSERT INTO access_grants (area_key, staff_id) VALUES (?, ?)", (area_key, staff_id)
        )
        if _grant_contacts_for_session_areas(conn, staff_id):
            flash("Contacts was added automatically, since the session pages sit under Contacts.")
        conn.commit()
        if not already_flashed:
            flash("Access granted.")
    elif already_flashed:
        pass  # account was just created/reactivated but somehow already had this exact grant -- unusual, nothing more to say
    conn.close()
    return redirect(url_for(".authorized_users_area", area_key=area_key))


@crm.route("/authorized-users/<area_key>/<int:grant_id>/remove", methods=["POST"])
@owner_required
def authorized_users_remove(area_key, grant_id):
    conn = get_db()
    conn.execute("DELETE FROM access_grants WHERE id = ?", (grant_id,))
    conn.commit()
    conn.close()
    flash("Access removed.")
    return redirect(url_for(".authorized_users_area", area_key=area_key))


@crm.route("/authorized-users/<area_key>/<int:staff_id>/reset-password", methods=["POST"])
@owner_required
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


# How many days each training normally runs. Used only to pre-fill the "through"
# date when scheduling a session (staff can always change it). D5 = Refocus,
# a single Saturday; every other training is a three-day weekend.
# Programs that normally run on the same weekend, in the same place. Scheduling one of them offers the others.
WEEKEND_BUNDLE = ["D1", "D2", "D3", "D5"]

_cohort_location_ready = False


def _ensure_cohort_location():
    """The 'location' column on cohorts is added the first time it's needed (no separate migration to run)."""
    global _cohort_location_ready
    if _cohort_location_ready:
        return
    conn = get_db()
    try:
        conn.execute("ALTER TABLE cohorts ADD COLUMN IF NOT EXISTS location TEXT")
        conn.commit()
        _cohort_location_ready = True
    except Exception as e:
        print(f"Couldn't add cohorts.location: {e}", flush=True)
    finally:
        conn.close()


_enrollment_extras_ready = False


def _ensure_enrollment_extras():
    """Columns for couples and the self-fill questionnaire, added the first time they're needed."""
    global _enrollment_extras_ready
    if _enrollment_extras_ready:
        return
    conn = get_db()
    try:
        conn.execute("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS partner_enrollment_id INTEGER")
        conn.execute("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS questionnaire_token TEXT")
        conn.commit()
        _enrollment_extras_ready = True
    except Exception as e:
        print(f"Couldn't add enrollment couple/questionnaire columns: {e}", flush=True)
    finally:
        conn.close()


DEFAULT_SESSION_DAYS = {"D5": 2}
DEFAULT_SESSION_DAYS_OTHER = 3
# Trainings that are normally held on one particular weekday (Python weekday:
# Monday=0 ... Saturday=5, Sunday=6) -> name shown in a gentle heads-up.
USUAL_START_WEEKDAY = {"D5": (5, "Saturday")}


def _clean_end_date(start, raw_end):
    """Returns (end_date_or_None, error_or_None). A blank end date, or one equal
    to the start, means a one-day session. The end can't be before the start."""
    raw_end = (raw_end or "").strip()
    if not raw_end or raw_end == start:
        return None, None
    if raw_end < start:
        return None, "The end date can't be before the start date. Nothing was saved."
    return raw_end, None


@crm.route("/programs/<int:program_id>/cohorts/new", methods=["POST"])
@leadership_required
def cohort_new(program_id):
    """Schedules one or several sessions at once. The page can queue many date ranges
    (batch_start / batch_end, repeated) and also sends the range currently picked
    (session_date / end_date). Sessions are numbered in date order, continuing from the
    program's highest number. Nothing is saved if any range is invalid."""
    cal_year = request.form.get("cal_year", type=int)

    def back():
        return redirect(url_for(".program_open", program_id=program_id, cal_year=cal_year) if cal_year
                        else url_for(".program_open", program_id=program_id))

    pairs = list(zip(request.form.getlist("batch_start"), request.form.getlist("batch_end")))
    one = (request.form.get("session_date") or "").strip()
    if one:
        pairs.append((one, request.form.get("end_date") or ""))
    cleaned = {}
    for start, raw_end in pairs:
        start = (start or "").strip()
        try:
            date.fromisoformat(start)
        except ValueError:
            flash("One of the dates wasn't valid. Nothing was saved.")
            return back()
        end_date, date_error = _clean_end_date(start, raw_end)
        if date_error:
            flash(date_error)
            return back()
        cleaned[(start, end_date)] = True
    ranges = sorted(cleaned)
    if not ranges:
        flash("Pick at least one date. Nothing was saved.")
        return back()

    _ensure_cohort_location()
    location = (request.form.get("location") or "").strip() or None
    also_ids = []
    for raw in request.form.getlist("also"):
        try:
            also_ids.append(int(raw))
        except ValueError:
            pass

    conn = get_db()
    code = conn.execute("SELECT code FROM programs WHERE id = ?", (program_id,)).fetchone()["code"]

    def next_number(pid):
        return conn.execute(
            "SELECT COALESCE(MAX(session_number), 0) + 1 AS n FROM cohorts WHERE program_id = ?", (pid,)
        ).fetchone()["n"]

    def make(pid, pcode, start, end_date):
        exists = conn.execute(
            "SELECT 1 FROM cohorts WHERE program_id = ? AND session_date = ?", (pid, start)
        ).fetchone()
        if exists:
            return f"{pcode} already has a session starting {fdate_text(start)} (skipped)"
        num = next_number(pid)
        conn.execute(
            "INSERT INTO cohorts (program_id, session_number, session_date, end_date, location) VALUES (?, ?, ?, ?, ?)",
            (pid, num, start, end_date, location),
        )
        return f"{pcode} #{num} ({_short_span(date.fromisoformat(start), date.fromisoformat(end_date or start))})"

    also_programs = []
    if also_ids:
        for r in conn.execute("SELECT id, code FROM programs ORDER BY id").fetchall():
            if r["id"] in also_ids and r["id"] != program_id and r["code"] in WEEKEND_BUNDLE:
                also_programs.append(r)

    made = []
    for start, end_date in ranges:
        first = date.fromisoformat(start)
        if code == "D5":
            # Refocus is Saturday + Sunday, so the weekend's Friday is the day before
            fri = first - timedelta(days=1)
            sun = date.fromisoformat(end_date or start) if end_date else first + timedelta(days=1)
        else:
            fri = first
            sun = date.fromisoformat(end_date) if end_date else first + timedelta(days=DEFAULT_SESSION_DAYS_OTHER - 1)
        sat = fri + timedelta(days=1)
        made.append(make(program_id, code, start, end_date))
        for ap in also_programs:
            if ap["code"] == "D5":
                a_start, a_end = sat.isoformat(), sun.isoformat() if sun > sat else (sat + timedelta(days=1)).isoformat()
            else:
                a_start, a_end = fri.isoformat(), sun.isoformat()
            made.append(make(ap["id"], ap["code"], a_start, a_end))
    conn.commit()
    conn.close()
    if len(made) == 1:
        flash(f"Scheduled {made[0]}. It now appears in the list below and on the calendar.")
    else:
        flash(f"Scheduled {len(made)} sessions: " + "; ".join(made) + ". They now appear in the lists and on the calendar.")
    return back()


COHORT_STATUSES = ["Scheduled", "Completed", "Cancelled"]


def _after_cohort_change(cohort_id):
    """Back to the Programs page when the change was made there, otherwise to
    the session's own page."""
    if request.form.get("back") == "programs":
        return redirect(url_for(".programs_list"))
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/cohorts/<int:cohort_id>/dates", methods=["POST"])
@area_required("contacts")
@leadership_required
def cohort_update_dates(cohort_id):
    """Change ONE scheduled session (its dates and status). Does not touch the
    program itself or any other session."""
    start = (request.form.get("session_date") or "").strip()
    if not start:
        flash("Please enter a start date. Nothing was changed.")
        return _after_cohort_change(cohort_id)
    end_date, date_error = _clean_end_date(start, request.form.get("end_date"))
    if date_error:
        flash(date_error)
        return _after_cohort_change(cohort_id)
    status = request.form.get("status")
    _ensure_cohort_location()
    conn = get_db()
    if "location" in request.form:
        conn.execute("UPDATE cohorts SET location = ? WHERE id = ?",
                     ((request.form.get("location") or "").strip() or None, cohort_id))
    if status in COHORT_STATUSES:
        conn.execute(
            "UPDATE cohorts SET session_date = ?, end_date = ?, status = ? WHERE id = ?",
            (start, end_date, status, cohort_id),
        )
    else:
        conn.execute("UPDATE cohorts SET session_date = ?, end_date = ? WHERE id = ?", (start, end_date, cohort_id))
    conn.commit()
    conn.close()
    flash("Session saved.")
    return _after_cohort_change(cohort_id)


@crm.route("/cohorts/<int:cohort_id>/delete", methods=["POST"])
@area_required("contacts")
@leadership_required
def cohort_delete(cohort_id):
    """Delete ONE scheduled session that has nothing attached to it. A session
    with enrolled people, staff, or contracts is protected -- the safe choice
    for those is to mark it Cancelled."""
    conn = get_db()
    cohort = conn.execute(
        """SELECT c.*, p.code FROM cohorts c JOIN programs p ON p.id = c.program_id WHERE c.id = ?""",
        (cohort_id,),
    ).fetchone()
    if not cohort:
        conn.close()
        flash("That session no longer exists.")
        return redirect(url_for(".programs_list"))
    label = f"{cohort['code']} #{cohort['session_number']}"
    checks = [
        ("SELECT COUNT(*) AS n FROM enrollments WHERE cohort_id = ?", "{n} enrolled trainee(s)"),
        ("SELECT COUNT(*) AS n FROM cohort_staffing WHERE cohort_id = ?", "{n} staff assignment(s)"),
        ("""SELECT COUNT(*) AS n FROM small_group_staffing sgs
            JOIN small_groups sg ON sg.id = sgs.small_group_id WHERE sg.cohort_id = ?""",
         "{n} small-group staff assignment(s)"),
        ("SELECT COUNT(*) AS n FROM contracts WHERE originating_cohort_id = ?", "{n} contract(s) started in it"),
    ]
    blockers = []
    for sql, text in checks:
        n = conn.execute(sql, (cohort_id,)).fetchone()["n"]
        if n:
            blockers.append(text.format(n=n))
    if blockers:
        conn.close()
        flash(
            f"Can't delete {label} -- it has " + ", ".join(blockers) + ". "
            f"To take it off the calendar without losing that history, change its status to Cancelled instead."
        )
        return redirect(url_for(".programs_list"))
    conn.execute("DELETE FROM small_groups WHERE cohort_id = ?", (cohort_id,))
    conn.execute("DELETE FROM cohorts WHERE id = ?", (cohort_id,))
    conn.commit()
    conn.close()
    print(f"DELETED session {label} (cohort {cohort_id}) by {session.get('staff_name')}", flush=True)
    flash(f"Deleted the session {label}. The {cohort['code']} program itself is unchanged.")
    return redirect(url_for(".programs_list"))


def _norm_name(s):
    import re as _re
    return " ".join(_re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def _auto_link_couples(conn, cohort_id):
    """Joins trainees of this session whose long form names the other as their partner (by email, or by name).
    Only links people who aren't linked yet, and only when the match is unambiguous. Shares an existing payment."""
    rows = conn.execute(
        f"""SELECT e.id AS eid, e.partner_enrollment_id AS pe, e.payment_completed_at AS paid,
                   ct.first_name, ct.last_name, ct.email, ct.partner_name, ct.partner_email,
                   {full_name_sql('ct')} AS full_name
            FROM enrollments e JOIN contacts ct ON ct.id = e.contact_id WHERE e.cohort_id = ?""", (cohort_id,)).fetchall()
    free = [r for r in rows if not r["pe"]]
    linked = set()
    for a in free:
        if a["eid"] in linked or not (a["partner_name"] or a["partner_email"]):
            continue
        others = [b for b in free if b["eid"] != a["eid"] and b["eid"] not in linked]
        pick = None
        pe = (a["partner_email"] or "").strip().lower()
        if pe:
            hits = [b for b in others if (b["email"] or "").strip().lower() == pe]
            if len(hits) == 1:
                pick = hits[0]
        if not pick and a["partner_name"]:
            pn = _norm_name(a["partner_name"])
            hits = [b for b in others if _norm_name(b["full_name"]) == pn]
            if not hits and len(pn.split()) == 1:
                hits = [b for b in others if _norm_name(b["first_name"]) == pn]
            if len(hits) == 1:
                pick = hits[0]
        if pick:
            conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (pick["eid"], a["eid"]))
            conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (a["eid"], pick["eid"]))
            paid = a["paid"] or pick["paid"]
            if paid:
                conn.execute("UPDATE enrollments SET payment_completed_at = ? WHERE id IN (?, ?)", (paid, a["eid"], pick["eid"]))
            linked.update((a["eid"], pick["eid"]))
    if linked:
        conn.commit()


@crm.route("/cohorts/<int:cohort_id>")
@area_required("contacts")
def cohort_detail(cohort_id):
    _ensure_enrollment_extras()
    conn = get_db()
    try:
        _auto_link_couples(conn, cohort_id)
    except Exception as e:
        conn.rollback()
        print(f"auto-link couples skipped: {e}", flush=True)
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
    partner_names = {e["id"]: e["full_name"] for e in roster}
    # Keep each couple together (alphabetical by the first of them) and shade every other group,
    # so a long list (80+ trainees) is easy to scan.
    by_id = {e["id"]: e for e in roster}
    ordered, bands, placed, band = [], {}, set(), 0
    for e in roster:
        if e["id"] in placed:
            continue
        group = [e]
        pe = by_id.get(e["partner_enrollment_id"]) if e["partner_enrollment_id"] else None
        if pe and pe["id"] not in placed:
            group.append(pe)
        for g in group:
            placed.add(g["id"])
            bands[g["id"]] = band % 2
            ordered.append(g)
        band += 1
    roster = ordered
    small_groups = conn.execute("SELECT * FROM small_groups WHERE cohort_id = ? ORDER BY label", (cohort_id,)).fetchall()
    staffing = conn.execute(
        f"""SELECT cs.*, {full_name_sql('ct')} AS full_name
           FROM cohort_staffing cs
           JOIN contacts ct ON ct.id = cs.contact_id WHERE cs.cohort_id = ?""",
        (cohort_id,),
    ).fetchall()
    # Lead Facilitator and Support Facilitator always head the team list. Someone with
    # several roles/duties appears once, with everything combined.
    _role_rank = {"Lead Facilitator": 0, "Support Facilitator": 1, "TA": 3,
                  "Support Team": 4, "Back-up TA": 5, "Trainee": 6}
    _by_person = {}
    for r in staffing:
        _by_person.setdefault(r["contact_id"], []).append(r)
    team = []
    for cid, rows in _by_person.items():
        roles = []
        for r in sorted(rows, key=lambda r: _role_rank.get(r["role"], 9)):
            if r["role"] not in roles:
                roles.append(r["role"])
        team.append({
            "contact_id": cid,
            "full_name": rows[0]["full_name"],
            "roles": roles,
            "duties": [r for r in rows if r["duty"]],
            "rows": rows,
            "rank": min(_role_rank.get(r["role"], 9) for r in rows),
        })
    team.sort(key=lambda t: (t["rank"], (t["full_name"] or "").lower()))
    staffing = team
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
        roster=roster, partner_names=partner_names, bands=bands,
        small_groups=small_groups,
        staffing=staffing,
        show_session_team=_can_see_session_part("session_team"),
        show_trainee_roster=_can_see_session_part("trainee_roster"),
        session_roles=SESSION_ROLES,
        session_duties=SESSION_DUTIES,
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
        if brought_by:
            _fill_sponsor_card(conn, contact_id, brought_by)
        conn.commit()
        flash("Enrolled.")
    except Exception:
        flash("This contact is already enrolled in this session.")
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/enrollments/<int:enrollment_id>/update", methods=["POST"])
@area_required("contacts")
def enrollment_update(enrollment_id):
    _ensure_enrollment_extras()
    conn = get_db()
    enr = conn.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
    fields = {}
    if "questionnaire_done" in request.form:
        fields["questionnaire_completed_at"] = date.today().isoformat()
    if "payment_done" in request.form:
        fields["payment_completed_at"] = date.today().isoformat()
    if "attended" in request.form:
        fields["attended"] = 1
    # Undo buttons (in case something was marked by mistake)
    if "questionnaire_undo" in request.form:
        fields["questionnaire_completed_at"] = None
    if "payment_undo" in request.form:
        fields["payment_completed_at"] = None
    if "attended_undo" in request.form:
        fields["attended"] = 0
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
        if fields.get("enrolled_by_contact_id"):
            _fill_sponsor_card(conn, enr["contact_id"], fields["enrolled_by_contact_id"])
        # One payment covers the couple: marking (or undoing) it for one partner does the same for the other.
        if "payment_completed_at" in fields and enr["partner_enrollment_id"]:
            conn.execute("UPDATE enrollments SET payment_completed_at = ? WHERE id = ?",
                         (fields["payment_completed_at"], enr["partner_enrollment_id"]))
        conn.commit()
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=enr["cohort_id"]))


@crm.route("/enrollments/<int:enrollment_id>/partner", methods=["POST"])
@area_required("contacts")
def enrollment_partner(enrollment_id):
    """Joins two trainees of the same session as a couple (or separates them). Payment is shared."""
    _ensure_enrollment_extras()
    conn = get_db()
    enr = conn.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
    if not enr:
        conn.close()
        abort(404)
    # separate from the current partner first
    if enr["partner_enrollment_id"]:
        conn.execute("UPDATE enrollments SET partner_enrollment_id = NULL WHERE id IN (?, ?)",
                     (enrollment_id, enr["partner_enrollment_id"]))
    pid = request.form.get("partner_enrollment_id", type=int)
    if pid and pid != enrollment_id:
        other = conn.execute("SELECT * FROM enrollments WHERE id = ? AND cohort_id = ?", (pid, enr["cohort_id"])).fetchone()
        if other:
            if other["partner_enrollment_id"]:
                conn.execute("UPDATE enrollments SET partner_enrollment_id = NULL WHERE id = ?", (other["partner_enrollment_id"],))
            conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (pid, enrollment_id))
            conn.execute("UPDATE enrollments SET partner_enrollment_id = ? WHERE id = ?", (enrollment_id, pid))
            # if one has already paid, the couple has paid
            paid = enr["payment_completed_at"] or other["payment_completed_at"]
            if paid:
                conn.execute("UPDATE enrollments SET payment_completed_at = ? WHERE id IN (?, ?)", (paid, enrollment_id, pid))
    conn.commit()
    conn.close()
    return redirect(url_for(".cohort_detail", cohort_id=enr["cohort_id"]))


@crm.route("/enrollments/<int:enrollment_id>/remove", methods=["POST"])
@area_required("contacts")
@leadership_required
def enrollment_remove(enrollment_id):
    """Takes one person off a session's roster (their contact record is not touched).
    Refused only if they've already submitted feedback, which is kept."""
    conn = get_db()
    enr = conn.execute(
        f"""SELECT e.*, {full_name_sql('ct')} AS full_name FROM enrollments e
            JOIN contacts ct ON ct.id = e.contact_id WHERE e.id = ?""", (enrollment_id,)
    ).fetchone()
    if not enr:
        conn.close()
        flash("That person is no longer on the roster.")
        return redirect(url_for(".programs_list"))
    cohort_id = enr["cohort_id"]
    if conn.execute("SELECT 1 FROM feedback_responses WHERE enrollment_id = ?", (enrollment_id,)).fetchone():
        conn.close()
        flash(f"Can't remove {enr['full_name']} -- they've submitted a feedback form, which is kept on file.")
        return redirect(url_for(".cohort_detail", cohort_id=cohort_id))
    _ensure_completion_letters_table(conn)
    for tbl in ("training_profiles", "portal_links", "completion_letters"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE enrollment_id = ?", (enrollment_id,))
        except Exception:
            conn.rollback()  # that table doesn't exist here, so there is nothing to clear
    conn.execute("DELETE FROM enrollments WHERE id = ?", (enrollment_id,))
    conn.commit()
    conn.close()
    print(f"REMOVED {enr['full_name']} from cohort {cohort_id} by {session.get('staff_name')}", flush=True)
    flash(f"Removed {enr['full_name']} from this session's roster. Their contact record is unchanged.")
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


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


RELATIONSHIP_PROFILE_QUESTIONS = [
    ("first_time_attending", "Is this your first time attending the Relationship Training?"),
    ("training_goals", "What do you want to get out of the Relationship Training?"),
    ("relationship_length", "How long have you been in your relationship?"),
    ("how_met", "How did you meet your partner and what attracted you to them?"),
    ("childhood_patterns", "Does this relationship have patterns similar to what you experienced with your parents in your childhood?"),
    ("emotional_milestone", "Describe a significant emotional milestone within your relationship."),
    ("relationship_strengths", "What are the strengths within the relationship?"),
    ("relationship_weaknesses", "What are the weaknesses within the relationship?"),
    ("stress_points", "Are there any stress points within the relationship? Please explain."),
    ("song_artist", "List one song and artist that describes the way you feel about your partner."),
    ("song_feelings", "Briefly describe how the song above makes you feel toward your partner."),
    ("other_info", "Please provide other information you believe is important to this relationship."),
]


def _relationship_profiles_for(conn, contact_id):
    """One entry per Relationship Training (D4) enrollment of this contact, with the profile answers and a
    status: Not started / In progress (n of 12) / Complete."""
    out = []
    rows = conn.execute(
        """SELECT e.id AS enrollment_id, k.session_number, k.session_date, k.end_date, p.name AS program_name
           FROM enrollments e JOIN cohorts k ON k.id = e.cohort_id JOIN programs p ON p.id = k.program_id
           WHERE e.contact_id = ? AND p.code = 'D4' ORDER BY k.session_date DESC""", (contact_id,)).fetchall()
    for r in rows:
        prof = conn.execute("SELECT * FROM training_profiles WHERE enrollment_id = ?", (r["enrollment_id"],)).fetchone()
        answers = [(label, (prof[f] if prof and prof[f] else "")) for f, label in RELATIONSHIP_PROFILE_QUESTIONS]
        n = sum(1 for _l, v in answers if v)
        status = "Not started" if n == 0 else ("Complete" if n == len(answers) else f"In progress ({n} of {len(answers)})")
        out.append({"enrollment_id": r["enrollment_id"], "session_number": r["session_number"],
                    "session_date": r["session_date"], "end_date": r["end_date"], "status": status, "answers": answers})
    return out


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


@crm.route("/cohorts/<int:cohort_id>/staffing/<int:staffing_id>/remove", methods=["POST"])
@area_required("contacts")
@leadership_required
def staffing_remove(cohort_id, staffing_id):
    conn = get_db()
    conn.execute("DELETE FROM cohort_staffing WHERE id = ? AND cohort_id = ?", (staffing_id, cohort_id))
    conn.commit()
    conn.close()
    flash("Removed from the session team.")
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


SESSION_ROLES = ["Lead Facilitator", "Support Facilitator", "TA", "Support Team", "Back-up TA", "Trainee"]
SESSION_DUTIES = ["Sound Equipment", "Large Group Leader", "Team Captain", "Microphone 1", "Microphone 2",
                  "Lights", "Doors", "Signs / Security", "Mail & Name Tags", "Housekeeping", "Male Runner",
                  "Female Runner", "Time Keeper", "Chairs", "Chair Counter", "Troubleshooter / Misc", "Not confirmed"]


# ---------- session letters (confirmations to trainees / facilitators / TAs / back-up TAs) ----------

def _ensure_session_letters_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS session_letters_sent (
        id SERIAL PRIMARY KEY,
        cohort_id INTEGER NOT NULL,
        contact_id INTEGER NOT NULL,
        letter_key TEXT NOT NULL,
        sent_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
        sent_by TEXT,
        UNIQUE (cohort_id, contact_id, letter_key)
    )""")
    conn.commit()


def _session_letters_live():
    """Sending to real people stays OFF until this is set to 1 in Render's Environment."""
    return (os.environ.get("SESSION_LETTERS_LIVE") or "").strip() == "1"


def _session_letter_recipients(conn, cohort_id):
    """{letter_key: [ {contact_id, name, first_name, email, roles, duties}, ... ]}.
    Support Team is deliberately not included. A person with several roles gets one letter,
    the highest: Facilitator, then TA, then Back-up TA."""
    _ensure_enrollment_extras()
    try:
        _auto_link_couples(conn, cohort_id)
    except Exception as e:
        conn.rollback()
        print(f"auto-link couples skipped: {e}", flush=True)
    out = {k: [] for k in SESSION_LETTER_ORDER}
    rows = conn.execute(
        f"""SELECT e.id AS enrollment_id, e.partner_enrollment_id AS partner_eid,
                   ct.id AS contact_id, ct.first_name, ct.email, {full_name_sql('ct')} AS name,
                   e.questionnaire_completed_at AS q_at, e.payment_completed_at AS p_at
            FROM enrollments e JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.cohort_id = ? ORDER BY ct.last_name, ct.first_name""", (cohort_id,)).fetchall()
    names_by_eid = {r["enrollment_id"]: r["name"] for r in rows}
    for r in rows:
        person = {"contact_id": r["contact_id"], "enrollment_id": r["enrollment_id"], "name": r["name"],
                  "first_name": r["first_name"], "email": r["email"], "roles": [], "duties": [],
                  "partner_name": names_by_eid.get(r["partner_eid"])}
        for k in SESSION_TRAINEE_LETTERS:
            if k == "payment_received" and not r["p_at"]:
                continue
            if k == "payment_reminder" and r["p_at"]:
                continue
            if k == "questionnaire_reminder" and r["q_at"]:
                continue
            out[k].append(person)
    people = {}
    for r in conn.execute(
        f"""SELECT cs.role, cs.duty, ct.id AS contact_id, ct.first_name, ct.email, {full_name_sql('ct')} AS name
            FROM cohort_staffing cs JOIN contacts ct ON ct.id = cs.contact_id
            WHERE cs.cohort_id = ?""", (cohort_id,)).fetchall():
        p = people.setdefault(r["contact_id"], {"contact_id": r["contact_id"], "name": r["name"],
                                                 "first_name": r["first_name"], "email": r["email"],
                                                 "roles": [], "duties": []})
        if r["role"] not in p["roles"]:
            p["roles"].append(r["role"])
        if r["duty"] and r["duty"] not in p["duties"]:
            p["duties"].append(r["duty"])
    for p in sorted(people.values(), key=lambda p: (p["name"] or "").lower()):
        if "Lead Facilitator" in p["roles"] or "Support Facilitator" in p["roles"]:
            out["facilitator_confirm"].append(p)
        elif "TA" in p["roles"]:
            out["ta_confirm"].append(p)
        elif "Back-up TA" in p["roles"]:
            out["backup_confirm"].append(p)
    return out


def _personal_link(contact_id):
    """The person's private link to the profile form (made once, reused). None for a sample person."""
    if not contact_id:
        return None
    conn = get_db()
    row = conn.execute("SELECT profile_token FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    token = row["profile_token"] if row else None
    if row and not token:
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (token, contact_id))
        conn.commit()
    conn.close()
    return url_for("public_complete_profile", token=token, _external=True) if token else None


def _questionnaire_link(enrollment_id):
    """The trainee's private link to their own questionnaire (made once, reused). None for a sample person."""
    if not enrollment_id:
        return None
    _ensure_enrollment_extras()
    conn = get_db()
    row = conn.execute("SELECT questionnaire_token FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
    token = row["questionnaire_token"] if row else None
    if row and not token:
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE enrollments SET questionnaire_token = ? WHERE id = ?", (token, enrollment_id))
        conn.commit()
    conn.close()
    return url_for("public_questionnaire", token=token, _external=True) if token else None


def _letter_link(person, key):
    if key == "questionnaire_reminder":
        return _questionnaire_link(person.get("enrollment_id"))
    if key == "seminar_registration":
        return _personal_link(person["contact_id"])
    return None


def _session_letter_for(cohort, person, key):
    code, name = cohort["code"], cohort["name"]
    training = name if name.startswith(code) else f"{code}-{name}"
    role = "Lead Facilitator" if "Lead Facilitator" in person["roles"] else "Support Facilitator"
    return session_letter_content(
        key, person["first_name"], training, date_span(cohort), cohort["location"] if "location" in cohort.keys() else None,
        role=role, duties=person["duties"],
        link=_letter_link(person, key),
        partner=person.get("partner_name"),
        amount=("$1,200 per couple" if code == "D4" else None))


def _letter_cohort(conn, cohort_id):
    cohort = conn.execute(
        """SELECT c.*, p.code, p.name FROM cohorts c JOIN programs p ON p.id = c.program_id WHERE c.id = ?""",
        (cohort_id,)).fetchone()
    if not cohort:
        abort(404)
    return cohort


@crm.route("/cohorts/<int:cohort_id>/letters")
@area_required("contacts")
@leadership_required
def session_letters(cohort_id):
    _ensure_cohort_location()
    conn = get_db()
    _ensure_session_letters_table(conn)
    cohort = _letter_cohort(conn, cohort_id)
    recipients = _session_letter_recipients(conn, cohort_id)
    sent = {(r["contact_id"], r["letter_key"]): r["sent_at"] for r in conn.execute(
        "SELECT contact_id, letter_key, sent_at FROM session_letters_sent WHERE cohort_id = ?", (cohort_id,)).fetchall()}
    conn.close()
    return render_template("session_letters.html", cohort=cohort, letters=SESSION_LETTERS, order=SESSION_LETTER_ORDER, trainee_keys=SESSION_TRAINEE_LETTERS,
                           recipients=recipients, sent=sent, live=_session_letters_live())


@crm.route("/cohorts/<int:cohort_id>/letters/<key>/preview/<int:contact_id>")
@area_required("contacts")
@leadership_required
def session_letter_preview(cohort_id, key, contact_id):
    if key not in SESSION_LETTERS:
        abort(404)
    conn = get_db()
    cohort = _letter_cohort(conn, cohort_id)
    person = next((p for p in _session_letter_recipients(conn, cohort_id)[key] if p["contact_id"] == contact_id), None)
    conn.close()
    if not person:
        abort(404)
    subject, html_content, _t = _session_letter_for(cohort, person, key)
    return render_template("session_letter_preview.html", cohort=cohort, key=key, spec=SESSION_LETTERS[key],
                           person=person, subject=subject, letter_html=html_content, live=_session_letters_live())


@crm.route("/cohorts/<int:cohort_id>/letters/<key>/view")
@area_required("contacts")
@leadership_required
def session_letter_view(cohort_id, key):
    """Shows the letter itself (with [First name] as a stand-in), for reading and revising. Sends nothing."""
    if key not in SESSION_LETTERS:
        abort(404)
    conn = get_db()
    cohort = _letter_cohort(conn, cohort_id)
    conn.close()
    sample = {"contact_id": 0, "name": "", "first_name": "[First name]", "email": "", "roles": ["Lead Facilitator"], "duties": []}
    subject, html_content, _t = _session_letter_for(cohort, sample, key)
    return render_template("session_letter_view.html", cohort=cohort, spec=SESSION_LETTERS[key],
                           subject=subject, letter_html=html_content)


def _staff_email():
    conn = get_db()
    row = conn.execute("SELECT email FROM staff WHERE id = ?", (session.get("staff_id"),)).fetchone()
    conn.close()
    return row["email"] if row else None


@crm.route("/cohorts/<int:cohort_id>/letters/<key>/test", methods=["POST"])
@area_required("contacts")
@leadership_required
def session_letter_test(cohort_id, key):
    """Sends the letter, as written for the chosen person, ONLY to the signed-in staff member."""
    if key not in SESSION_LETTERS:
        abort(404)
    contact_id = request.form.get("contact_id", type=int)
    conn = get_db()
    cohort = _letter_cohort(conn, cohort_id)
    person = next((p for p in _session_letter_recipients(conn, cohort_id)[key] if p["contact_id"] == contact_id), None)
    conn.close()
    me = _staff_email()
    if not person or not me:
        flash("Couldn't send a test copy.")
        return redirect(url_for(".session_letters", cohort_id=cohort_id))
    subject, html_content, text_content = _session_letter_for(cohort, person, key)
    try:
        send_email(to_email=me, subject="[TEST] " + subject, html_content=html_content, text_content=text_content)
        flash(f"Test copy sent to you at {me}. Nothing was sent to {person['first_name']}.")
    except EmailSendError as e:
        flash(f"Couldn't send the test copy: {e}")
    return redirect(url_for(".session_letter_preview", cohort_id=cohort_id, key=key, contact_id=contact_id))


@crm.route("/cohorts/<int:cohort_id>/letters/<key>/send", methods=["POST"])
@area_required("contacts")
@leadership_required
def session_letter_send(cohort_id, key):
    """Sends the letter to the ticked people, once each. OFF until SESSION_LETTERS_LIVE=1."""
    if key not in SESSION_LETTERS:
        abort(404)
    if not _session_letters_live():
        flash("Sending to people is switched off for now. You can preview any letter and send yourself a test copy.")
        return redirect(url_for(".session_letters", cohort_id=cohort_id))
    ids = {int(x) for x in request.form.getlist("contact_id") if x.isdigit()}
    conn = get_db()
    _ensure_session_letters_table(conn)
    cohort = _letter_cohort(conn, cohort_id)
    people = [p for p in _session_letter_recipients(conn, cohort_id)[key] if p["contact_id"] in ids]
    sent_n, skipped = 0, []
    for p in people:
        if not p["email"]:
            skipped.append(f"{p['name']} (no email)")
            continue
        claim = conn.execute(
            """INSERT INTO session_letters_sent (cohort_id, contact_id, letter_key, sent_by) VALUES (?,?,?,?)
               ON CONFLICT (cohort_id, contact_id, letter_key) DO NOTHING""",
            (cohort_id, p["contact_id"], key, session.get("staff_name")))
        conn.commit()
        if not claim.rowcount:
            skipped.append(f"{p['name']} (already sent)")
            continue
        subject, html_content, text_content = _session_letter_for(cohort, p, key)
        try:
            send_email(to_email=p["email"], to_name=p["first_name"], subject=subject,
                       html_content=html_content, text_content=text_content)
            sent_n += 1
            print(f"SENT session letter {key} to contact {p['contact_id']} cohort {cohort_id} by {session.get('staff_name')}", flush=True)
        except EmailSendError as e:
            conn.execute("DELETE FROM session_letters_sent WHERE cohort_id = ? AND contact_id = ? AND letter_key = ?",
                         (cohort_id, p["contact_id"], key))
            conn.commit()
            skipped.append(f"{p['name']} (failed: {e})")
    conn.close()
    msg = f"Sent {sent_n} letter(s)."
    if skipped:
        msg += " Not sent: " + "; ".join(skipped) + "."
    flash(msg)
    return redirect(url_for(".session_letters", cohort_id=cohort_id))


@crm.route("/cohorts/<int:cohort_id>/team/print")
@area_required("contacts")
def cohort_team_print(cohort_id):
    """Plain, printable Session Team sheet: facilitators first, one line per person."""
    _ensure_cohort_location()
    conn = get_db()
    cohort = conn.execute(
        """SELECT c.*, p.code, p.name FROM cohorts c JOIN programs p ON p.id = c.program_id WHERE c.id = ?""",
        (cohort_id,),
    ).fetchone()
    if not cohort:
        conn.close()
        flash("That session no longer exists.")
        return redirect(url_for(".programs_list"))
    rows = conn.execute(
        f"""SELECT cs.role, cs.duty, ct.id AS contact_id, {full_name_sql('ct')} AS full_name,
                   ct.cell_phone, ct.email
            FROM cohort_staffing cs JOIN contacts ct ON ct.id = cs.contact_id
            WHERE cs.cohort_id = ?""", (cohort_id,)).fetchall()
    conn.close()
    rank = {"Lead Facilitator": 0, "Support Facilitator": 1, "TA": 3, "Support Team": 4, "Back-up TA": 5, "Trainee": 6}
    people = {}
    for r in rows:
        p = people.setdefault(r["contact_id"], {"name": r["full_name"], "cell": r["cell_phone"], "email": r["email"],
                                                  "roles": [], "duties": []})
        if r["role"] not in p["roles"]:
            p["roles"].append(r["role"])
        if r["duty"] and r["duty"] not in p["duties"]:
            p["duties"].append(r["duty"])
    team = sorted(people.values(), key=lambda p: (min(rank.get(x, 9) for x in p["roles"]), (p["name"] or "").lower()))
    for p in team:
        p["roles"].sort(key=lambda x: rank.get(x, 9))
    return render_template("team_print.html", cohort=cohort, team=team, printed=date.today().isoformat())


@crm.route("/cohorts/<int:cohort_id>/staffing/person/<int:contact_id>/modify", methods=["POST"])
@area_required("contacts")
@leadership_required
def staffing_modify_person(cohort_id, contact_id):
    """Replaces one person's roles and duties on this session with what was ticked."""
    roles = [r for r in request.form.getlist("role") if r in SESSION_ROLES]
    roles.sort(key=SESSION_ROLES.index)
    duties = [d.strip() for d in request.form.getlist("duty") if d.strip()]
    extra = (request.form.get("extra_duty") or "").strip()
    if extra and extra not in duties:
        duties.append(extra)
    if not roles:
        flash("Tick at least one role. (To take them off the team, use Remove.)")
        return redirect(url_for(".cohort_detail", cohort_id=cohort_id))
    conn = get_db()
    conn.execute("DELETE FROM cohort_staffing WHERE cohort_id = ? AND contact_id = ?", (cohort_id, contact_id))
    # duties ride on TA when they are one, otherwise on their first role
    home = "TA" if "TA" in roles else roles[0]
    for role in roles:
        if role == home and duties:
            for d in duties:
                conn.execute("INSERT INTO cohort_staffing (cohort_id, contact_id, role, duty) VALUES (?,?,?,?)",
                             (cohort_id, contact_id, role, d))
        else:
            conn.execute("INSERT INTO cohort_staffing (cohort_id, contact_id, role, duty) VALUES (?,?,?,NULL)",
                         (cohort_id, contact_id, role))
    conn.commit()
    conn.close()
    flash("Updated their roles and duties.")
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/cohorts/<int:cohort_id>/staffing/person/<int:contact_id>/remove", methods=["POST"])
@area_required("contacts")
@leadership_required
def staffing_remove_person(cohort_id, contact_id):
    conn = get_db()
    conn.execute("DELETE FROM cohort_staffing WHERE cohort_id = ? AND contact_id = ?", (cohort_id, contact_id))
    conn.commit()
    conn.close()
    flash("Removed from the session team.")
    return redirect(url_for(".cohort_detail", cohort_id=cohort_id))


@crm.route("/cohorts/<int:cohort_id>/group-staffing/<int:staffing_id>/remove", methods=["POST"])
@area_required("contacts")
@leadership_required
def sg_staffing_remove(cohort_id, staffing_id):
    conn = get_db()
    conn.execute(
        """DELETE FROM small_group_staffing WHERE id = ? AND small_group_id IN
           (SELECT id FROM small_groups WHERE cohort_id = ?)""", (staffing_id, cohort_id))
    conn.commit()
    conn.close()
    flash("Removed from the small group.")
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
        """SELECT cid, COUNT(DISTINCT trainee) AS n,
                  COUNT(DISTINCT CASE WHEN att = 1 THEN trainee END) AS attended
           FROM (
               SELECT enrolled_by_contact_id AS cid, contact_id AS trainee, attended AS att
               FROM enrollments
               WHERE enrolled_by_contact_id IS NOT NULL AND contact_id != enrolled_by_contact_id
               UNION ALL
               SELECT c.sponsor_contact_id, c.id,
                      CASE WHEN EXISTS (SELECT 1 FROM enrollments e WHERE e.contact_id = c.id AND e.attended = 1)
                           THEN 1 ELSE 0 END
               FROM contacts c
               WHERE c.sponsor_contact_id IS NOT NULL AND c.sponsor_contact_id != c.id
           ) x
           GROUP BY cid"""
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


# ---------- contact lists ("where this contact came from") ----------

@crm.route("/lists")
@area_required("contacts")
@leadership_required
def lists_manage():
    conn = get_db()
    lists = conn.execute(
        """SELECT l.id, l.name, l.created_at, COUNT(m.id) AS n
           FROM contact_lists l LEFT JOIN contact_list_members m ON m.list_id = l.id
           GROUP BY l.id, l.name, l.created_at ORDER BY l.name"""
    ).fetchall()
    conn.close()
    return render_template("lists_manage.html", lists=lists)


@crm.route("/lists/new", methods=["POST"])
@area_required("contacts")
@leadership_required
def list_new():
    name = " ".join((request.form.get("name") or "").split())
    if not name:
        flash("Please type a name for the list.")
        return redirect(url_for(".lists_manage"))
    conn = get_db()
    if conn.execute("SELECT 1 FROM contact_lists WHERE LOWER(name) = LOWER(?)", (name,)).fetchone():
        flash(f'There is already a list called "{name}".')
    else:
        conn.execute("INSERT INTO contact_lists (name, created_at) VALUES (?, ?)", (name, date.today().isoformat()))
        conn.commit()
        flash(f'Created the list "{name}".')
    conn.close()
    return redirect(url_for(".lists_manage"))


@crm.route("/lists/<int:list_id>/rename", methods=["POST"])
@area_required("contacts")
@leadership_required
def list_rename(list_id):
    name = " ".join((request.form.get("name") or "").split())
    conn = get_db()
    if not name:
        flash("Please type a name for the list.")
    elif conn.execute(
        "SELECT 1 FROM contact_lists WHERE LOWER(name) = LOWER(?) AND id != ?", (name, list_id)
    ).fetchone():
        flash(f'There is already a list called "{name}".')
    else:
        conn.execute("UPDATE contact_lists SET name = ? WHERE id = ?", (name, list_id))
        conn.commit()
        flash("Renamed.")
    conn.close()
    return redirect(url_for(".lists_manage"))


@crm.route("/lists/<int:list_id>/delete", methods=["POST"])
@area_required("contacts")
@leadership_required
def list_delete(list_id):
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM contact_list_members WHERE list_id = ?", (list_id,)).fetchone()["n"]
    if n:
        flash(f"That list still has {n} contact(s) on it, so it can't be deleted.")
    else:
        conn.execute("DELETE FROM contact_lists WHERE id = ?", (list_id,))
        conn.commit()
        flash("List deleted.")
    conn.close()
    return redirect(url_for(".lists_manage"))


@crm.route("/contacts/<int:contact_id>/lists/add", methods=["POST"])
@area_required("contacts")
@leadership_required
def contact_list_add(contact_id):
    list_id = request.form.get("list_id", type=int)
    conn = get_db()
    if list_id and conn.execute("SELECT 1 FROM contact_lists WHERE id = ?", (list_id,)).fetchone():
        if not conn.execute(
            "SELECT 1 FROM contact_list_members WHERE contact_id = ? AND list_id = ?", (contact_id, list_id)
        ).fetchone():
            conn.execute(
                "INSERT INTO contact_list_members (contact_id, list_id, added_at) VALUES (?, ?, ?)",
                (contact_id, list_id, date.today().isoformat()),
            )
            conn.commit()
    conn.close()
    return redirect(url_for(".contact_detail", contact_id=contact_id))


@crm.route("/contacts/<int:contact_id>/lists/<int:list_id>/remove", methods=["POST"])
@area_required("contacts")
@leadership_required
def contact_list_remove(contact_id, list_id):
    conn = get_db()
    conn.execute("DELETE FROM contact_list_members WHERE contact_id = ? AND list_id = ?", (contact_id, list_id))
    conn.commit()
    conn.close()
    return redirect(url_for(".contact_detail", contact_id=contact_id))


# ---------- calendar ----------

def _short_date(d):
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}"


def _short_span(start, last):
    """'June 11\u201313', 'July 30\u2013Aug. 1', or just 'June 12' for a one-day session."""
    if last == start:
        return _short_date(start)
    if last.month == start.month:
        return f"{_short_date(start)}\u2013{last.day}"
    return f"{_short_date(start)}\u2013{_short_date(last)}"


CALENDAR_COLORS = {
    "D1": "#3f7d4f", "D2": "#c9a13b", "D3": "#3a78b5",
    "D4": "#8a5aa8", "D5": "#d9822b", "D6": "#2a9d9a",
}


def _holidays_for_year(year):
    """Holidays shown on the calendar, worked out by rule (no outside service).
    Returns (days, listing): days maps each date to (name, kind) with kind 'us', 'christian' or 'jewish';
    listing is [(date, label, kind)] with multi-day holidays listed once, on their first day."""
    import calendar as _c
    days, listing = {}, []

    def add(d, name, kind, span=1, label=None):
        for i in range(span):
            days[d + timedelta(days=i)] = (name, kind)
        listing.append((d, label or name, kind))

    def nth(month, weekday, n):
        first = date(year, month, 1)
        return date(year, month, 1 + (weekday - first.weekday()) % 7 + 7 * (n - 1))

    def last(month, weekday):
        d = date(year, month, _c.monthrange(year, month)[1])
        return d - timedelta(days=(d.weekday() - weekday) % 7)

    def easter(y):
        a, b, c = y % 19, y // 100, y % 100
        d, e = b // 4, b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = c // 4, c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        return date(y, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)

    # ---- United States ----
    thanks = nth(11, 3, 4)
    for d, n in [
        (date(year, 1, 1), "New Year's Day"), (nth(1, 0, 3), "Martin Luther King Jr. Day"),
        (date(year, 2, 2), "Groundhog Day"), (date(year, 2, 14), "Valentine's Day"),
        (nth(2, 0, 3), "Presidents' Day"), (date(year, 3, 17), "St. Patrick's Day"),
        (nth(5, 6, 2), "Mother's Day"), (last(5, 0), "Memorial Day"), (nth(6, 6, 3), "Father's Day"),
        (date(year, 6, 19), "Juneteenth"), (date(year, 7, 4), "Independence Day"), (nth(9, 0, 1), "Labor Day"),
        (nth(10, 0, 2), "Columbus Day"), (date(year, 10, 31), "Halloween"), (date(year, 11, 11), "Veterans Day"),
        (thanks, "Thanksgiving"), (thanks + timedelta(days=1), "Day after Thanksgiving"),
        (date(year, 12, 31), "New Year's Eve"),
    ]:
        add(d, n, "us")

    # ---- Christian (Western churches) ----
    e = easter(year)
    xmas = date(year, 12, 25)
    advent = xmas - timedelta(days=(xmas.weekday() + 1) % 7 or 7) - timedelta(days=21)   # 4th Sunday before Christmas
    for d, n in [
        (date(year, 1, 6), "Epiphany"), (e - timedelta(days=46), "Ash Wednesday"),
        (e - timedelta(days=7), "Palm Sunday"), (e - timedelta(days=3), "Maundy Thursday"),
        (e - timedelta(days=2), "Good Friday"), (e, "Easter"), (e + timedelta(days=39), "Ascension Day"),
        (e + timedelta(days=49), "Pentecost"), (date(year, 11, 1), "All Saints' Day"),
        (advent, "First Sunday of Advent"), (date(year, 12, 24), "Christmas Eve"), (xmas, "Christmas Day"),
    ]:
        add(d, n, "christian")

    # ---- Jewish (dates are for the day itself; each holiday begins at sundown the evening before) ----
    EPOCH = -1373427

    def elapsed(y):
        me = (235 * y - 234) // 19
        day = 29 * me + (12084 + 13753 * me) // 25920
        return day + 1 if (3 * (day + 1)) % 7 < 3 else day

    def new_year(y):
        a, b, c = elapsed(y - 1), elapsed(y), elapsed(y + 1)
        return EPOCH + b + (2 if c - b == 356 else (1 if b - a == 382 else 0))

    def hebrew_months(y):
        """{month name: first date} for Hebrew year y (Tishri-based)."""
        start = new_year(y)
        length = new_year(y + 1) - start
        leap = (7 * y + 1) % 19 < 7
        sizes = [("Tishri", 30), ("Cheshvan", 30 if length % 10 == 5 else 29),
                 ("Kislev", 29 if length % 10 == 3 else 30), ("Tevet", 29), ("Shevat", 30)]
        if leap:
            sizes.append(("Adar I", 30))
        sizes += [("Adar", 29), ("Nisan", 30), ("Iyar", 29), ("Sivan", 30), ("Tammuz", 29), ("Av", 30), ("Elul", 29)]
        out, cur = {}, start
        for name, n in sizes:
            out[name] = date.fromordinal(cur)
            cur += n
        return out

    jewish = []
    for hy in (year + 3760, year + 3761):
        m = hebrew_months(hy)
        t = m["Tishri"]
        jewish += [
            (t, "Rosh Hashanah", 2, "Rosh Hashanah (2 days)"), (t + timedelta(days=9), "Yom Kippur", 1, None),
            (t + timedelta(days=14), "Sukkot", 7, "Sukkot (7 days)"), (t + timedelta(days=21), "Shemini Atzeret", 1, None),
            (t + timedelta(days=22), "Simchat Torah", 1, None),
            (m["Kislev"] + timedelta(days=24), "Hanukkah", 8, "Hanukkah (8 days)"),
            (m["Shevat"] + timedelta(days=14), "Tu BiShvat", 1, None),
            (m["Adar"] + timedelta(days=13), "Purim", 1, None),
            (m["Nisan"] + timedelta(days=14), "Passover", 8, "Passover (8 days)"),
            (m["Sivan"] + timedelta(days=5), "Shavuot", 2, "Shavuot (2 days)"),
            (m["Av"] + timedelta(days=8), "Tisha B'Av", 1, None),
        ]
    for d, n, span, label in jewish:
        if d.year == year or (d + timedelta(days=span - 1)).year == year:
            for i in range(span):
                day = d + timedelta(days=i)
                if day.year == year:
                    days[day] = (n, "jewish")
            if d.year == year:
                listing.append((d, label or n, "jewish"))
    listing.sort(key=lambda x: x[0])
    return days, listing


def _calendar_data(year, public=False):
    """Everything the year calendar needs. public=True is the visitor-facing
    version: cancelled sessions are left out and nothing links to a staff page."""
    import calendar as _cal
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        """SELECT ch.id, ch.session_number, ch.session_date, ch.end_date, ch.status, p.code, p.name
           FROM cohorts ch JOIN programs p ON p.id = ch.program_id
           WHERE ch.session_date <= ? AND COALESCE(ch.end_date, ch.session_date) >= ?
           ORDER BY ch.session_date, p.code""",
        (f"{year}-12-31", f"{year}-01-01"),
    ).fetchall()]
    conn.close()
    if public:
        rows = [r for r in rows if r["status"] != "Cancelled"]
    for r in rows:
        r["href"] = None if public else url_for(".cohort_detail", cohort_id=r["id"])
        r["span"] = date_span(r)

    # sessions that run on the same dates (e.g. D1, D2 and D3 on one weekend)
    # are grouped so the weekend can be called out as one event
    groups_by_key = {}
    for r in rows:
        try:
            start = date.fromisoformat(r["session_date"])
            last = date.fromisoformat(r["end_date"]) if r["end_date"] else start
        except (TypeError, ValueError):
            continue
        g = groups_by_key.setdefault((start, last), {"start": start, "last": last, "sessions": []})
        g["sessions"].append(r)
    groups = sorted(groups_by_key.values(), key=lambda g: (g["start"], g["last"]))
    for g in groups:
        g["sessions"].sort(key=lambda r: r["code"])
        g["label"] = _short_span(g["start"], g["last"])
        g["start_short"] = _short_date(g["start"])

    # every day a group covers (first day through last day), within this year
    by_day = {}
    for g in groups:
        d = g["start"]
        while d <= g["last"]:
            if d.year == year:
                by_day.setdefault(d.isoformat(), []).append(g)
            d += timedelta(days=1)

    holidays, holiday_listing = _holidays_for_year(year)

    def day_cell(d, m):
        iso = f"{year}-{m:02d}-{d:02d}"
        cell = {"day": d, "iso": iso, "today": iso == date.today().isoformat(), "sessions": [],
                "holiday": (holidays.get(date(year, m, d)) or (None, None))[0],
                "hkind": (holidays.get(date(year, m, d)) or (None, None))[1]}
        found = by_day.get(iso, [])
        if not found:
            return cell
        sessions = [r for g in found for r in g["sessions"]]
        colors = [CALENDAR_COLORS.get(r["code"], "#777777") for r in sessions]
        colors = list(dict.fromkeys(colors))
        if len(colors) == 1:
            bg = colors[0]
        else:
            step = 100 / len(colors)
            bg = "conic-gradient(" + ", ".join(
                f"{c} {i * step:.1f}% {(i + 1) * step:.1f}%" for i, c in enumerate(colors)
            ) + ")"
        cell.update({
            "sessions": sessions,
            "bg": bg,
            "multi_day": any(g["last"] != g["start"] for g in found),
            "cancelled": all(r["status"] == "Cancelled" for r in sessions),
            "href": sessions[0]["href"],
            "title": "; ".join(
                (f"{r['code']} #{r['session_number']} " if not public else f"{r['code']} ")
                + r["span"] + ("" if public else f" ({r['status']})")
                for r in sessions
            ) + (f"; {cell['holiday']}" if cell["holiday"] else ""),
        })
        return cell

    cal = _cal.Calendar(firstweekday=6)  # weeks start on Sunday
    months = []
    for m in range(1, 13):
        weeks = [
            [None if d == 0 else day_cell(d, m) for d in week]
            for week in cal.monthdayscalendar(year, m)
        ]
        months.append({
            "name": _cal.month_name[m],
            "weeks": weeks,
            "opens": [g for g in groups if g["start"].year == year and g["start"].month == m],
            "holidays": [(h.day, name, kind) for h, name, kind in holiday_listing if h.month == m],
        })
    return {
        "year": year, "months": months, "groups": groups, "colors": CALENDAR_COLORS,
        "codes": sorted({r["code"] for r in rows}),
        "names": {r["code"]: r["name"] for r in rows},
        "public": public,
    }


@crm.route("/calendar")
@area_required("contacts")
def calendar_view():
    """Year-at-a-glance calendar of every scheduled training session, twelve
    small months with each session date marked in its program's color and
    linked to that session. Read-only -- sessions are still scheduled on the
    Programs page."""
    year = request.args.get("year", type=int) or date.today().year
    year = max(2000, min(2100, year))
    cal = _calendar_data(year, public=False)
    return render_template(
        "calendar.html", cal=cal,
        prev_url=url_for(".calendar_view", year=year - 1),
        next_url=url_for(".calendar_view", year=year + 1),
        today_url=url_for(".calendar_view"),
    )


# ---------- bulk "brought in by" entry ----------

BROUGHT_IN_PAGE_LIMIT = 300


@crm.route("/brought-in-by", methods=["GET", "POST"])
@area_required("contacts")
def brought_in_by_bulk():
    """One screen to fill in 'Brought in by' for many past trainees at once.
    Lists enrollments with no one recorded yet (or all, with ?show=all),
    optionally for a single cohort. Saving applies every row that has a name
    picked; blank rows are left alone, never cleared."""
    conn = get_db()
    cohort_id = request.values.get("cohort_id", type=int)
    show_all = request.values.get("show") == "all"

    if request.method == "POST":
        saved, problems = 0, []
        for key, raw in request.form.items():
            if not key.startswith("pick_") or not (raw or "").strip():
                continue
            try:
                enr_id = int(key[5:])
            except ValueError:
                continue
            enr = conn.execute("SELECT * FROM enrollments WHERE id = ?", (enr_id,)).fetchone()
            if not enr:
                continue
            brought_by, err = parse_contact_pick(conn, raw)
            who = conn.execute(
                f"SELECT {FULL_NAME_SQL} AS n FROM contacts WHERE id = ?", (enr["contact_id"],)
            ).fetchone()
            who = who["n"] if who else f"#{enr_id}"
            if err:
                problems.append(f"{who}: {err}")
            elif brought_by == enr["contact_id"]:
                problems.append(f"{who}: can't be recorded as bringing themselves in.")
            elif brought_by != enr["enrolled_by_contact_id"]:
                conn.execute(
                    "UPDATE enrollments SET enrolled_by_contact_id = ? WHERE id = ?", (brought_by, enr_id)
                )
                _fill_sponsor_card(conn, enr["contact_id"], brought_by)
                saved += 1
        conn.commit()
        if saved:
            flash(f"Saved {saved} {'entry' if saved == 1 else 'entries'}.")
        elif not problems:
            flash("Nothing to save -- pick a name in at least one row.")
        for p in problems[:10]:
            flash("Not saved -- " + p)
        if len(problems) > 10:
            flash(f"...and {len(problems) - 10} more not saved.")
        conn.close()
        return redirect(url_for(".brought_in_by_bulk", cohort_id=cohort_id or None,
                                show="all" if show_all else None))

    where, params = [], []
    if cohort_id:
        where.append("e.cohort_id = ?")
        params.append(cohort_id)
    if not show_all:
        where.append("e.enrolled_by_contact_id IS NULL")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT e.id, e.enrolled_by_contact_id, {full_name_sql('c')} AS trainee,
                   p.code AS program_code, ch.session_number, ch.session_date,
                   {full_name_sql('b')} AS brought_name
            FROM enrollments e
            JOIN contacts c ON c.id = e.contact_id
            JOIN cohorts ch ON ch.id = e.cohort_id
            JOIN programs p ON p.id = ch.program_id
            LEFT JOIN contacts b ON b.id = e.enrolled_by_contact_id
            {where_sql}
            ORDER BY ch.session_date DESC, ch.id DESC, c.last_name, c.first_name
            LIMIT {BROUGHT_IN_PAGE_LIMIT + 1}""",
        params,
    ).fetchall()
    truncated = len(rows) > BROUGHT_IN_PAGE_LIMIT
    rows = rows[:BROUGHT_IN_PAGE_LIMIT]
    missing_total = conn.execute(
        "SELECT COUNT(*) AS n FROM enrollments WHERE enrolled_by_contact_id IS NULL"
    ).fetchone()["n"]
    cohorts = conn.execute(
        """SELECT ch.id, p.code, ch.session_number, ch.session_date
           FROM cohorts ch JOIN programs p ON p.id = ch.program_id
           ORDER BY ch.session_date DESC, ch.id DESC"""
    ).fetchall()
    all_contacts = conn.execute(
        f"SELECT id, {FULL_NAME_SQL} AS full_name FROM contacts ORDER BY last_name, first_name"
    ).fetchall()
    conn.close()
    return render_template(
        "brought_in_by.html", rows=rows, cohorts=cohorts, cohort_id=cohort_id, show_all=show_all,
        truncated=truncated, limit=BROUGHT_IN_PAGE_LIMIT, missing_total=missing_total,
        all_contacts=all_contacts,
    )


# ---------- contracts & business plan (read-only; for Accounting & Legal) ----------

@crm.route("/contracts")
@area_required("contracts")
def contracts_list():
    conn = get_db()
    rows = conn.execute(
        f"""SELECT k.contact_id, {full_name_sql('c')} AS full_name, COUNT(*) AS n
            FROM contracts k JOIN contacts c ON c.id = k.contact_id
            GROUP BY k.contact_id, c.last_name, c.first_name
            ORDER BY c.last_name, c.first_name"""
    ).fetchall()
    conn.close()
    return render_template("contracts_list.html", people=rows)


@crm.route("/contracts/person/<int:contact_id>")
@area_required("contracts")
def contracts_person(contact_id):
    conn = get_db()
    person = conn.execute(
        f"SELECT {full_name_sql('c')} AS full_name FROM contacts c WHERE c.id = ?", (contact_id,)
    ).fetchone()
    if not person:
        conn.close()
        abort(404)
    rows = conn.execute(
        "SELECT * FROM contracts WHERE contact_id = ? ORDER BY kind", (contact_id,)
    ).fetchall()
    items = []
    for k in rows:
        revs = conn.execute(
            "SELECT * FROM contract_revisions WHERE contract_id = ? ORDER BY revised_at DESC, id DESC", (k["id"],)
        ).fetchall()
        items.append({"k": k, "revisions": revs})
    conn.close()
    return render_template("contracts_person.html", person=person, items=items, kinds=CONTRACT_KINDS)


@crm.route("/contracts/business-plan")
def business_plan_view():
    html_content, _text = preliminary_plan_content("")
    return render_template("business_plan_view.html", subject=PRELIMINARY_PLAN_SUBJECT, plan_html=html_content)


@crm.route("/contracts/<int:contract_id>")
@area_required("contracts")
def contract_view(contract_id):
    conn = get_db()
    k = conn.execute(
        f"""SELECT k.*, {full_name_sql('c')} AS full_name FROM contracts k
            JOIN contacts c ON c.id = k.contact_id WHERE k.id = ?""",
        (contract_id,),
    ).fetchone()
    if not k:
        conn.close()
        abort(404)
    revisions = conn.execute(
        "SELECT * FROM contract_revisions WHERE contract_id = ? ORDER BY revised_at DESC, id DESC", (contract_id,)
    ).fetchall()
    conn.close()
    return render_template("contract_view.html", k=k, revisions=revisions, kinds=CONTRACT_KINDS)


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


@app.route("/questionnaire/<token>", methods=["GET", "POST"])
def public_questionnaire(token):
    """Public, no-login page where a trainee answers their own questionnaire. The private link is the key;
    nobody else (including their partner) can see the answers from here."""
    _ensure_enrollment_extras()
    conn = get_db()
    enr = conn.execute(
        f"""SELECT e.*, ct.first_name, p.code, p.name AS program_name, c.session_number, c.session_date, c.end_date
            FROM enrollments e JOIN contacts ct ON ct.id = e.contact_id
            JOIN cohorts c ON c.id = e.cohort_id JOIN programs p ON p.id = c.program_id
            WHERE e.questionnaire_token = ?""", (token,)).fetchone()
    if not enr:
        conn.close()
        return render_template("public_questionnaire.html", enr=None), 404
    is_couples = enr["code"] == "D4"
    fields = RELATIONSHIP_PROFILE_FIELDS if is_couples else COMMON_PROFILE_FIELDS
    done = False
    if request.method == "POST":
        values = [request.form.get(f, "").strip() or None for f in fields]
        existing = conn.execute("SELECT id FROM training_profiles WHERE enrollment_id = ?", (enr["id"],)).fetchone()
        if existing:
            set_clause = ", ".join(f"{f} = ?" for f in fields)
            conn.execute(f"UPDATE training_profiles SET {set_clause}, updated_at = datetime('now') WHERE enrollment_id = ?",
                         (*values, enr["id"]))
        else:
            conn.execute(f"INSERT INTO training_profiles (enrollment_id, {', '.join(fields)}) "
                         f"VALUES (?, {', '.join('?' for _ in fields)})", (enr["id"], *values))
        if request.form.get("submit_done"):
            conn.execute("UPDATE enrollments SET questionnaire_completed_at = ? WHERE id = ?",
                         (date.today().isoformat(), enr["id"]))
            done = True
        conn.commit()
    profile = conn.execute("SELECT * FROM training_profiles WHERE enrollment_id = ?", (enr["id"],)).fetchone()
    conn.close()
    if not done and enr["questionnaire_completed_at"]:
        done = True
    return render_template("public_questionnaire.html", enr=enr, profile=profile, is_couples=is_couples,
                           done=done, saved=(request.method == "POST" and not done))


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
           WHERE COALESCE(c.end_date, c.session_date) >= date('now')
             AND c.status != 'Cancelled'
           ORDER BY c.session_date ASC LIMIT 20"""
    ).fetchall()
    conn.close()
    year = request.args.get("year", type=int)
    if not year:
        # default to this year, or -- if nothing is scheduled yet this year --
        # the year of the next upcoming session
        year = date.today().year
        if upcoming:
            first_year = int(str(upcoming[0]["session_date"])[:4])
            if first_year > year:
                year = first_year
    year = max(2000, min(2100, year))
    cal = _calendar_data(year, public=True)
    return render_template(
        "public_register.html", programs=programs, upcoming=upcoming, cal=cal,
        prev_url=url_for("public_register", year=year - 1) + "#calendar",
        next_url=url_for("public_register", year=year + 1) + "#calendar",
        today_url=url_for("public_register") + "#calendar",
    )


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
        return {"has_contacts_access": False, "has_program_access": False, "has_donations_access": False,
                "has_contracts_access": False}
    return {
        "has_contacts_access": _has_area_access("contacts"),
        "has_program_access": _has_any_program_access(),
        "has_donations_access": _has_area_access("donations"),
        "has_contracts_access": _has_area_access("contracts"),
    }


app.jinja_env.globals["role_label"] = role_label
app.register_blueprint(crm)


# ---------------------------------------------------------------------------
# Automatic completion letters
# After a session's last day has passed, everyone on that session's roster who is marked attended gets the
# Contract Information Request letter for that training, once. Off unless the
# AUTO_COMPLETION_LETTERS_START environment variable holds a date (YYYY-MM-DD):
# only sessions whose last day is on/after that date are ever considered, so old
# rosters are never emailed. Safe with several workers: the completion_letters
# table's UNIQUE enrollment_id lets exactly one claim each send.
# ---------------------------------------------------------------------------
COMPLETION_LETTER_WINDOW_DAYS = 14


def _ensure_completion_letters_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS completion_letters (
        id SERIAL PRIMARY KEY,
        enrollment_id INTEGER NOT NULL UNIQUE,
        contact_id INTEGER NOT NULL,
        program_code TEXT,
        sent_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
    )""")
    conn.commit()


def _auto_letters_start():
    raw = (os.environ.get("AUTO_COMPLETION_LETTERS_START") or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
    except ValueError:
        return None


def send_due_completion_letters(today=None):
    """Sends any completion letters that are due. Returns the number sent."""
    start = _auto_letters_start()
    if not start:
        return 0
    today = today or date.today()
    earliest = max(start, today - timedelta(days=COMPLETION_LETTER_WINDOW_DAYS))
    conn = get_db()
    try:
        _ensure_completion_letters_table(conn)
        rows = conn.execute(
            """SELECT e.id AS enrollment_id, c.id AS contact_id, c.first_name, c.email, c.profile_token,
                      p.code AS program_code, p.name AS program_name,
                      k.session_date, k.end_date
               FROM enrollments e
               JOIN cohorts k ON k.id = e.cohort_id
               JOIN programs p ON p.id = k.program_id
               JOIN contacts c ON c.id = e.contact_id
               WHERE COALESCE(k.status, '') <> 'Cancelled'
                 AND e.attended = 1
                 AND c.email IS NOT NULL AND c.email <> ''
                 AND NOT EXISTS (SELECT 1 FROM completion_letters l WHERE l.enrollment_id = e.id)"""
        ).fetchall()
        sent = 0
        for r in rows:
            try:
                last_day = datetime.strptime((r["end_date"] or r["session_date"])[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if not (earliest <= last_day < today):
                continue
            code = (r["program_code"] or "").upper()
            token = r["profile_token"]
            if not token:
                token = secrets.token_urlsafe(24)
                conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ? AND profile_token IS NULL",
                             (token, r["contact_id"]))
                conn.commit()
                token = conn.execute("SELECT profile_token FROM contacts WHERE id = ?", (r["contact_id"],)).fetchone()["profile_token"]
            with app.test_request_context(base_url=os.environ.get("PUBLIC_BASE_URL", "https://dallas-discovery-crm.onrender.com")):
                url = url_for("public_complete_profile", token=token, contracts=1, _external=True)
            letter = completion_letter_content(r["first_name"], url, code, r["program_name"])
            if not letter:
                continue
            claim = conn.execute(
                "INSERT INTO completion_letters (enrollment_id, contact_id, program_code) VALUES (?, ?, ?) ON CONFLICT (enrollment_id) DO NOTHING",
                (r["enrollment_id"], r["contact_id"], code))
            conn.commit()
            if claim.rowcount != 1:
                continue
            subject, html_content, text_content = letter
            try:
                send_email(to_email=r["email"], to_name=r["first_name"], subject=subject,
                           html_content=html_content, text_content=text_content)
                sent += 1
                print(f"AUTO-SENT {code} completion letter to contact {r['contact_id']} ({r['email']})", flush=True)
            except EmailSendError as e:
                conn.execute("DELETE FROM completion_letters WHERE enrollment_id = ?", (r["enrollment_id"],))
                conn.commit()
                print(f"EmailSendError auto-sending completion letter to {r['email']}: {e}", flush=True)
        return sent
    finally:
        conn.close()


def _completion_letter_loop():
    import time
    time.sleep(60)
    while True:
        try:
            send_due_completion_letters()
        except Exception as e:  # never let the loop die
            print(f"completion letter check failed: {e}", flush=True)
        time.sleep(3600)


if _auto_letters_start() and not os.environ.get("DISABLE_BACKGROUND_THREADS"):
    import threading
    threading.Thread(target=_completion_letter_loop, daemon=True).start()


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5050, debug=True)
