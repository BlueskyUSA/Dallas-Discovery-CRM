"""At a Glance: one page that shows, for each upcoming training, how many are interested, reserved, ready and paid,
how many seats are left against the goal, how each Guide is doing, and who needs a nudge."""
from datetime import date, datetime
from flask import render_template, session

DEFAULT_GOAL_PEOPLE = {"D4": 80}   # 40 couples for The Art of Us, until the session's seat count is set


def _days_since(ts):
    try:
        return (date.today() - datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()).days
    except Exception:
        return None


def build(conn, pay_info, can_money):
    import call_flow
    out = []
    cohorts = conn.execute(
        """SELECT c.id, c.session_date, c.end_date, c.capacity, p.code, p.name
           FROM cohorts c JOIN programs p ON p.id = c.program_id
           WHERE COALESCE(c.end_date, c.session_date) >= ? AND COALESCE(c.status, '') <> 'Cancelled'
             AND p.code NOT IN ('D2','D3')
           ORDER BY c.session_date ASC, p.id ASC LIMIT 4""", (date.today().isoformat(),)).fetchall()
    for c in cohorts:
        roster = conn.execute(
            """SELECT e.id, e.contact_id, e.partner_enrollment_id, e.questionnaire_completed_at, e.payment_completed_at,
                      e.created_at, ct.first_name, ct.last_name
               FROM enrollments e JOIN contacts ct ON ct.id = e.contact_id WHERE e.cohort_id = ? ORDER BY e.created_at""",
            (c["id"],)).fetchall()
        info = pay_info(conn, c["id"], roster)
        paid_ids = set()
        for e in roster:
            if (info.get(e["id"]) or {}).get("total", 0) > 0 or e["payment_completed_at"]:
                paid_ids.add(e["contact_id"])
        people = len(roster)
        paired = sum(1 for e in roster if e["partner_enrollment_id"])
        couples = paired // 2 + (people - paired)
        q_done = sum(1 for e in roster if e["questionnaire_completed_at"])
        paid = len(paid_ids)
        goal = c["capacity"] or DEFAULT_GOAL_PEOPLE.get(c["code"])
        total_money = None
        if can_money:
            r = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE cohort_id = ? AND voided_at IS NULL", (c["id"],)).fetchone()
            total_money = r["s"] if r else 0
        reqs = conn.execute(
            """SELECT r.*, v.name AS guide_name FROM call_requests r LEFT JOIN call_volunteers v ON v.id = r.claimed_by
               WHERE r.program_code = ?""", (c["code"],)).fetchall()
        interested = len(reqs)
        talked = sum(1 for r in reqs if r["outcome"] and not str(r["outcome"]).startswith(("Wrong number",)))
        linked = sum(1 for r in reqs if r["link_sent_at"] or r["reserve_result"])
        reserved_reqs = [r for r in reqs if r["reserve_result"] == "Enrolled" and r["reserve_cohort_id"] == c["id"]]
        waitlist = sum(1 for r in reqs if r["reserve_result"] == "Waitlist" and r["reserve_cohort_id"] == c["id"])
        unclaimed = sum(1 for r in reqs if (r["status"] or "New") == "New" and (_days_since(r["created_at"]) or 0) >= 1)
        guides = {}
        for r in reqs:
            g = r["guide_name"] or "Not yet picked up"
            d = guides.setdefault(g, dict(name=g, requests=0, talked=0, linked=0, reserved=0, paid=0))
            d["requests"] += 1
            if r["outcome"] and not str(r["outcome"]).startswith("Wrong number"): d["talked"] += 1
            if r["link_sent_at"] or r["reserve_result"]: d["linked"] += 1
            if r in reserved_reqs:
                d["reserved"] += 1
                if r["contact_id"] in paid_ids: d["paid"] += 1
        attention = []
        for e in roster:
            notes = []
            if not e["questionnaire_completed_at"]: notes.append("questionnaire not done")
            if e["contact_id"] not in paid_ids: notes.append("not paid")
            if notes:
                attention.append(dict(contact_id=e["contact_id"], name=f"{e['first_name']} {e['last_name']}".strip(),
                                      what=", ".join(notes), days=_days_since(e["created_at"])))
        attention.sort(key=lambda a: -(a["days"] or 0))
        try:
            start = date.fromisoformat(c["session_date"])
            days_away = (start - date.today()).days
        except Exception:
            days_away = None
        label = {"D1": "The Beginning", "D4": "The Art of Us"}.get(c["code"], c["name"])
        goal_couples = goal // 2 if (goal and c["code"] == "D4") else None
        out.append(dict(id=c["id"], label=label, code=c["code"], date=c["session_date"], days_away=days_away,
                        people=people, couples=couples, goal=goal, goal_couples=goal_couples,
                        seats_left=(goal - people) if goal else None,
                        pct=(min(100, round(100 * people / goal)) if goal else None),
                        interested=interested, talked=talked, linked=linked, reserved=people,
                        waitlist=waitlist, q_done=q_done, paid=paid, money=total_money, unclaimed=unclaimed,
                        guides=sorted(guides.values(), key=lambda d: (d["name"] == "Not yet picked up", -d["requests"])),
                        attention=attention[:25], attention_total=len(attention)))
    return out


def register(app, crm, leadership_required, get_db, pay_info, can_money):
    @crm.route("/at-a-glance")
    @leadership_required
    def at_a_glance():
        import call_flow
        call_flow.ensure_tables(get_db)
        conn = get_db()
        try:
            data = build(conn, pay_info, can_money())
        finally:
            conn.close()
        return render_template("at_a_glance.html", sessions=data, as_of=datetime.now().strftime("%b %d, %Y"))
