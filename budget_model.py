"""Training budgets: the structure, defaults and arithmetic.

One budget belongs to one training session (cohort). It is stored as JSON: a few
"key variables" plus a list of lines, each with a quantity, a unit cost and a number
of times (nights or days). Quantity / unit cost can also be a link to a shared value:
"=couples" (number of couples), "=people" (couples x 2 + team members), "=rack" (hotel
rack rate per room per night). Everything else is arithmetic, so the page and the
Excel template always agree.
"""
import copy
import json
import math

KEY_VARS = [
    ("rack", "Hotel rack rate per room per night", 108.99, "money2",
     "PLACEHOLDER: one guest's average Fri-Sun rate here in Oct. 2025, not the rack rate. Replace with the hotel's quote."),
    ("room_discount", "Negotiated discount off room rate", 0, "pct", "Percent off the rack rate (0% = none)."),
    ("room_tax", "Hotel tax on rooms", 0.13, "pct", "Total taxes of 13% listed for this hotel. Confirm."),
    ("food_discount", "Negotiated discount off food and beverage", 0, "pct", "Percent off hotel food prices (0% = none)."),
    ("food_service", "Banquet service charge and tax", 0.25, "pct", "ESTIMATE: hotels commonly add service charge plus tax to food. Confirm."),
    ("coffee_on", "Include hotel coffee service (1 = yes, 0 = no)", 1, "int", "Set to 0 to drop the coffee and snack service."),
    ("team_people", "Team members attending (people)", 16, "int", "ESTIMATE: 2 per paid room. Used for meals."),
    ("card_rate", "Credit card processing rate", 0.029, "pct", "ESTIMATE. Plus the fixed fee per payment on the Processing Fees line."),
    ("contingency", "Contingency", 0.05, "pct", "Percent of operating expenses held in reserve."),
]


def _L(id, section, label, q, u, t, mode, note=""):
    return {"id": id, "section": section, "label": label, "q": q, "u": u, "t": t, "mode": mode, "note": note}


# modes: plain (q*u*t) | room (rooms: discount + tax) | food (discount + service) | coffee (food x on/off)
#        fee (processing: q*u*t + card rate x program fees)
DEFAULT_LINES = [
    _L("fee_income", "income", "Program Fee Income", 40, 1200, 1, "plain", "Couples x price per couple ($1,200; one payment covers both partners)"),
    _L("merch", "income", "Merchandise Sales Income", 0, 0, 1, "plain", "None planned"),
    _L("donations", "income", "Unrestricted Financial Partnerships / donations", 0, 0, 1, "plain", "None assumed"),
    _L("rooms", "venue", "Participant hotel rooms", "=couples", "=rack", 2, "room", "One room per couple x 2 nights x rack rate, less discount, plus tax"),
    _L("meeting", "venue", "Meeting room / convention center", 1, 0, 1, "plain", "Assumed free with the room block; enter a fee if the hotel charges"),
    _L("dj", "venue", "DJ / music for the dance", 1, 0, 1, "plain", "Removed"),
    _L("fac_rooms", "team", "Facilitator rooms", 2, "=rack", 2, "room", "2 rooms for facilitators"),
    _L("ta_rooms", "team", "TA rooms", 5, "=rack", 2, "room", "5 rooms for TAs"),
    _L("md_room", "team", "Music director room", 1, "=rack", 2, "room", "1 room"),
    _L("fri_hors", "meals", "Friday hors d'oeuvres", "=people", 18, 1, "food", "All attendees x per person; ESTIMATE, replace with banquet quote"),
    _L("sat_hors", "meals", "Saturday hors d'oeuvres", "=people", 18, 1, "food", ""),
    _L("sat_dinner", "meals", "Saturday dinner", "=people", 45, 1, "food", ""),
    _L("coffee", "meals", "Coffee, tea, water and snacks", "=people", 14, 3, "coffee", "Per person per day x 3 days; switch off in Key variables"),
    _L("supplies", "other", "Training Supplies & Support", 1, 1500, 1, "plain", "ESTIMATE (name tags, workbooks, printing)"),
    _L("marketing", "other", "Marketing & Awareness", 1, 500, 1, "plain", "ESTIMATE"),
    _L("office", "other", "General Office Support", 1, 400, 1, "plain", "Office supplies, software, postage; ESTIMATE"),
    _L("uniforms", "other", "Uniforms", 1, 0, 1, "plain", "Not budgeted for the first training"),
    _L("gifts", "other", "Welcome gifts left in the room for trainees", "=trainees", 5, 1, "plain", "One small gift per trainee, set out before they arrive; $5 each is an ESTIMATE"),
    _L("processing", "other", "Processing Fees", "=couples", 0.30, 1, "fee", "Fixed fee per payment (one per couple) + card rate x program fees"),
    _L("file_cabinet", "onetime", "Rolling plastic file cabinet", 1, 60, 1, "plain", "Holds extra handouts, mints, gum, pens and other training supplies; ESTIMATE"),
    _L("speakers", "onetime", "Speakers (powered)", 2, 400, 1, "plain", "2 speakers; ESTIMATE"),
    _L("stands", "onetime", "Speaker stands", 2, 50, 1, "plain", "ESTIMATE"),
    _L("tv", "onetime", "64-inch TV", 1, 500, 1, "plain", "ESTIMATE"),
    _L("tv_stand", "onetime", "Rolling TV stand / platform", 1, 150, 1, "plain", "ESTIMATE"),
    _L("mics", "onetime", "Trainee microphones", 2, 120, 1, "plain", "2 microphones for trainees; ESTIMATE"),
    _L("lavs", "onetime", "Lavalier microphones", 3, 150, 1, "plain", "3 lavaliers; ESTIMATE (wireless sets cost more)"),
    _L("mixer", "onetime", "Audio mixer", 1, 250, 1, "plain", "Connects laptop/iPhone music, microphones and speakers; ESTIMATE"),
    _L("cables", "onetime", "Cables and adapters (laptop and iPhone)", 1, 100, 1, "plain", "ESTIMATE, include spares"),
    _L("banners", "onetime", "Printed banners (about 20 ft each)", 3, 250, 1, "plain",
       "Banner 1: \"You can't change or heal what you don't acknowledge.\" Banners 2 and 3: text to be decided"),
    _L("insurance", "onetime", "General Liability Insurance (annual premium)", 1, 1825, 1, "plain",
       "PLACEHOLDER: Austin paid $1,825 for 2025. Replace with a written quote. Paid once a year."),
    _L("npo", "onetime", "501(c)(3) setup (legal and filing)", 1, 3000, 1, "plain", "$3,000 one-time to set up the nonprofit"),
]

SECTIONS = [
    ("income", "Income"),
    ("venue", "Venue"),
    ("team", "Team hotel rooms (paid by the program)"),
    ("meals", "Meals"),
    ("other", "Other operating expenses"),
    ("onetime", "One-time expenses (equipment, banners, insurance, nonprofit setup)"),
]


def default_budget(title=""):
    return {"title": title, "vars": {k: v for k, _, v, _, _ in KEY_VARS}, "lines": copy.deepcopy(DEFAULT_LINES)}


def loads(text):
    try:
        data = json.loads(text)
        data.setdefault("vars", {})
        for k, _, v, _, _ in KEY_VARS:
            data["vars"].setdefault(k, v)
        data.setdefault("lines", copy.deepcopy(DEFAULT_LINES))
        return data
    except (TypeError, ValueError):
        return default_budget()


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def compute(data):
    """Returns (rows, totals). rows keep the input order and gain 'amount', 'qv', 'uv' (resolved quantity / unit)."""
    v = {k: _num(data["vars"].get(k)) for k, *_ in KEY_VARS}
    lines = data["lines"]
    by_id = {ln["id"]: ln for ln in lines}
    couples = _num(by_id.get("fee_income", {}).get("q"), 0)
    people = 2 * couples + v["team_people"]
    links = {"=couples": couples, "=trainees": 2 * couples, "=people": people, "=rack": v["rack"]}

    def resolve(x):
        return links[x] if isinstance(x, str) and x in links else _num(x)

    room_f = (1 - v["room_discount"]) * (1 + v["room_tax"])
    food_f = (1 - v["food_discount"]) * (1 + v["food_service"])
    rows = []
    fee_income = 0.0
    for ln in lines:
        q, u, t = resolve(ln["q"]), resolve(ln["u"]), _num(ln["t"], 1)
        base = q * u * t
        mode = ln.get("mode", "plain")
        if mode == "room":
            amt = base * room_f
        elif mode == "food":
            amt = base * food_f
        elif mode == "coffee":
            amt = base * v["coffee_on"] * food_f
        else:
            amt = base
        if ln["id"] == "fee_income":
            fee_income = amt
        rows.append(dict(ln, qv=q, uv=u, tv=t, amount=amt))
    for r in rows:
        if r["mode"] == "fee":
            r["amount"] += fee_income * v["card_rate"]
    tot = {}
    for sec, _ in SECTIONS:
        tot[sec] = sum(r["amount"] for r in rows if r["section"] == sec)
    operating = tot["venue"] + tot["team"] + tot["meals"] + tot["other"]
    contingency = operating * v["contingency"]
    expenses = operating + contingency + tot["onetime"]
    net = tot["income"] - expenses
    couples_n = couples or 1
    price_net = _num(by_id.get("fee_income", {}).get("u")) * (1 - v["card_rate"])
    totals = dict(tot, operating=operating, contingency=contingency, expenses=expenses, net=net,
                  net_before_onetime=net + tot["onetime"], per_couple_expense=expenses / couples_n,
                  per_couple_net=net / couples_n,
                  breakeven=(math.ceil(expenses / price_net) if price_net > 0 else None), couples=couples, people=people)
    return rows, totals


def apply_form(data, form):
    """Updates key variables and the editable numbers of each line from a posted form. Linked values stay linked."""
    for k, _lbl, _d, kind, _n in KEY_VARS:
        if f"var_{k}" in form:
            val = _num(form.get(f"var_{k}"), data["vars"].get(k, 0) * (100 if kind == "pct" else 1))
            data["vars"][k] = val / 100 if kind == "pct" else val
    for ln in data["lines"]:
        for field in ("q", "u", "t"):
            key = f"{field}_{ln['id']}"
            if key in form and not (isinstance(ln[field], str) and ln[field].startswith("=")):
                ln[field] = _num(form.get(key), ln[field])
        if f"note_{ln['id']}" in form:
            ln["note"] = (form.get(f"note_{ln['id']}") or "")[:300]
    return data
