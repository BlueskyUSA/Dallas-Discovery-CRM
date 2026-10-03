"""
One-off: sends the "Excitement is Building" blast email -- the same one
meant for the eventual full outreach to past Discovery volunteers/TAs -- to
khres53@gmail.com, so Kent can see exactly what it looks like and test its
link (now pointing at dallas-discovery-crm.onrender.com/discovery).

Not wired into any route -- just run by hand from the Render shell:

    python3 send_excitement_blast_test.py

Safe to run more than once -- it just sends another copy.
"""
from email_templates import excitement_blast_content, EXCITEMENT_BLAST_SUBJECT
from email_utils import send_email, EmailSendError

TO_EMAIL = "khres53@gmail.com"


def main():
    html_content, text_content = excitement_blast_content(first_name="Kent")
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


if __name__ == "__main__":
    main()
