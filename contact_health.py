"""Contact health check: upload a spreadsheet/CSV (or check the contacts already in the Hub) and get a report.
Nothing is added to the Hub by this page. An uploaded file is read, checked and thrown away."""
import csv
import io
import re
import secrets
from flask import render_template, request, redirect, url_for, flash, session, Response

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
HEADER_GUESSES = {
    "first": ["first name", "firstname", "first", "fname", "given name"],
    "last": ["last name", "lastname", "last", "lname", "surname", "family name"],
    "name": ["name", "full name", "fullname", "contact name"],
    "email": ["email", "e-mail", "email address", "e-mail address", "primary email"],
    "cell": ["cell", "cell phone", "cellphone", "mobile", "mobile phone", "phone", "phone number", "primary phone"],
    "home": ["home phone", "home"],
    "street": ["street", "street address", "address", "address 1", "address1", "address line 1"],
    "city": ["city", "town"],
    "state": ["state", "st", "province"],
    "zip": ["zip", "zip code", "zipcode", "postal code", "postal"],
}
ISSUES = [
    ("no_name", "No name at all"),
    ("no_last", "No last name"),
    ("no_email", "No email address"),
    ("bad_email", "Email address that doesn't look valid"),
    ("no_phone", "No phone number"),
    ("bad_phone", "Phone number that isn't 10 digits"),
    ("caps", "Name in ALL CAPS or all lowercase"),
    ("junk", "Looks like a test or junk row"),
    ("dup_email", "Same email and same first name as another row (likely duplicate)"),
    ("dup_name", "Same first and last name as another row"),
    ("shared_email", "Shares an email with someone who has a different first name (may be a couple, not a problem)"),
    ("no_address", "No street address"),
]
_REPORT_TABLE_READY = [False]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("_", " ")).strip()


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _guess_columns(headers):
    found = {}
    normed = [_norm(h) for h in headers]
    for field, guesses in HEADER_GUESSES.items():
        for g in guesses:
            if g in normed and field not in found:
                found[field] = normed.index(g)
    return found


def read_upload(storage):
    """Returns (rows as list of dicts keyed by our field names, columns found, raw header list)."""
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
    cols = _guess_columns(headers)
    rows = []
    for n, r in enumerate(grid[1:], start=2):
        d = {"_row": n}
        for f, i in cols.items():
            d[f] = (r[i].strip() if i < len(r) and r[i] is not None else "")
        if "name" in d and not d.get("first") and not d.get("last"):
            parts = d["name"].split()
            if len(parts) >= 2:
                d["first"], d["last"] = parts[0], " ".join(parts[1:])
            else:
                d["first"] = d["name"]
        rows.append(d)
    return rows, cols, headers


def hub_rows(conn):
    out = []
    for r in conn.execute("SELECT id, first_name, last_name, email, cell_phone, home_phone, street_address, city, state, zip FROM contacts ORDER BY id").fetchall():
        out.append({"_row": r["id"], "first": r["first_name"] or "", "last": r["last_name"] or "", "email": r["email"] or "",
                    "cell": r["cell_phone"] or "", "home": r["home_phone"] or "", "street": r["street_address"] or "",
                    "city": r["city"] or "", "state": r["state"] or "", "zip": r["zip"] or ""})
    return out


def _first_close(a, b):
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a and b and (a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))))


def check_rows(rows, cols_found):
    """Returns (issues: {key: [row dicts]}, by_row: {rownum: [labels]})."""
    labels = dict(ISSUES)
    issues = {k: [] for k, _ in ISSUES}
    by_email, by_name = {}, {}
    for r in rows:
        e = (r.get("email") or "").strip().lower()
        if e:
            by_email.setdefault(e, []).append(r)
        fn, ln = (r.get("first") or "").strip().lower(), (r.get("last") or "").strip().lower()
        if fn and ln:
            by_name.setdefault(fn + "|" + ln, []).append(r)
    have = lambda f: f in cols_found or f in ("first", "last")
    for r in rows:
        first, last = (r.get("first") or "").strip(), (r.get("last") or "").strip()
        email = (r.get("email") or "").strip()
        phones = [p for p in (r.get("cell"), r.get("home")) if p and p.strip()]
        mark = lambda k: issues[k].append(r)
        if not first and not last:
            mark("no_name")
        elif not last:
            mark("no_last")
        if have("email"):
            if not email:
                mark("no_email")
            elif not EMAIL_RE.match(email):
                mark("bad_email")
        if "cell" in cols_found or "home" in cols_found:
            if not phones:
                mark("no_phone")
            elif any(len(_digits(p)) not in (10, 11) or (len(_digits(p)) == 11 and not _digits(p).startswith("1")) for p in phones):
                mark("bad_phone")
        nm = (first + " " + last).strip()
        letters = re.sub(r"[^A-Za-z]", "", nm)
        if len(letters) >= 3 and (letters.isupper() or letters.islower()):
            mark("caps")
        local, _, domain = email.lower().partition("@")
        low = nm.lower() + " " + local
        if re.search(r"\b(test|testing|asdf|xxx|zzz|dummy|sample|delete me|unknown)\b", low) or re.search(r"^(example\.(com|org|net)|test\.com)$", domain):
            mark("junk")
        if email:
            others = [o for o in by_email.get(email.lower(), []) if o is not r]
            if any(_first_close(first, o.get("first")) for o in others):
                mark("dup_email")
            elif others:
                mark("shared_email")
        if first and last and len(by_name.get(first.lower() + "|" + last.lower(), [])) > 1:
            mark("dup_name")
        if "street" in cols_found and not (r.get("street") or "").strip():
            mark("no_address")
    by_row = {}
    for k, lst in issues.items():
        for r in lst:
            by_row.setdefault(r["_row"], []).append(labels[k])
    return issues, by_row


def vs_hub(conn, rows):
    emails, names = {}, {}
    for r in conn.execute("SELECT id, first_name, last_name, email, email_2, email_3 FROM contacts").fetchall():
        for k in ("email", "email_2", "email_3"):
            e = (r[k] or "").strip().lower()
            if e:
                emails.setdefault(e, []).append(r)
        fn, ln = (r["first_name"] or "").strip().lower(), (r["last_name"] or "").strip().lower()
        if fn and ln:
            names.setdefault(fn + "|" + ln, []).append(r)
    exact = maybe = new = 0
    for r in rows:
        e = (r.get("email") or "").strip().lower()
        fn, ln = (r.get("first") or "").strip().lower(), (r.get("last") or "").strip().lower()
        hit = [h for h in emails.get(e, []) if _first_close(fn, h["first_name"])] if e else []
        if hit:
            exact += 1
        elif (fn and ln and names.get(fn + "|" + ln)):
            maybe += 1
        else:
            new += 1
    return {"exact": exact, "maybe": maybe, "new": new}


def _ensure_reports(conn):
    if _REPORT_TABLE_READY[0]:
        return
    conn.execute("CREATE TABLE IF NOT EXISTS health_reports (token TEXT PRIMARY KEY, csv_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    _REPORT_TABLE_READY[0] = True


def _problem_csv(rows, by_row, source_name):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Row" if source_name != "Hub" else "Hub contact #", "First name", "Last name", "Email", "Cell", "Home", "Street", "Problems"])
    for r in rows:
        if r["_row"] in by_row:
            w.writerow([r["_row"], r.get("first", ""), r.get("last", ""), r.get("email", ""), r.get("cell", ""), r.get("home", ""),
                        r.get("street", ""), "; ".join(by_row[r["_row"]])])
    return out.getvalue()


def register(app, crm, owner_required, get_db):

    @crm.route("/contacts/health", methods=["GET", "POST"])
    @owner_required
    def contact_health():
        if request.method == "GET":
            return render_template("contact_health.html", report=None)
        mode = request.form.get("mode")
        conn = get_db()
        try:
            _ensure_reports(conn)
            if mode == "hub":
                rows, cols, headers, source = hub_rows(conn), {k: 0 for k in ("email", "cell", "home", "street")}, [], "Hub"
                source_label = "the contacts already in the Hub"
            else:
                f = request.files.get("file")
                if not f or not f.filename:
                    flash("Choose a file first.")
                    return redirect(url_for(".contact_health"))
                try:
                    rows, cols, headers = read_upload(f)
                except Exception as e:
                    flash(f"That file couldn't be read ({str(e)[:100]}). Try saving it as a CSV or .xlsx file.")
                    return redirect(url_for(".contact_health"))
                source, source_label = "file", f.filename
                if not rows:
                    flash("That file looks empty.")
                    return redirect(url_for(".contact_health"))
            issues, by_row = check_rows(rows, cols)
            hub = vs_hub(conn, rows) if source == "file" else None
            token = secrets.token_urlsafe(16)
            conn.execute("DELETE FROM health_reports WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '1 day'")
            conn.execute("INSERT INTO health_reports (token, csv_text) VALUES (?, ?)", (token, _problem_csv(rows, by_row, source)))
            conn.commit()
        finally:
            conn.close()
        total = len(rows)
        have = lambda k: sum(1 for r in rows if (r.get(k) or "").strip())
        stats = [("People", total)]
        if source == "file" or True:
            stats += [("With an email", have("email")), ("With a cell number", have("cell")), ("With a street address", have("street"))]
        report = dict(source_label=source_label, source=source, total=total, stats=stats, hub=hub, token=token,
                      cols=cols, headers=headers, problem_rows=len(by_row),
                      issues=[(k, lbl, len(issues[k]), issues[k][:8]) for k, lbl in ISSUES if issues[k]])
        return render_template("contact_health.html", report=report)

    @crm.route("/contacts/health/download/<token>")
    @owner_required
    def contact_health_download(token):
        conn = get_db()
        try:
            _ensure_reports(conn)
            row = conn.execute("SELECT csv_text FROM health_reports WHERE token = ?", (token,)).fetchone()
        finally:
            conn.close()
        if not row:
            flash("That report has expired. Run the check again.")
            return redirect(url_for(".contact_health"))
        return Response(row["csv_text"], mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=contact-health-problems.csv"})
