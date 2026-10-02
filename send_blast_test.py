"""
One-off test: sends the real "Excitement is Building" email (the same
content the full blast will use) to a single contact already in the CRM,
looked up by email address. Used to verify the actual copy/links/formatting
before sending to the full contact list.

Run from Render's Shell, passing the contact's email:

    python3 send_blast_test.py pamela@example.com
"""
import sys
from db import get_db
from email_templates import excitement_blast_content, EXCITEMENT_BLAST_SUBJECT
from email_utils import send_email, EmailSendError

if len(sys.argv) < 2:
    print("Usage: python3 send_blast_test.py <contact's email address>")
    sys.exit(1)

to_email = sys.argv[1].strip()

conn = get_db()
contact = conn.execute("SELECT * FROM contacts WHERE email = ?", (to_email,)).fetchone()
conn.close()

if not contact:
    print(f"No contact found in the CRM with email {to_email}. Add them first, or check the address.")
    sys.exit(1)

first_name = contact["first_name"] or ""
html_content, text_content = excitement_blast_content(first_name)

try:
    message_id = send_email(
        to_email=to_email,
        to_name=first_name or None,
        subject=EXCITEMENT_BLAST_SUBJECT,
        html_content=html_content,
        text_content=text_content,
    )
    print(f"Sent successfully to {first_name} <{to_email}>. Brevo message id: {message_id}")
except EmailSendError as e:
    print(f"FAILED to send: {e}")
    sys.exit(1)
