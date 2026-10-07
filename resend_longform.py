"""
Re-sends the long-form follow-up email (the same one sent automatically
when someone checks "Volunteer" on the discovery short form) to a contact
in the CRM, looked up by email. Useful when someone needs their link
re-sent -- e.g. their earlier submission was merged with an older
duplicate contact and they should get a fresh link pointing at the
now-single record -- or just to send/test the long form against any
email address, even one not yet in the CRM (a contact is created for it).

If the contact doesn't already have a profile_token, this generates one.
If they do, it reuses the existing one (so any link already out there
keeps working) unless you pass --new-link to force a fresh one.

Run from Render's Shell:

    python3 resend_longform.py pamela@example.com
    python3 resend_longform.py pamela@example.com --new-link
    python3 resend_longform.py pamela@example.com --name Pamela
"""
import sys
import secrets
from db import get_db
from email_templates import longform_followup_content, LONGFORM_FOLLOWUP_SUBJECT
from email_utils import send_email, EmailSendError

from config import PUBLIC_BASE_URL as DISCOVERY_URL_BASE


def resend(to_email, force_new_link, first_name=None):
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE email = ?", (to_email,)).fetchone()
    if not contact:
        conn.execute(
            "INSERT INTO contacts (first_name, email, marketing_source) VALUES (?, ?, ?)",
            (first_name or "", to_email, "Long-form test/resend"),
        )
        conn.commit()
        contact = conn.execute("SELECT * FROM contacts WHERE email = ?", (to_email,)).fetchone()
        print(f"No existing contact for {to_email} -- created a new one (#{contact['id']}).")

    token = contact["profile_token"]
    if not token or force_new_link:
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE contacts SET profile_token = ? WHERE id = ?", (token, contact["id"]))
        conn.commit()
        print(f"Generated a {'new' if token else ''} profile_token for contact #{contact['id']}.")
    else:
        print(f"Reusing contact #{contact['id']}'s existing profile_token.")

    longform_url = f"{DISCOVERY_URL_BASE}/complete-profile/{token}"
    name_for_email = contact["first_name"] or first_name or ""
    html_content, text_content = longform_followup_content(name_for_email, longform_url)

    try:
        message_id = send_email(
            to_email=to_email,
            to_name=name_for_email or None,
            subject=LONGFORM_FOLLOWUP_SUBJECT,
            html_content=html_content,
            text_content=text_content,
        )
        print(f"Sent successfully to {name_for_email or to_email} <{to_email}>. Link: {longform_url}")
        print(f"Brevo message id: {message_id}")
    except EmailSendError as e:
        print(f"FAILED to send: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 resend_longform.py <email address> [--new-link] [--name <first name>]")
        sys.exit(1)

    args = sys.argv[2:]
    force_new_link = "--new-link" in args
    name_arg = None
    if "--name" in args:
        idx = args.index("--name")
        if idx + 1 < len(args):
            name_arg = args[idx + 1]

    resend(sys.argv[1].strip(), force_new_link=force_new_link, first_name=name_arg)
