# Dallas Discovery — CRM (v1)

A working starting point for the CRM/registration system, covering what we've designed so far:
Contacts, Programs (T1–T4), Sessions with permanent session numbers, Enrollment with the
T1→T2→T3 prerequisite check, questionnaire/payment tracking, small groups, TA/Facilitator
staffing, contracts (with revision history), and donations.

## Requirements

- Python 3.9+
- The packages in `requirements.txt` (Flask, gunicorn, psycopg, python-dotenv)
- A Postgres database — this app now uses a hosted Postgres database (not a local file),
  shared between this local app and the public client-portal website, so a submitted
  questionnaire shows up here immediately with no manual syncing.

## First-time setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in your real Postgres connection string
   (see the comments in that file for where to get it from Render).
3. If this is a brand-new database, create the tables once:
   `python3 -c "from db import init_db; init_db()"`
4. If you have existing data in a local `bluesky_crm.db` from before this change, run
   `python3 migrate_sqlite_to_postgres.py` once to copy it all into Postgres.

## Running it

```bash
python3 app.py
```

Then open http://localhost:5050 in your browser — that's the public marketing
site (home, programs, upcoming sessions/registration, donate). The staff CRM
now lives at http://localhost:5050/crm.

## Deploying the client-facing portal

`Procfile` and `requirements.txt` are set up for Render: push this repo to GitHub, connect
it as a Render web service, set `DATABASE_URL` in Render's environment settings to the same
Postgres database, and Render will run it with gunicorn. Because both this local app and the
Render deployment point at the same `DATABASE_URL`, they're always in sync.

The four programs (T1–T4) are seeded automatically on first run, with the T1→T2→T3
prerequisite chain already wired up.

## What's in here

- `app.py` — all routes/logic
- `schema.sql` — the database schema (this is the actual data model — start here to see the
  full picture)
- `seed.py` — seeds the T1–T4 programs
- `templates/` — the pages
- `static/style.css` — styling

## What's already working

- Add/search contacts, with referral tracking (who referred whom) and marketing source
- Schedule a new session for any program — session numbers auto-increment permanently per
  program and are not editable
- Enroll a trainee in a session — T2/T3 automatically check that the trainee attended the
  prerequisite program first, and block enrollment with an explanation if not
- Mark a trainee's life questionnaire and payment as complete independently (Enrollment shows an
  "outstanding" flag until both are done)
- Assign trainees to small groups, and TAs to small groups
- Assign Facilitators and floating Contract Support TAs at the session level
- Record a Contact's evolving "contract" statement with full revision history
- Record donations per contact, with a running total

## What's intentionally not built yet (next steps)

- Feedback form submission wired into the app (the draft questions live in the separate doc)
- Accounting integration (QuickBooks/Xero export)
- Marketing tool integration (Mailchimp/ActiveCampaign sync)
- Login/authentication — right now anyone with the URL has full access; needed before this
  goes anywhere beyond your own machine
- Role-based permissions (e.g., restricting who can see feedback/contract data)
- A real Contact "Role" model (Donor/TA/Facilitator are just a status field for now, not full
  multi-role tracking)

## A note on the stack

This was built in Flask + SQLite instead of the originally-planned Next.js/Postgres stack because
the sandbox this was built in couldn't reach npm's package registry. Functionally it's the same
design — the schema in `schema.sql` is the real deliverable, and a developer can port this to
another stack without much friction since the data model doesn't change.
