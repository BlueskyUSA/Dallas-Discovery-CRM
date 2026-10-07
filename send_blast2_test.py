"""
One-off: sends the Email Blast #2 cover letter (for the Dallas Discovery
contact database) to ONE address -- khres53@gmail.com, Kent's own test
inbox -- so Kent can see exactly how it will look in a real inbox.

It does NOT touch the contact list and sends to nobody else. Run by hand
from the Render shell:

    python3 send_blast2_test.py

Safe to run more than once -- it just sends another copy.
"""
from email_templates import blast2_cover_content, BLAST2_SUBJECT
from email_utils import send_email, EmailSendError

TO_EMAIL = "khres53@gmail.com"


def main():
    html_content, text_content = blast2_cover_content(first_name="Kent")
    try:
        message_id = send_email(
            to_email=TO_EMAIL,
            subject="[TEST] " + BLAST2_SUBJECT,
            html_content=html_content,
            text_content=text_content,
        )
    except EmailSendError as e:
        print(f"FAILED to send: {e}")
        return
    print(f"Sent to {TO_EMAIL} -- Brevo message id: {message_id}")


if __name__ == "__main__":
    main()
