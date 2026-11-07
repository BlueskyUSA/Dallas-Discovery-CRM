"""Error alerts: when the Hub hits an unexpected error, the Owner gets an email (so Kent hears it from the Hub,
not from a guest). Visitors see a plain apology page. Nothing here changes any data."""
import os
import threading
import time
import traceback
from datetime import datetime

from flask import request, redirect, url_for, flash, make_response

THROTTLE_SECONDS = 30 * 60      # the same problem is emailed at most once per half hour
MAX_PER_HOUR = 10               # and never more than this many alert emails an hour
_seen = {}
_sent_times = []
_lock = threading.Lock()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Something went wrong</title></head><body style="font-family:Arial,sans-serif;max-width:560px;margin:12vh auto;padding:0 18px;color:#222">
<h1 style="font-size:1.5rem">Sorry, something went wrong on our end.</h1>
<p>We have been told about it and will fix it. Please try again in a little while.</p>
<p><a href="/">Back to the home page</a></p></body></html>"""


def _recipients(get_db):
    out = []
    env = (os.environ.get("ERROR_ALERT_EMAIL") or "").strip()
    if env:
        out += [e.strip() for e in env.split(",") if e.strip()]
    if not out:
        try:
            conn = get_db()
            try:
                out = [r["email"] for r in conn.execute("SELECT email FROM staff WHERE is_owner = 1 AND active = 1").fetchall()]
            finally:
                conn.close()
        except Exception:
            pass
    return out


def _where(exc):
    tb = traceback.extract_tb(exc.__traceback__) if exc is not None else []
    if not tb:
        return ""
    f = tb[-1]
    return f"{os.path.basename(f.filename)} line {f.lineno} in {f.name}"


def _should_send(key):
    now = time.time()
    with _lock:
        if now - _seen.get(key, 0) < THROTTLE_SECONDS:
            return False
        _sent_times[:] = [t for t in _sent_times if now - t < 3600]
        if len(_sent_times) >= MAX_PER_HOUR:
            return False
        _seen[key] = now
        _sent_times.append(now)
        return True


def send_alert(get_db, subject, lines, test=False):
    """Email the Owner. Returns how many addresses it was sent to. Never raises."""
    try:
        import email_utils
        to = _recipients(get_db)
        if not to:
            print("Error alert: no Owner email found to send to.", flush=True)
            return 0
        body = "".join(f"<p style='margin:4px 0'>{l}</p>" for l in lines)
        html = ("<div style='font-family:Arial,sans-serif;font-size:15px'>" + body +
                "<p style='color:#666;margin-top:14px'>This is an automatic alert from the Hub. "
                "No private details (names, passwords, form contents) are included.</p></div>")
        n = 0
        for addr in to:
            try:
                email_utils.send_email(addr, subject, html)
                n += 1
            except Exception as e:
                print(f"Error alert to {addr} failed: {e}", flush=True)
        return n
    except Exception as e:
        print(f"Error alert failed: {e}", flush=True)
        return 0


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def register(app, crm, owner_required, get_db):
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(Exception)
    def _unexpected(e):
        if isinstance(e, HTTPException) and (e.code or 500) < 500:
            return e                                  # ordinary "not found" and similar: not a problem
        code = getattr(e, "code", 500) or 500
        try:
            app.logger.exception("Unexpected error at %s", request.path)
            key = f"{type(e).__name__}|{request.path}|{_where(e)}"
            if _should_send(key):
                lines = [
                    "<b>The Hub hit an unexpected error.</b>",
                    f"When: {datetime.now().strftime('%a %b %d, %I:%M %p')} (server time)",
                    f"Page: {_esc(request.method)} {_esc(request.path)}",
                    f"Error: {_esc(type(e).__name__)}: {_esc(str(e)[:250])}",
                    f"Where in the code: {_esc(_where(e)) or 'unknown'}",
                    "If you see this repeatedly, forward this email to Claude.",
                ]
                subj = "Hub error: " + request.path[:60]
                threading.Thread(target=send_alert, args=(get_db, subj, lines), daemon=True).start()
        except Exception:
            pass
        resp = make_response(PAGE, code if code >= 500 else 500)
        return resp

    @crm.route("/error-alert-test", methods=["POST"])
    @owner_required
    def error_alert_test():
        n = send_alert(get_db, "Hub test alert",
                       ["<b>This is a test.</b> If you can read this, the Hub can warn you when something breaks.",
                        f"Sent: {datetime.now().strftime('%a %b %d, %I:%M %p')} (server time)"], test=True)
        if n:
            flash(f"Test alert emailed to {n} address{'es' if n != 1 else ''}. Check your inbox (and spam).")
        else:
            flash("The test alert could not be sent. Check that Brevo is connected and an active Owner email is on file.")
        return redirect(url_for(".owner_instructions") + "#alerts")
