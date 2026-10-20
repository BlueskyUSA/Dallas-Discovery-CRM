"""Owner-only 'Database map': a layered, click-through picture of how the database is organized, with live counts.
Level 1 = the big picture, level 2 = the tables inside a block, level 3 = one table's fields and a few sample rows."""
import re

from flask import render_template, request, jsonify

# (key, title, color, what it is, [(table, plain-English description)])
BLOCKS = [
    ("people", "People", "#1f6f8b", "Everyone we know: alumni, volunteers, interested people, sponsors.", [
        ("contacts", "One row per person: name, email, phones, address, who sponsored them, where they came from."),
        ("contact_lists", "The data sources (lists) a person can belong to, such as Entire Discovery database."),
        ("contact_list_members", "Which person is on which list."),
        ("contact_photos", "Photos people have uploaded."),
        ("profile_relationship", "The Relationship Training (D4) profile: questions about the relationship. Stored in training_profiles."),
        ("profile_d1", "The D1 profile: basic questions now, the rest still to be determined (TBD). Stored in training_profiles."),
    ]),
    ("programs", "Programs and sessions", "#6a4c93", "What we teach and when: programs, scheduled sessions and their groups.", [
        ("programs", "The trainings we offer (D1, Relationship Training and so on)."),
        ("cohorts", "Each scheduled session of a program, with dates and capacity."),
        ("small_groups", "The small groups inside a session."),
        ("program_materials", "Training materials attached to a program."),
        ("program_playlists", "Music lists for a program."),
        ("playlist_songs", "The songs on those lists."),
        ("program_movies", "Movies used in a program."),
    ]),
    ("enroll", "Enrollment and training", "#2a7f62", "Who is enrolled in which session, who staffed it, and what they said afterward.", [
        ("enrollments", "One row each time a person is enrolled in a session."),
        ("cohort_staffing", "Who served on a session's team (Director, Facilitator, TA)."),
        ("small_group_staffing", "Who led each small group."),
        ("feedback_responses", "Answers people gave on questionnaires and feedback forms."),
        ("contracts", "Agreements people sign."),
        ("contract_revisions", "The earlier versions of each agreement."),
    ]),
    ("money", "Money", "#b5651d", "Gifts and donations.", [
        ("donations", "One row per donation."),
    ]),
    ("outreach", "Outreach and volunteers", "#c0392b", "People asking to hear from us, the Guides who answer, and our emails.", [
        ("interest_log", "Each time someone used a Curious / interest form."),
        ("call_requests", "A request to be called or texted, and how it went."),
        ("call_notices", "The alert sent to each Guide for a request."),
        ("call_volunteers", "The Guides, their phones and permissions."),
        ("volunteer_helpers", "Volunteers with a personal page, and who brought them in."),
        ("blast_audiences", "The groups an email blast can go to, and the wording."),
        ("blast_sends", "Each blast that was sent."),
        ("blast_recipients", "Who each blast went to, and whether it arrived."),
    ]),
    ("team", "Team and access", "#34495e", "Staff logins and what each can see.", [
        ("staff", "Staff and leadership accounts."),
        ("access_grants", "Extra areas a staff member has been given."),
    ]),
]

LINKS = [
    ("people", "enroll", "people enroll in sessions"),
    ("programs", "enroll", "sessions hold people and teams"),
    ("people", "money", "people give donations"),
    ("people", "outreach", "requests and Guides are people"),
    ("people", "team", "a login can link to a person"),
]

TABLES = {t: d for _k, _n, _c, _w, ts in BLOCKS for t, d in ts}
HIDE_ALWAYS = re.compile(r"password|token|consent_ip|push_user_key|secret", re.I)
MASK = re.compile(r"email|phone|cell|street|address|city|zip|birth|dob|notes|note|emergency|partner|sponsor_|consent_name|message|answer|response|body", re.I)


PROFILE_CODES = {"profile_relationship": "D4", "profile_d1": "D1"}
D1_TBD = ["relationship_length", "how_met", "childhood_patterns", "emotional_milestone", "relationship_strengths",
          "relationship_weaknesses", "stress_points", "song_artist", "song_feelings"]
PROFILE_JOIN = ("FROM training_profiles tp JOIN enrollments e ON e.id = tp.enrollment_id "
                "JOIN cohorts c ON c.id = e.cohort_id JOIN programs p ON p.id = c.program_id WHERE p.code = ?")


def profile_label(table, col):
    if table == "profile_d1" and col in D1_TBD:
        return f"question_{D1_TBD.index(col) + 3} (TBD)"
    return col


def columns_of(conn, table):
    if table in PROFILE_CODES:
        return [profile_label(table, c) for c in columns_of(conn, "training_profiles")]
    try:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position", (table,)).fetchall()
        if rows:
            return [r[0] if not hasattr(r, "keys") else r["column_name"] for r in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def count_of(conn, table):
    try:
        if table in PROFILE_CODES:
            r = conn.execute("SELECT COUNT(*) AS n " + PROFILE_JOIN, (PROFILE_CODES[table],)).fetchone()
            return r["n"] if hasattr(r, "keys") else r[0]
        r = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return r["n"] if hasattr(r, "keys") else r[0]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _first(row):
    """First value of a result row, whether it is a tuple-like or a dict-like row."""
    try:
        return row[0]
    except Exception:
        return list(row.values())[0]


# For these id columns the sample table also shows a readable name beside the number.
NAME_LOOKUPS = {
    "list_id": ("list_name", "SELECT name FROM contact_lists WHERE id = ?"),
    "contact_id": ("contact_name", "SELECT TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')) FROM contacts WHERE id = ?"),
    "program_id": ("program_name", "SELECT name FROM programs WHERE id = ?"),
    "cohort_id": ("session", "SELECT p.code || ' #' || c.session_number FROM cohorts c JOIN programs p ON p.id = c.program_id WHERE c.id = ?"),
    "enrollment_id": ("enrollee", "SELECT TRIM(COALESCE(ct.first_name,'') || ' ' || COALESCE(ct.last_name,'')) FROM enrollments e JOIN contacts ct ON ct.id = e.contact_id WHERE e.id = ?"),
    "audience_id": ("group_name", "SELECT name FROM blast_audiences WHERE id = ?"),
    "letter_id": ("letter_name", "SELECT title FROM blast_letters WHERE id = ?"),
}


def register(app, crm, owner_required, get_db):
    @crm.route("/database-map")
    @owner_required
    def database_map():
        conn = get_db()
        try:
            blocks = []
            for key, title, color, what, tables in BLOCKS:
                ts = [dict(name=t, desc=d, count=count_of(conn, t), cols=columns_of(conn, t)) for t, d in tables]
                blocks.append(dict(key=key, title=title, color=color, what=what, tables=ts,
                                   total=sum(t["count"] or 0 for t in ts)))
            return render_template("database_map.html", blocks=blocks,
                                   links=[dict(a=a, b=b, label=l) for a, b, l in LINKS])
        finally:
            conn.close()

    @crm.route("/database-map/sample/<table>")
    @owner_required
    def database_map_sample(table):
        if table not in TABLES:
            return jsonify(error="unknown table"), 404
        mask = request.args.get("mask", "1") != "0"
        conn = get_db()
        try:
            cols = columns_of(conn, table)
            shown = [c for c in cols if not HIDE_ALWAYS.search(c)]
            rows = []
            real = "training_profiles" if table in PROFILE_CODES else table
            real_of = {profile_label(table, c): c for c in columns_of(conn, real)} if table in PROFILE_CODES else {}
            if shown:
                sel = ", ".join(("tp." + real_of[c]) if real_of else c for c in shown)
                if table in PROFILE_CODES:
                    qs = [("SELECT " + sel + " " + PROFILE_JOIN + " ORDER BY tp.id DESC LIMIT 5", (PROFILE_CODES[table],))]
                else:
                    qs = [(f"SELECT {sel} FROM {table} ORDER BY id DESC LIMIT 5", ()), (f"SELECT {sel} FROM {table} LIMIT 5", ())]
                for q, qargs in qs:
                    try:
                        rows = conn.execute(q, qargs).fetchall()
                        break
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
            out = []
            for r in rows:
                line = []
                for i, c in enumerate(shown):
                    v = r[i] if not hasattr(r, "keys") else (r[real_of.get(c, c)] if real_of else r[c])
                    if v is None or v == "":
                        line.append("")
                    elif mask and MASK.search(c):
                        line.append("••••••")
                    else:
                        s = str(v)
                        line.append(s if len(s) <= 60 else s[:57] + "...")
                out.append(line)
            # show the name next to each id so the numbers mean something (list 1 = which list, contact 20 = who)
            for fk, (label, q) in list(NAME_LOOKUPS.items()):
                if fk not in shown:
                    continue
                pos = shown.index(fk)
                cache = {}
                for line in out:
                    key = line[pos]
                    if key not in cache:
                        try:
                            row = conn.execute(q, (key,)).fetchone() if key != "" else None
                            cache[key] = (_first(row) if row else "") or ""
                        except Exception:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            cache[key] = ""
                    line.insert(pos + 1, cache[key])
                shown = shown[:pos + 1] + [label] + shown[pos + 1:]
            return jsonify(columns=shown, rows=out, hidden=[c for c in cols if HIDE_ALWAYS.search(c)])
        finally:
            conn.close()
