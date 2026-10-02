"""
Outbound email, sent through Brevo's transactional email API.

Why Brevo: free tier (300 emails/day, no expiration, no credit card) is
enough for both the "Excitement is Building" blast and the automated
follow-up emails triggered by the short/long form flow. See BREVO_API_KEY
in Render's Environment settings -- never hardcoded here.

This module is deliberately just a thin wrapper around Brevo's API. It
doesn't know anything about contacts, templates, or the send queue --
those live in app.py and the (future) blast-queue logic, and call
send_email() one message at a time.
"""
import os
import requests

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

# The address/name every CRM email is sent from. Brevo requires this
# address to be a "verified sender" (either the whole blueskyusa.net domain,
# or this specific address) before it will actually deliver -- see the
# Senders & IP section of the Brevo dashboard.
DEFAULT_FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "kent@blueskyusa.net")
DEFAULT_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Kent Hafemann")


class EmailSendError(Exception):
    """Raised when Brevo rejects or fails to send a message."""


def send_email(to_email, subject, html_content, to_name=None, text_content=None,
                from_email=None, from_name=None, reply_to=None):
    """Sends one email through Brevo. Returns Brevo's message id on success.
    Raises EmailSendError on any failure (missing API key, bad request,
    Brevo-side error, network problem) -- callers decide how to handle
    that (e.g. leave a contact's "email sent" timestamp unset so a daily
    queue retries them later)."""
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        raise EmailSendError("BREVO_API_KEY is not set in the environment.")

    payload = {
        "sender": {"email": from_email or DEFAULT_FROM_EMAIL, "name": from_name or DEFAULT_FROM_NAME},
        "to": [{"email": to_email, **({"name": to_name} if to_name else {})}],
        "subject": subject,
        "htmlContent": html_content,
    }
    if text_content:
        payload["textContent"] = text_content
    if reply_to:
        payload["replyTo"] = {"email": reply_to}

    try:
        resp = requests.post(
            BREVO_API_URL,
            json=payload,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        raise EmailSendError(f"Network error contacting Brevo: {e}") from e

    if resp.status_code not in (200, 201):
        raise EmailSendError(f"Brevo rejected the send ({resp.status_code}): {resp.text}")

    return resp.json().get("messageId")
