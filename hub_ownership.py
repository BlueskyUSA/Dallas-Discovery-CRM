"""Hub Ownership page (Administrator menu): the legal entities that own and use the Hub, the documents that prove it,
and the professionals involved. Owner and Administrators. No passwords, no personal Social Security or bank account numbers."""
from flask import render_template, request, redirect, url_for, session, flash

TABLES = {
    "entity": ("ownership_entities", ["name", "kind", "state", "formed_on", "ein", "address", "registered_agent", "status", "notes"]),
    "doc": ("ownership_documents", ["entity", "title", "status", "doc_date", "where_kept", "link", "notes"]),
    "person": ("ownership_people", ["role", "name", "firm", "email", "phone", "notes"]),
}
DOC_STATUS = ["Not started", "In progress", "Done"]
ENTITY_STATUS = ["Planned", "In progress", "Active"]

SEED_ENTITIES = [
    ("Software and equipment LLC (name to be chosen)", "LLC", "Texas", "Owns the Hub software, the IP and equipment; licenses the software to the nonprofit for its monthly cost (about $200 per month)", "Planned"),
    ("Nonprofit running the trainings (name to be chosen)", "501(c)(3) nonprofit", "Texas", "Runs the trainings, collects fees and donations; pays the LLC the monthly license fee", "Planned"),
    ("InVision Technologies, Inc.", "Corporation", "Texas", "Existing corporation (Coppell, TX)", "Active"),
]
SEED_DOCS = [
    ("Software LLC", "Articles of Organization (Texas Secretary of State)"),
    ("Software LLC", "Operating agreement"),
    ("Software LLC", "EIN confirmation letter from the IRS"),
    ("Software LLC", "Software license agreement with the nonprofit (monthly cost-based fee)"),
    ("Software LLC", "Assignment of the Hub software and code to the LLC"),
    ("Nonprofit", "Certificate of formation"),
    ("Nonprofit", "Bylaws and board list"),
    ("Nonprofit", "EIN confirmation letter from the IRS"),
    ("Nonprofit", "IRS determination letter (501(c)(3))"),
    ("Both", "Agreement with Keith / purchase of the Discovery assets, if reached"),
    ("Both", "Trademark filings and clearance"),
]
SEED_PEOPLE = [("Attorney", ""), ("Accountant / bookkeeper", ""), ("Registered agent", "")]


def register(app, crm, owner_required, get_db):
    def ensure(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS ownership_entities (id SERIAL PRIMARY KEY, name TEXT, kind TEXT, state TEXT,
            formed_on TEXT, ein TEXT, address TEXT, registered_agent TEXT, status TEXT, notes TEXT, updated_by TEXT, updated_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ownership_documents (id SERIAL PRIMARY KEY, entity TEXT, title TEXT, status TEXT,
            doc_date TEXT, where_kept TEXT, link TEXT, notes TEXT, updated_by TEXT, updated_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ownership_people (id SERIAL PRIMARY KEY, role TEXT, name TEXT, firm TEXT,
            email TEXT, phone TEXT, notes TEXT, updated_by TEXT, updated_at TEXT)""")
        if not conn.execute("SELECT 1 FROM ownership_entities LIMIT 1").fetchone():
            for n, k, st, notes, status in SEED_ENTITIES:
                conn.execute("INSERT INTO ownership_entities (name, kind, state, notes, status) VALUES (?,?,?,?,?)", (n, k, st, notes, status))
            for e, t in SEED_DOCS:
                conn.execute("INSERT INTO ownership_documents (entity, title, status) VALUES (?,?,'Not started')", (e, t))
            for r, n in SEED_PEOPLE:
                conn.execute("INSERT INTO ownership_people (role, name) VALUES (?,?)", (r, n))
        conn.commit()

    @crm.route("/hub-ownership")
    @owner_required
    def hub_ownership():
        conn = get_db()
        try:
            ensure(conn)
            entities = conn.execute("SELECT * FROM ownership_entities ORDER BY id").fetchall()
            docs = conn.execute("SELECT * FROM ownership_documents ORDER BY id").fetchall()
            people = conn.execute("SELECT * FROM ownership_people ORDER BY id").fetchall()
        finally:
            conn.close()
        return render_template("hub_ownership.html", entities=entities, docs=docs, people=people,
                               doc_status=DOC_STATUS, entity_status=ENTITY_STATUS)

    @crm.route("/hub-ownership/<kind>/save", methods=["POST"])
    @owner_required
    def hub_ownership_save(kind):
        if kind not in TABLES:
            return redirect(url_for(".hub_ownership"))
        table, cols = TABLES[kind]
        f = request.form
        vals = [(f.get(c) or "").strip() for c in cols]
        if not any(vals[:2]):
            flash("Nothing to save.")
            return redirect(url_for(".hub_ownership") + "#" + kind)
        who = session.get("staff_name")
        conn = get_db()
        try:
            ensure(conn)
            rid = f.get("id")
            if rid:
                sets = ", ".join(f"{c} = ?" for c in cols)
                conn.execute(f"UPDATE {table} SET {sets}, updated_by = ?, updated_at = to_char(now(), 'YYYY-MM-DD') WHERE id = ?",
                             vals + [who, rid])
            else:
                names = ", ".join(cols)
                marks = ", ".join("?" for _ in cols)
                conn.execute(f"INSERT INTO {table} ({names}, updated_by, updated_at) VALUES ({marks}, ?, to_char(now(), 'YYYY-MM-DD'))",
                             vals + [who])
            conn.commit()
        finally:
            conn.close()
        flash("Saved.")
        return redirect(url_for(".hub_ownership") + "#" + kind)

    @crm.route("/hub-ownership/<kind>/<int:rid>/delete", methods=["POST"])
    @owner_required
    def hub_ownership_delete(kind, rid):
        if kind in TABLES:
            conn = get_db()
            try:
                conn.execute(f"DELETE FROM {TABLES[kind][0]} WHERE id = ?", (rid,))
                conn.commit()
            finally:
                conn.close()
            flash("Removed.")
        return redirect(url_for(".hub_ownership") + "#" + kind)
