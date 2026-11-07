"""Contact import (Owner and Administrators): check a file, show exactly what would be added and what would not,
then add it as one tagged batch that can be undone. Nothing is sent to anyone by importing."""
import csv
import io
import json
import re
import secrets
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, session, Response

import contact_health as ch

GUESSES = dict(ch.HEADER_GUESSES)
GUESSES.update({
    "middle": ["middle", "middle name", "middlename"],
    "email2": ["email2", "email 2", "second email", "email address 2", "e-mail 2 address", "e-mail 2"],
    "email3": ["email3", "email 3", "third email", "email address 3", "e-mail 3 address", "e-mail 3"],
    "work": ["work phone", "work_phone", "business phone", "workphone"],
    "company": ["company", "organization", "employer"],
    "title": ["job title", "job_title", "title", "position"],
    "group": ["group", "category", "categories"],
    "notes": ["notes", "note", "comments"],
    "src": ["source", "source row"],
})
SENSITIVE = re.compile(r"password|passwd|\bpwd\b|\bpin\b|\bpin:|login|log in|username|user name|serial|access code|passcode|"
                       r"\bssn\b|social security|account\s*(#|no|number)|acct|routing|credit card|\bcvv\b|security code|meeting id", re.I)
STATUS = "Interested Party"
CHUNK = 250


NAMES_LIST = "Names to check"
BIZ = re.compile(r"\b(service|services|inc|llc|ltd|co|company|corp|department|dept|store|bank|hospital|insurance|center|association|assoc|group|supply|shop|bistro|cust)\b", re.I)


def name_flags(first, last):
    """Plain-English reasons a name may be split wrongly. Empty list = looks fine."""
    f = (first or "").strip(); l = (last or "").strip(); out = []
    if not l:
        out.append("No last name")
    if re.search(r"\d", f + l):
        out.append("Digits in the name")
    if l and not re.match(r"[A-Za-z]", l):
        out.append("Last name starts with a symbol")
    if l and re.search(r"[\[\](){}@:]", l):
        out.append("Odd characters in last name")
    if l and len(re.sub(r"[^A-Za-z]", "", l)) <= 1:
        out.append("Last name is one letter")
    if BIZ.search(f + " " + l):
        out.append("Looks like a company")
    if l and len(f.split()) >= 2 and not re.search(r"(&|\band\b|\+)", f, re.I):
        out.append("First name has several words")
    if SENSITIVE.search(f + " " + l):
        out.append("Name field may hold a password or login")
    return out


def refresh_names_list(conn):
    """Make the 'Names to check' list match the imported contacts that still look uncertain."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lid = _make_list(conn, NAMES_LIST, now)
    rows = conn.execute("SELECT id, first_name, last_name FROM contacts WHERE import_batch_id IS NOT NULL").fetchall()
    want = {r["id"]: name_flags(r["first_name"], r["last_name"]) for r in rows}
    want = {k: v for k, v in want.items() if v}
    have = {r["contact_id"] for r in conn.execute("SELECT contact_id FROM contact_list_members WHERE list_id = ?", (lid,)).fetchall()}
    add = [i for i in want if i not in have]
    drop = [i for i in have if i not in want]
    for i in range(0, len(add), 200):
        part = add[i:i + 200]
        conn.execute("INSERT INTO contact_list_members (contact_id, list_id, added_at) VALUES " + ",".join(["(?,?,?)"] * len(part)),
                     [x for c in part for x in (c, lid, now[:10])])
    for i in range(0, len(drop), 400):
        part = drop[i:i + 400]
        conn.execute(f"DELETE FROM contact_list_members WHERE list_id = ? AND contact_id IN ({','.join('?' for _ in part)})", [lid] + part)
    conn.commit()
    return len(want), want


def _norm(s):
    return ch._norm(s)


def read_rows(storage):
    name = (storage.filename or "").lower()
    data = storage.read()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        grid = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        grid = list(csv.reader(io.StringIO(text), dialect))
    grid = [r for r in grid if any((c or "").strip() for c in r)]
    if not grid:
        return [], {}, []
    headers = grid[0]
    normed = [_norm(h) for h in headers]
    cols = {}
    for field, guesses in GUESSES.items():
        for g in guesses:
            if _norm(g) in normed and field not in cols:
                cols[field] = normed.index(_norm(g))
    rows = []
    for n, r in enumerate(grid[1:], start=2):
        d = {"_row": n}
        for f, i in cols.items():
            d[f] = (r[i].strip() if i < len(r) and r[i] is not None else "")
        if "name" in d and not d.get("first") and not d.get("last"):
            parts = d["name"].split()
            d["first"], d["last"] = (parts[0], " ".join(parts[1:])) if len(parts) >= 2 else (d["name"], "")
        rows.append(d)
    return rows, cols, headers


def _state2(s):
    s = (s or "").strip()
    return s.upper() if len(s) == 2 else s


def classify(rows, cols, hub_emails, hub_names):
    """Returns (to_add, skipped, counts). Each skipped row carries a plain-English reason."""
    to_add, skipped = [], []
    counts = dict(bad_email=0, sensitive_notes=0, maybe_same=0, no_email=0)
    seen = {}
    for r in rows:
        first, last = (r.get("first") or "").strip(), (r.get("last") or "").strip()
        emails = []
        bad = []
        for k in ("email", "email2", "email3"):
            e = (r.get(k) or "").strip()
            if not e:
                continue
            (emails if ch.EMAIL_RE.match(e) else bad).append(e)
        phones = [p for p in (r.get("cell"), r.get("home"), r.get("work")) if p and p.strip()]
        why = None
        if not first and not last:
            why = "No name"
        elif re.search(r"\b(test|testing|asdf|xxx|zzz|dummy|sample|delete me)\b", (first + " " + last).lower()):
            why = "Looks like a test or junk row"
        elif not emails and not phones:
            why = "No usable email and no phone number"
        if not why and emails:
            e0 = emails[0].lower()
            for h in hub_emails.get(e0, []):
                if ch._first_close(first, h):
                    why = "Already in the Hub (same email and first name)"
                    break
            if not why:
                for o in seen.get(e0, []):
                    if ch._first_close(first, o):
                        why = "Duplicate of another row in this file"
                        break
        if why:
            skipped.append(dict(r, _reason=why))
            continue
        if emails:
            seen.setdefault(emails[0].lower(), []).append(first)
        else:
            counts["no_email"] += 1
        notes = []
        if r.get("group"):
            notes.append("Group: " + r["group"])
        if r.get("title"):
            notes.append("Title: " + r["title"])
        if r.get("middle"):
            notes.append("Middle name: " + r["middle"])
        raw = (r.get("notes") or "").strip()
        if raw:
            if SENSITIVE.search(raw):
                counts["sensitive_notes"] += 1
                notes.append("(A note in the original file was left out because it looked like it contained a password, PIN or account number.)")
            else:
                notes.append("Note: " + raw)
        if bad:
            counts["bad_email"] += 1
            notes.append("Email in the file that did not look valid: " + ", ".join(bad))
        if r.get("src"):
            notes.append("Imported from " + r["src"])
        if first and last and hub_names.get(first.lower() + "|" + last.lower()):
            counts["maybe_same"] += 1
        to_add.append(dict(
            first=first or last, last=last if first else "", email=emails[0] if emails else "",
            email2=emails[1] if len(emails) > 1 else "", email3=emails[2] if len(emails) > 2 else "",
            cell=(r.get("cell") or "").strip(), home=(r.get("home") or "").strip(), work=(r.get("work") or "").strip(),
            street=(r.get("street") or "").strip(), city=(r.get("city") or "").strip(),
            state=_state2(r.get("state")), zip=(r.get("zip") or "").strip(),
            employer=(r.get("company") or "").strip(), group=(r.get("group") or "").strip(),
            notes=" · ".join(notes)))
    return to_add, skipped, counts


def _hub_lookups(conn):
    emails, names = {}, {}
    for r in conn.execute("SELECT first_name, last_name, email, email_2, email_3 FROM contacts").fetchall():
        for k in ("email", "email_2", "email_3"):
            e = (r[k] or "").strip().lower()
            if e:
                emails.setdefault(e, []).append(r["first_name"] or "")
        fn, ln = (r["first_name"] or "").strip().lower(), (r["last_name"] or "").strip().lower()
        if fn and ln:
            names[fn + "|" + ln] = True
    return emails, names


def _skipped_csv(skipped):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Row in file", "First", "Last", "Email", "Cell", "Reason"])
    for r in skipped:
        w.writerow([r.get("_row"), r.get("first", ""), r.get("last", ""), r.get("email", ""), r.get("cell", ""), r["_reason"]])
    return out.getvalue()


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS contact_imports (
        id SERIAL PRIMARY KEY, source TEXT, filename TEXT, created_by TEXT, created_at TEXT,
        added INTEGER, skipped INTEGER, list_ids TEXT, undone_at TEXT, undone_count INTEGER, kept_count INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS import_staging (
        token TEXT PRIMARY KEY, source TEXT, filename TEXT, add_json TEXT, skipped_csv TEXT, summary_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS import_batch_id INTEGER")
    conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS import_group TEXT")
    conn.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS do_not_email INTEGER NOT NULL DEFAULT 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS contact_lists (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT '')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS contact_list_members (id SERIAL PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id),
        list_id INTEGER NOT NULL REFERENCES contact_lists(id), added_at TEXT NOT NULL DEFAULT '', UNIQUE(contact_id, list_id))""")
    conn.commit()


# tables that point at a contact without a declared foreign key
LOOSE_REFS = [("call_requests", "contact_id"), ("interest_log", "contact_id"), ("payments", "contact_id"),
              ("donations", "contact_id"), ("volunteer_helpers", "contact_id"), ("staff", "contact_id")]


def _touched_ids(conn, ids):
    """Contact ids (from ids) that something else in the Hub now depends on."""
    if not ids:
        return set()
    refs = [(r["tbl"], r["col"]) for r in conn.execute(
        """SELECT c.conrelid::regclass::text AS tbl, a.attname AS col FROM pg_constraint c
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
           WHERE c.contype = 'f' AND c.confrelid = 'contacts'::regclass""").fetchall()]
    refs = [(t, c) for t, c in refs if t not in ("contact_list_members", "contacts")]
    have = {r["t"] for r in conn.execute("SELECT table_name AS t FROM information_schema.tables WHERE table_schema = current_schema()").fetchall()}
    for t, c in LOOSE_REFS:
        if t in have and (t, c) not in refs:
            refs.append((t, c))
    # a contact that points at another contact, or is pointed at by one (sponsor links), also counts
    touched = set()
    marks = ",".join("?" for _ in ids)
    for t, c in refs:
        if t.split(".")[-1].strip('"') not in have:
            continue
        try:
            for r in conn.execute(f"SELECT DISTINCT {c} AS cid FROM {t} WHERE {c} IN ({marks})", list(ids)).fetchall():
                touched.add(r["cid"])
        except Exception:
            conn.rollback()
            raise
    return touched


def register(app, crm, owner_required, get_db):
    @crm.route("/contacts/import")
    @owner_required
    def contact_import():
        conn = get_db()
        try:
            ensure(conn)
            batches = conn.execute("SELECT * FROM contact_imports ORDER BY id DESC").fetchall()
            nr = conn.execute("""SELECT COUNT(*) c FROM contact_list_members m JOIN contact_lists l ON l.id = m.list_id
                                 WHERE l.name = ?""", (NAMES_LIST,)).fetchone()
            names_count = nr["c"] if nr else 0
        finally:
            conn.close()
        return render_template("contact_import.html", report=None, batches=batches, default_source="Kent's own contacts", names_count=names_count)

    @crm.route("/contacts/import/names-to-check", methods=["POST"])
    @owner_required
    def contact_names_refresh():
        conn = get_db()
        try:
            ensure(conn)
            n, _ = refresh_names_list(conn)
        finally:
            conn.close()
        flash(f"The \"{NAMES_LIST}\" list now has {n} contacts. Nothing was changed on any contact and nothing was emailed.")
        return redirect(url_for(".contact_import"))

    @crm.route("/contacts/import/names-to-check.csv")
    @owner_required
    def contact_names_csv():
        import csv, io
        conn = get_db()
        try:
            ensure(conn)
            rows = conn.execute("SELECT id, first_name, last_name, email, cell_phone, import_group FROM contacts WHERE import_batch_id IS NOT NULL ORDER BY last_name, first_name").fetchall()
        finally:
            conn.close()
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow(["Hub ID", "First name", "Last name", "Email", "Cell", "Group", "Why it was flagged"])
        for r in rows:
            fl = name_flags(r["first_name"], r["last_name"])
            if fl:
                w.writerow([r["id"], r["first_name"], r["last_name"] or "", r["email"] or "", r["cell_phone"] or "", r["import_group"] or "", "; ".join(fl)])
        from flask import Response
        return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=names-to-check.csv"})

    @crm.route("/contacts/import/check", methods=["POST"])
    @owner_required
    def contact_import_check():
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a file first.")
            return redirect(url_for(".contact_import"))
        try:
            rows, cols, headers = read_rows(f)
        except Exception as e:
            flash(f"That file couldn't be read ({str(e)[:100]}). Try a CSV or .xlsx file.")
            return redirect(url_for(".contact_import"))
        problems = []
        if not rows:
            problems.append("The file looks empty.")
        if not any(k in cols for k in ("first", "last", "name")):
            problems.append("No name column was found (looking for First, Last or Name).")
        if not any(k in cols for k in ("email", "cell", "home")):
            problems.append("No email or phone column was found.")
        conn = get_db()
        try:
            ensure(conn)
            emails, names = _hub_lookups(conn)
            to_add, skipped, counts = classify(rows, cols, emails, names) if not problems else ([], [], dict(bad_email=0, sensitive_notes=0, maybe_same=0, no_email=0))
            if rows and not problems and len(skipped) > 0.25 * len(rows):
                problems.append(f"{len(skipped)} of {len(rows)} rows would be skipped. That is more than a quarter, which usually means the wrong file or wrong columns.")
            reasons = {}
            for s in skipped:
                reasons[s["_reason"]] = reasons.get(s["_reason"], 0) + 1
            groups = {}
            for a in to_add:
                if a["group"]:
                    groups[a["group"]] = groups.get(a["group"], 0) + 1
            token = secrets.token_urlsafe(16)
            conn.execute("DELETE FROM import_staging WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '1 day'")
            summary = dict(total=len(rows), add=len(to_add), skipped=len(skipped), reasons=reasons, groups=groups, counts=counts,
                           columns=sorted(cols.keys()), problems=problems)
            conn.execute("INSERT INTO import_staging (token, source, filename, add_json, skipped_csv, summary_json) VALUES (?,?,?,?,?,?)",
                         (token, "", f.filename, json.dumps(to_add), _skipped_csv(skipped), json.dumps(summary)))
            conn.commit()
            batches = conn.execute("SELECT * FROM contact_imports ORDER BY id DESC").fetchall()
        finally:
            conn.close()
        report = dict(summary, token=token, filename=f.filename, passed=not problems, sample=to_add[:8])
        return render_template("contact_import.html", report=report, batches=batches, default_source="Kent's own contacts")

    @crm.route("/contacts/import/not-added/<token>")
    @owner_required
    def contact_import_not_added(token):
        conn = get_db()
        try:
            ensure(conn)
            row = conn.execute("SELECT skipped_csv FROM import_staging WHERE token = ?", (token,)).fetchone()
        finally:
            conn.close()
        if not row:
            flash("That list has expired. Check the file again.")
            return redirect(url_for(".contact_import"))
        return Response(row["skipped_csv"], mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=not-added.csv"})

    @crm.route("/contacts/import/run", methods=["POST"])
    @owner_required
    def contact_import_run():
        token = request.form.get("token") or ""
        source = (request.form.get("source") or "").strip()
        if not source:
            flash("Give this group of contacts a name (for example: Kent's own contacts). Nothing was added.")
            return redirect(url_for(".contact_import"))
        if not request.form.get("backup_done"):
            flash("Please confirm you took a Render backup first. Nothing was added.")
            return redirect(url_for(".contact_import"))
        conn = get_db()
        try:
            ensure(conn)
            row = conn.execute("SELECT * FROM import_staging WHERE token = ?", (token,)).fetchone()
            if not row:
                flash("That check has expired. Please check the file again. Nothing was added.")
                return redirect(url_for(".contact_import"))
            summ = json.loads(row["summary_json"])
            if summ.get("problems"):
                flash("The file did not pass the check. Nothing was added.")
                return redirect(url_for(".contact_import"))
            if conn.execute("SELECT 1 FROM contact_imports WHERE filename = ? AND undone_at IS NULL AND source = ?", (row["filename"], source)).fetchone():
                flash("This file was already imported under that name. Undo that import first if you want to redo it. Nothing was added.")
                return redirect(url_for(".contact_import"))
            people = json.loads(row["add_json"])
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            bid = conn.execute("""INSERT INTO contact_imports (source, filename, created_by, created_at, added, skipped, list_ids)
                                  VALUES (?,?,?,?,0,?,?) RETURNING id""",
                               (source, row["filename"], session.get("staff_name"), now, summ.get("skipped", 0), "[]")).fetchone()["id"]
            conn.commit()
            cols = ["first_name", "last_name", "email", "email_2", "email_3", "cell_phone", "home_phone", "work_phone",
                    "street_address", "city", "state", "zip", "employer", "notes", "status", "import_group", "import_batch_id"]
            one = "(" + ",".join("?" for _ in cols) + ")"
            for i in range(0, len(people), CHUNK):
                part = people[i:i + CHUNK]
                args = []
                for p in part:
                    args += [p["first"], p["last"] or None, p["email"] or None, p["email2"] or None, p["email3"] or None,
                             p["cell"] or None, p["home"] or None, p["work"] or None, p["street"] or None, p["city"] or None,
                             p["state"] or None, p["zip"] or None, p["employer"] or None, p["notes"] or None, STATUS,
                             p["group"] or None, bid]
                conn.execute(f"INSERT INTO contacts ({','.join(cols)}) VALUES " + ",".join([one] * len(part)), args)
                conn.commit()
            added = conn.execute("SELECT COUNT(*) c FROM contacts WHERE import_batch_id = ?", (bid,)).fetchone()["c"]
            list_ids = []
            lid = _make_list(conn, source, now)
            list_ids.append(lid)
            conn.execute("""INSERT INTO contact_list_members (contact_id, list_id, added_at)
                            SELECT id, ?, ? FROM contacts WHERE import_batch_id = ?""", (lid, now[:10], bid))
            if request.form.get("group_lists"):
                for g in (summ.get("groups") or {}):
                    gl = _make_list(conn, f"{source}: {g}", now)
                    list_ids.append(gl)
                    conn.execute("""INSERT INTO contact_list_members (contact_id, list_id, added_at)
                                    SELECT id, ?, ? FROM contacts WHERE import_batch_id = ? AND import_group = ?""", (gl, now[:10], bid, g))
            conn.commit()
            try:
                refresh_names_list(conn)
            except Exception:
                conn.rollback()
            conn.execute("UPDATE contact_imports SET added = ?, list_ids = ? WHERE id = ?", (added, json.dumps(list_ids), bid))
            conn.execute("DELETE FROM import_staging WHERE token = ?", (token,))
            conn.commit()
        finally:
            conn.close()
        flash(f"Added {added} contacts as \"{source}\". Nothing was emailed. You can undo this import below.")
        return redirect(url_for(".contact_import"))

    @crm.route("/contacts/import/<int:bid>/undo", methods=["POST"])
    @owner_required
    def contact_import_undo(bid):
        conn = get_db()
        try:
            ensure(conn)
            b = conn.execute("SELECT * FROM contact_imports WHERE id = ?", (bid,)).fetchone()
            if not b or b["undone_at"]:
                flash("That import was not found or is already undone.")
                return redirect(url_for(".contact_import"))
            ids = [r["id"] for r in conn.execute("SELECT id FROM contacts WHERE import_batch_id = ?", (bid,)).fetchall()]
            touched = _touched_ids(conn, ids)
            gone = [i for i in ids if i not in touched]
            for i in range(0, len(gone), 400):
                part = gone[i:i + 400]
                m = ",".join("?" for _ in part)
                conn.execute(f"DELETE FROM contact_list_members WHERE contact_id IN ({m})", part)
                conn.execute(f"DELETE FROM contacts WHERE id IN ({m})", part)
            for lid in json.loads(b["list_ids"] or "[]"):
                if not conn.execute("SELECT 1 FROM contact_list_members WHERE list_id = ?", (lid,)).fetchone():
                    conn.execute("DELETE FROM contact_lists WHERE id = ?", (lid,))
            conn.execute("UPDATE contact_imports SET undone_at = ?, undone_count = ?, kept_count = ? WHERE id = ?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M"), len(gone), len(touched), bid))
            conn.commit()
        finally:
            conn.close()
        msg = f"Removed {len(gone)} contacts from that import."
        if touched:
            msg += f" {len(touched)} were kept because they have already been used elsewhere in the Hub (for example enrolled, or given a login)."
        flash(msg)
        return redirect(url_for(".contact_import"))


def _make_list(conn, name, now):
    r = conn.execute("SELECT id FROM contact_lists WHERE name = ?", (name,)).fetchone()
    if r:
        return r["id"]
    return conn.execute("INSERT INTO contact_lists (name, created_at) VALUES (?, ?) RETURNING id", (name, now[:10])).fetchone()["id"]
