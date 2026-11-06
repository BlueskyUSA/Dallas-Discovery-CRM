"""Dashboard alerts: things in the Hub that need someone's attention.
Every check is independent and wrapped, so one failing never breaks the Dashboard."""
from datetime import date, datetime, timedelta


def _plural(n, one, many=None):
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def collect(conn, is_owner, is_lead, can_money, label_of, url_for, today=None):
    """Returns a list of dicts: level ('urgent' or 'note'), text, url, and optional links [(label, url)]."""
    today = today or date.today()
    out = []

    def safe(fn):
        try:
            fn()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"dashboard alert skipped: {e}", flush=True)

    # Upcoming sessions (next 45 days; Awakening and Vision ride along with The Beginning)
    sessions = []

    def load_sessions():
        rows = conn.execute(
            """SELECT c.id, c.session_date, c.end_date, p.code, p.name FROM cohorts c
               JOIN programs p ON p.id = c.program_id
               WHERE COALESCE(c.end_date, c.session_date) >= ? AND COALESCE(c.status, '') <> 'Cancelled'
                 AND p.code NOT IN ('D2','D3') ORDER BY c.session_date""", (today.isoformat(),)).fetchall()
        for r in rows:
            try:
                start = date.fromisoformat(r["session_date"][:10])
            except Exception:
                continue
            days = (start - today).days
            if days <= 45:
                sessions.append(dict(id=r["id"], days=days, start=start, name=label_of(r["code"], r["name"])))
    safe(load_sessions)

    def when(s):
        return s["start"].strftime("%b %-d") + (" (today)" if s["days"] == 0 else f", {s['days']} days away" if s["days"] > 0 else "")

    if is_lead:
        def call_requests():
            cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            n = conn.execute(
                """SELECT COUNT(*) AS n FROM call_requests WHERE COALESCE(status,'New') = 'New'
                   AND claimed_by IS NULL AND REPLACE(created_at, 'T', ' ') < ?""", (cutoff,)).fetchone()["n"]
            if n:
                out.append(dict(level="urgent", text=f"{_plural(n, 'request')} from the website {'has' if n == 1 else 'have'} had no Guide response for over a day.",
                                url=url_for(".call_requests_queue"), cta="Open Call requests"))
        safe(call_requests)

        def team_gaps():
            for s in sessions:
                missing = []
                if not conn.execute("SELECT 1 FROM cohort_staffing WHERE cohort_id = ? AND role = 'Lead Facilitator'", (s["id"],)).fetchone():
                    missing.append("Lead Facilitator")
                if not conn.execute("SELECT 1 FROM cohort_staffing WHERE cohort_id = ? AND duty = 'Team Captain'", (s["id"],)).fetchone():
                    missing.append("Team Captain")
                if missing:
                    out.append(dict(level="urgent" if s["days"] <= 14 else "note",
                                    text=f"{s['name']} ({when(s)}) has no {' or '.join(missing)} yet.",
                                    url=url_for(".cohort_detail", cohort_id=s["id"]), cta="Open the session"))
        safe(team_gaps)

        def team_needs():
            import needs
            for s in sessions:
                if s["days"] > 30:
                    continue
                n = len(needs.team_without_answers(conn, s["id"]))
                if n:
                    out.append(dict(level="note", text=f"{_plural(n, 'team member')} for {s['name']} ({when(s)}) {'has' if n == 1 else 'have'} not answered the food and needs questions.",
                                    url=url_for(".cohort_detail", cohort_id=s["id"]) + "#sec-team", cta="Open the session"))
        safe(team_needs)

        def questionnaires():
            for s in sessions:
                if s["days"] > 30:
                    continue
                n = conn.execute("SELECT COUNT(*) AS n FROM enrollments WHERE cohort_id = ? AND questionnaire_completed_at IS NULL", (s["id"],)).fetchone()["n"]
                if n:
                    out.append(dict(level="note", text=f"{_plural(n, 'trainee')} for {s['name']} ({when(s)}) {'has' if n == 1 else 'have'} not finished the questionnaire.",
                                    url=url_for(".cohort_detail", cohort_id=s["id"]) + "#sec-roster", cta="Open the session"))
        safe(questionnaires)

        def failed_emails():
            since = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%d")
            rows = conn.execute(
                """SELECT DISTINCT m.contact_id, c.first_name, c.last_name FROM contact_messages m
                   JOIN contacts c ON c.id = m.contact_id
                   WHERE m.status LIKE 'Failed%' AND m.sent_at >= ? LIMIT 30""", (since,)).fetchall()
            if rows:
                links = [((f"{r['first_name'] or ''} {r['last_name'] or ''}").strip() or "Contact", url_for(".contact_detail", contact_id=r["contact_id"])) for r in rows[:4]]
                out.append(dict(level="urgent", text=f"{_plural(len(rows), 'person', 'people')} did not receive an email in the last 2 weeks (it failed to send). Check their address.",
                                links=links))
        safe(failed_emails)

    if can_money or is_lead:
        def unpaid():
            for s in sessions:
                if s["days"] > 30:
                    continue
                n = conn.execute("SELECT COUNT(*) AS n FROM enrollments WHERE cohort_id = ? AND payment_completed_at IS NULL", (s["id"],)).fetchone()["n"]
                if n:
                    out.append(dict(level="urgent" if s["days"] <= 14 else "note",
                                    text=f"{_plural(n, 'trainee')} for {s['name']} ({when(s)}) {'has' if n == 1 else 'have'} not paid yet.",
                                    url=url_for(".cohort_detail", cohort_id=s["id"]) + "#sec-roster", cta="Open the session"))
        safe(unpaid)

    if is_owner:
        def suggestions():
            n = conn.execute("SELECT COUNT(*) AS n FROM letter_suggestions WHERE status = 'New'").fetchone()["n"]
            if n:
                out.append(dict(level="note", text=f"{_plural(n, 'suggested letter change')} from volunteers {'is' if n == 1 else 'are'} waiting for you.",
                                url=url_for(".correspondence_all") + "#suggestions", cta="Read suggestions"))
        safe(suggestions)

        def testimonials():
            n = conn.execute("SELECT COUNT(*) AS n FROM testimonials WHERE status = 'New'").fetchone()["n"]
            if n:
                out.append(dict(level="note", text=f"{_plural(n, 'testimonial')} {'is' if n == 1 else 'are'} waiting for review.",
                                url=url_for(".testimonials_review"), cta="Open Testimonials"))
        safe(testimonials)

    out.sort(key=lambda a: 0 if a["level"] == "urgent" else 1)
    return out
