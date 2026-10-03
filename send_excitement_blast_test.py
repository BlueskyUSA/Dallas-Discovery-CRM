"""
One-off: sends the "Excitement is Building" blast email -- the same one
meant for the eventual full outreach to past Discovery volunteers/TAs -- to
khres53@gmail.com, so Kent can see exactly what it looks like and test both
its signup link and its "reach out to me personally" link.

Finds or creates a test contact for khres53@gmail.com so that link has a
real profile_token to point at, same as the real excitement-email button
in the CRM does.

Not wired into any route -- just run by hand from the Render shell:

    python3 send_excitement_blast_test.py

Safe to run more than once -- it just sends another copy (and reuses the
same test contact/token rather than making a new one each time).
"""
import secrets

from db import get_db
from email_templates import excitement_blast_content, EXCITEMENT_BLAST_SUBJECT
from email_utils import send_email, EmailSendError

TO_EMAIL = "khres53@gmail.com"
SITE_BASE = "https://dallas-discovery-crm.onrender.com"


def main():
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE email = ?", (TO_EMAIL,)).fetchone()
    if contact:
        contact_id = contact["id"]
        token = contact["profile_token"]
    else:
        token = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO contacts (first_name, email, profile_token, status) VALUES (?, ?, ?, ?)",
            ("Kent", TO_EMAIL, token, "Interested Party"),
        )
        conn.commit()
        contact_id = conn.execute("SELECT id FROM contacts WHERE email = ?", (TO_EMAIL,)).fetchone()["id"]

    if not token:
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (token, contact_id))
        conn.commit()
    conn.close()

    connect_url = f"{SITE_BASE}/connect/{token}"
    html_content, text_content = excitement_blast_content(first_name="Kent", connect_url=connect_url)
    try:
        message_id = send_email(
            to_email=TO_EMAIL,
            subject=EXCITEMENT_BLAST_SUBJECT,
            html_content=html_content,
            text_content=text_content,
        )
    except EmailSendError as e:
        print(f"FAILED to send: {e}")
        return
    print(f"Sent to {TO_EMAIL} -- Brevo message id: {message_id}")
    print(f"Connect link in this email: {connect_url}")


if __name__ == "__main__":
    main()
