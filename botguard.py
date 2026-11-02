"""Simple, setup-free protection for public forms against bots.

 1. Timing check: each form carries a signed, hidden timestamp. A form sent in under 3 seconds, with a missing or tampered
    timestamp, or older than a day is refused (people never fill these out that fast; bots do).
 2. Rate limits: one computer (IP address) may send only a few forms per hour, and one email address may be used only a
    few times per day on a form. Hits are kept in the database so every server process shares them.
Works together with the hidden "website" trap field the forms already have."""
import hashlib
import hmac
import time

from flask import current_app, request
from markupsafe import Markup

MIN_SECONDS = 3
MAX_AGE = 24 * 3600
_ready = {"v": False}


def _sig(ts):
    key = (current_app.secret_key or "x")
    key = key if isinstance(key, bytes) else str(key).encode()
    return hmac.new(key, str(ts).encode(), hashlib.sha256).hexdigest()[:20]


def field_html():
    """Hidden input to place inside every protected form: {{ bot_field() }}"""
    ts = int(time.time())
    return Markup(f'<input type="hidden" name="bg" value="{ts}.{_sig(ts)}">')


def client_ip():
    return (request.headers.get("X-Forwarded-For", "") or request.remote_addr or "").split(",")[0].strip()


def _timing_ok(form, now):
    raw = (form.get("bg") or "")
    try:
        ts_s, sig = raw.split(".", 1)
        ts = int(ts_s)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sig(ts)):
        return False
    age = now - ts
    return MIN_SECONDS <= age <= MAX_AGE


def _hits(get_db, key, form_name, window, now, record=True):
    conn = get_db()
    try:
        if not _ready["v"]:
            conn.execute("CREATE TABLE IF NOT EXISTS bot_hits (id SERIAL PRIMARY KEY, k TEXT NOT NULL, form TEXT NOT NULL, at INTEGER NOT NULL)")
            conn.commit()
            _ready["v"] = True
        if record:
            conn.execute("INSERT INTO bot_hits (k, form, at) VALUES (?, ?, ?)", (key, form_name, now))
            conn.execute("DELETE FROM bot_hits WHERE at < ?", (now - 2 * 86400,))
            conn.commit()
        row = conn.execute("SELECT COUNT(*) AS n FROM bot_hits WHERE k = ? AND form = ? AND at >= ?", (key, form_name, now - window)).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def check(get_db, form_name, form, email=None, ip_limit=6, ip_window=3600, email_limit=3, email_window=86400):
    """Returns (ok, message). message is None when the form should be quietly ignored (hidden trap filled).
    Never blocks real use if the database check itself fails."""
    if form.get("website"):
        return False, None
    now = int(time.time())
    if not _timing_ok(form, now):
        return False, "That took a moment too long or went too fast. Please reload the page and try again."
    try:
        if _hits(get_db, "ip:" + client_ip(), form_name, ip_window, now) > ip_limit:
            return False, "We have received several forms from your connection. Please try again in a little while."
        if email and _hits(get_db, "em:" + email.strip().lower(), form_name, email_window, now) > email_limit:
            return False, "That email address has already been used several times today. Please try again tomorrow, or reply to the email we sent."
    except Exception as e:
        print(f"Bot check skipped: {e}", flush=True)
    return True, None
