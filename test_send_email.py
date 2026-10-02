"""
One-time sanity check: sends a single test email through Brevo to confirm
BREVO_API_KEY is set correctly and the sending address is verified.

Run this from Render's Shell tab (never locally, since it needs
BREVO_API_KEY from the live environment):

    python3 test_send_email.py you@example.com

If you leave off the address, it sends to kent@blueskyusa.net by default.
"""
import sys
from email_utils import send_email, EmailSendError

to = sys.argv[1] if len(sys.argv) > 1 else "kent@blueskyusa.net"

try:
    message_id = send_email(
        to_email=to,
        subject="Bluesky CRM -- test email",
        html_content="<p>This is a test email from the Bluesky CRM, sent through Brevo.</p>"
                      "<p>If you're reading this, the email integration is wired up correctly.</p>",
        text_content="This is a test email from the Bluesky CRM, sent through Brevo. "
                     "If you're reading this, the email integration is wired up correctly.",
    )
    print(f"Sent successfully to {to}. Brevo message id: {message_id}")
except EmailSendError as e:
    print(f"FAILED to send: {e}")
    sys.exit(1)
