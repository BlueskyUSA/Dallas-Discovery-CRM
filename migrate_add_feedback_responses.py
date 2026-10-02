"""
One-time migration: adds the "Bluesky Feedback Form" feature to an existing
Postgres database.

What it does:
  - Adds a feedback_token column to enrollments (a unique, unguessable token
    used to build each trainee's personal feedback link -- generated on
    demand from the CRM's Cohort page, not by this script).
  - Creates the feedback_responses table: one row per submitted form, linked
    to enrollment_id (which is how a response auto-ties back to that
    trainee's Cohort/Program, Small Group -> TA(s), and Cohort Staffing ->
    Facilitator, without ever asking the trainee to name anyone).

Nothing here touches existing data -- both changes are purely additive.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_feedback_responses.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'enrollments'"
    )
    enrollment_cols = [row["column_name"] for row in cur.fetchall()]

    if "feedback_token" not in enrollment_cols:
        cur.execute("ALTER TABLE enrollments ADD COLUMN feedback_token TEXT UNIQUE")
        print("Added enrollments.feedback_token.")
    else:
        print("enrollments.feedback_token already exists.")

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'feedback_responses'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE feedback_responses (
                id SERIAL PRIMARY KEY,
                enrollment_id INTEGER NOT NULL UNIQUE REFERENCES enrollments(id),
                facilitator_clear INTEGER,
                facilitator_safe INTEGER,
                facilitator_responsive INTEGER,
                facilitator_did_well TEXT,
                facilitator_could_improve TEXT,
                ta_safe INTEGER,
                ta_focused INTEGER,
                ta_individual_attention INTEGER,
                ta_did_well TEXT,
                ta_could_improve TEXT,
                practical_tools INTEGER,
                emotionally_safe INTEGER,
                would_recommend INTEGER,
                future_training_likelihood INTEGER,
                uncomfortable_notes TEXT,
                other_comments TEXT,
                submitted_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            )"""
        )
        print("Created feedback_responses table.")
    else:
        print("feedback_responses table already exists.")

    conn.commit()
    conn.close()
    print("Done. Generate feedback links from a session's roster on the Cohort page in the CRM, "
          "and view submitted responses at /crm/feedback (not linked from the main nav on purpose).")


if __name__ == "__main__":
    migrate()
