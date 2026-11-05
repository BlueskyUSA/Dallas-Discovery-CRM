"""Possible duplicate contacts: find them, merge them, and warn when someone is about to add one.

Merging moves everything that points at the duplicate contact (sessions, payments, letters, notes, lists, photo...)
over to the contact you keep, fills blanks on the kept contact from the duplicate, then removes the duplicate.
Every merge is first run as a preview inside a database transaction that is rolled back, so the preview shows exactly
what the real merge will do. A merge that hits a conflict it can't settle (for example both people are enrolled in the
same session) changes nothing and says why.
"""
import re
from flask import render_template, request, redirect, url_for, flash, session, jsonify

SKIP_FILL = {"id", "profile_token", "notes", "status", "created_at", "updated_at", "email", "email_2", "email_3"}
TOLERATED = {"contact_photos", "contact_list_members"}   # if the kept contact already has one, the duplicate's is simply dropped


def _digits(s):
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else ""


def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS contact_dupe_ignore (
        a INTEGER NOT NULL, b INTEGER NOT NULL, marked_by TEXT, marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (a, b))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS contact_merges (
        id SERIAL PRIMARY KEY, keeper_id INTEGER, duplicate_id INTEGER, duplicate_name TEXT, summary TEXT,
        merged_by TEXT, merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()


def _contact_columns(conn):
    return [r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'contacts' ORDER BY ordinal_position").fetchall()]


def _child_columns(conn):
    rows = conn.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()").fetchall()
    out = []
    for r in rows:
        t, c = r["table_name"], r["column_name"]
        if t in ("contact_dupe_ignore", "contact_merges"):
            continue
        if c == "contact_id" or c.endswith("_contact_id"):
            out.append((t, c))
    return sorted(out)


def _same_first(a, b):
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a and b and (a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))))


def find_pairs(conn):
    rows = conn.execute("SELECT id, first_name, last_name, email, email_2, email_3, cell_phone, home_phone, work_phone, status FROM contacts").fetchall()
    people = {r["id"]: dict(r) for r in rows}
    keys = {}
    for p in people.values():
        for e in (p["email"], p["email_2"], p["email_3"]):
            e = (e or "").strip().lower()
            if e:
                keys.setdefault(("Same email address", e), set()).add(p["id"])
        for ph in (p["cell_phone"], p["home_phone"], p["work_phone"]):
            d = _digits(ph)
            if d:
                keys.setdefault(("Same phone number", d), set()).add(p["id"])
        fn, ln = (p["first_name"] or "").strip().lower(), (p["last_name"] or "").strip().lower()
        if fn and ln:
            keys.setdefault(("Same first and last name", fn + "|" + ln), set()).add(p["id"])
    pairs = {}
    for (reason, _k), ids in keys.items():
        ids = sorted(ids)
        if len(ids) < 2 or len(ids) > 6:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                r = reason
                if reason == "Same email address" and not _same_first(people[ids[i]]["first_name"], people[ids[j]]["first_name"]):
                    r = "Shared email, different first names (may be a couple)"
                pairs.setdefault((ids[i], ids[j]), set()).add(r)
    ignored = {(r["a"], r["b"]) for r in conn.execute("SELECT a, b FROM contact_dupe_ignore").fetchall()}
    order = {"Same email address": 0, "Same first and last name": 1, "Same phone number": 2,
             "Shared email, different first names (may be a couple)": 3}
    out = []
    for (a, b), reasons in pairs.items():
        if (a, b) in ignored:
            continue
        out.append(dict(a=people[a], b=people[b], reasons=sorted(reasons, key=lambda r: order[r]), rank=min(order[r] for r in reasons)))
    out.sort(key=lambda x: (x["rank"], -len(x["reasons"]), x["a"]["id"]))
    return out


def _counts(conn, ids):
    res = {i: {} for i in ids}
    if not ids:
        return res
    ph = ",".join("?" * len(ids))
    for label, sql in (("sessions", f"SELECT contact_id AS i, COUNT(*) AS n FROM enrollments WHERE contact_id IN ({ph}) GROUP BY contact_id"),
                       ("team roles", f"SELECT contact_id AS i, COUNT(*) AS n FROM cohort_staffing WHERE contact_id IN ({ph}) GROUP BY contact_id"),
                       ("donations", f"SELECT contact_id AS i, COUNT(*) AS n FROM donations WHERE contact_id IN ({ph}) GROUP BY contact_id")):
        try:
            for r in conn.execute(sql, tuple(ids)).fetchall():
                res[r["i"]][label] = r["n"]
        except Exception:
            conn.rollback()
    return res


def do_merge(conn, keeper_id, dup_id, apply, merged_by=None):
    """Returns (lines, problems). Always works inside a transaction; commits only when apply is True and there are no problems."""
    lines, problems = [], []
    cols = _contact_columns(conn)
    keeper = conn.execute("SELECT * FROM contacts WHERE id = ?", (keeper_id,)).fetchone()
    dup = conn.execute("SELECT * FROM contacts WHERE id = ?", (dup_id,)).fetchone()
    if not keeper or not dup or keeper_id == dup_id:
        return ["Those two contacts could not be found."], ["missing"]
    keeper, dup = dict(keeper), dict(dup)
    dup_name = f"{dup['first_name']} {dup.get('last_name') or ''}".strip()
    try:
        # 1. everything that points at the duplicate moves to the keeper
        for table, col in _child_columns(conn):
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = ?", (dup_id,)).fetchone()["n"]
            if not n:
                continue
            conn.execute("SAVEPOINT mv")
            try:
                conn.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (keeper_id, dup_id))
                conn.execute("RELEASE SAVEPOINT mv")
                lines.append(f"Moves {n} record{'s' if n != 1 else ''} in {table.replace('_', ' ')} to the contact you keep.")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT mv")
                conn.execute("RELEASE SAVEPOINT mv")
                if table in TOLERATED:
                    lines.append(f"The contact you keep already has this in {table.replace('_', ' ')}, so the duplicate's copy is dropped.")
                else:
                    problems.append(f"Both contacts already have a record in {table.replace('_', ' ')} ({col}). Settle that one by hand first, then merge.")
        conn.execute("UPDATE contacts SET sponsor_contact_id = NULL WHERE id = ? AND sponsor_contact_id = ?", (keeper_id, keeper_id))

        # 2. blanks on the keeper are filled from the duplicate (nothing the keeper already has is overwritten)
        fill = {c: dup[c] for c in cols if c not in SKIP_FILL and keeper.get(c) in (None, "") and dup.get(c) not in (None, "")}
        # extra email addresses go into free email slots
        have = {(keeper.get(k) or "").strip().lower() for k in ("email", "email_2", "email_3")} - {""}
        slots = [k for k in ("email", "email_2", "email_3") if k in cols and not (keeper.get(k) or "").strip()]
        for e in [dup.get("email"), dup.get("email_2"), dup.get("email_3")]:
            e = (e or "").strip()
            if e and e.lower() not in have and slots:
                fill[slots.pop(0)] = e
                have.add(e.lower())
        if fill:
            lines.append("Fills these blanks from the duplicate: " + ", ".join(k.replace("_", " ") for k in fill) + ".")
        token = dup.get("profile_token")
        if token and not keeper.get("profile_token"):
            conn.execute("UPDATE contacts SET profile_token = NULL WHERE id = ?", (dup_id,))
            fill["profile_token"] = token
            lines.append("Moves their private profile link, so a link already emailed keeps working.")
        elif token:
            lines.append("The contact you keep has its own private profile link. A link already emailed from the duplicate will stop working.")
        if dup.get("notes"):
            fill["notes"] = (keeper["notes"] + "\n\n" + dup["notes"]) if keeper.get("notes") else dup["notes"]
            lines.append("Adds the duplicate's notes after the kept contact's notes.")
        if fill:
            sets = ", ".join(f"{k} = ?" for k in fill)
            conn.execute(f"UPDATE contacts SET {sets} WHERE id = ?", list(fill.values()) + [keeper_id])

        # 3. the duplicate's leftovers, then the duplicate
        for t in TOLERATED:
            try:
                conn.execute(f"DELETE FROM {t} WHERE contact_id = ?", (dup_id,))
            except Exception:
                conn.rollback()
                raise
        conn.execute("DELETE FROM contacts WHERE id = ?", (dup_id,))
        lines.append(f"Removes the duplicate contact ({dup_name}).")
        if problems:
            conn.rollback()
        elif apply:
            _ensure(conn)
            conn.execute("INSERT INTO contact_merges (keeper_id, duplicate_id, duplicate_name, summary, merged_by) VALUES (?,?,?,?,?)",
                         (keeper_id, dup_id, dup_name, "\n".join(lines), merged_by))
            conn.commit()
        else:
            conn.rollback()
    except Exception as e:
        conn.rollback()
        problems.append(f"The Hub couldn't finish this merge, so nothing was changed. ({str(e)[:160]})")
    return lines, problems


def register(app, crm, area_required, leadership_required, get_db):

    @crm.route("/contacts/duplicates")
    @area_required("contacts")
    def contact_duplicates():
        conn = get_db()
        try:
            _ensure(conn)
            pairs = find_pairs(conn)
            counts = _counts(conn, sorted({p[k]["id"] for p in pairs for k in ("a", "b")}))
            merges = conn.execute("SELECT * FROM contact_merges ORDER BY id DESC LIMIT 15").fetchall()
        finally:
            conn.close()
        return render_template("contact_duplicates.html", pairs=pairs, counts=counts, merges=merges,
                               can_merge=bool(session.get("is_owner") or session.get("staff_role") == "Leadership"))

    @crm.route("/contacts/duplicates/ignore", methods=["POST"])
    @area_required("contacts")
    def contact_duplicates_ignore():
        a, b = sorted([int(request.form["a"]), int(request.form["b"])])
        conn = get_db()
        try:
            _ensure(conn)
            conn.execute("INSERT INTO contact_dupe_ignore (a, b, marked_by) VALUES (?,?,?) ON CONFLICT DO NOTHING", (a, b, session.get("staff_name")))
            conn.commit()
        finally:
            conn.close()
        flash("Got it. Those two won't be suggested again.")
        return redirect(url_for(".contact_duplicates"))

    @crm.route("/contacts/merge", methods=["GET", "POST"])
    @leadership_required
    def contact_merge():
        keeper_id = int(request.values.get("keeper", 0) or 0)
        dup_id = int(request.values.get("dup", 0) or 0)
        conn = get_db()
        try:
            _ensure(conn)
            apply = request.method == "POST"
            lines, problems = do_merge(conn, keeper_id, dup_id, apply, session.get("staff_name"))
            if apply:
                if problems:
                    flash(problems[0])
                    return redirect(url_for(".contact_duplicates"))
                flash("Merged. Everything is now on one contact.")
                return redirect(url_for(".contact_detail", contact_id=keeper_id))
            both = {r["id"]: r for r in conn.execute("SELECT id, first_name, last_name, email, cell_phone, status FROM contacts WHERE id IN (?, ?)", (keeper_id, dup_id)).fetchall()}
        finally:
            conn.close()
        if keeper_id not in both or dup_id not in both:
            flash("Those contacts could not be found.")
            return redirect(url_for(".contact_duplicates"))
        return render_template("contact_merge.html", keeper=both[keeper_id], dup=both[dup_id], lines=lines, problems=problems)

    @crm.route("/contacts/check-duplicate")
    @area_required("contacts")
    def contact_check_duplicate():
        first = (request.args.get("first") or "").strip().lower()
        last = (request.args.get("last") or "").strip().lower()
        email = (request.args.get("email") or "").strip().lower()
        cell = _digits(request.args.get("cell"))
        exclude = int(request.args.get("exclude", 0) or 0)
        conn = get_db()
        try:
            rows = conn.execute("SELECT id, first_name, last_name, email, email_2, email_3, cell_phone, home_phone, work_phone FROM contacts").fetchall()
        finally:
            conn.close()
        hits = []
        for r in rows:
            if r["id"] == exclude:
                continue
            why = []
            if email and email in {(r[k] or "").strip().lower() for k in ("email", "email_2", "email_3")}:
                why.append("same email" if _same_first(first, r["first_name"]) or not first else "same email, but a different first name, so possibly a couple")
            if cell and cell in {_digits(r[k]) for k in ("cell_phone", "home_phone", "work_phone")}:
                why.append("same phone")
            if first and last and (r["first_name"] or "").strip().lower() == first and (r["last_name"] or "").strip().lower() == last:
                why.append("same name")
            if why:
                hits.append({"id": r["id"], "name": f"{r['first_name']} {r['last_name'] or ''}".strip(), "email": r["email"] or "", "why": ", ".join(why),
                             "url": url_for(".contact_detail", contact_id=r["id"])})
        return jsonify(hits[:5])
