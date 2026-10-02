"""
Email copy for the "Excitement is Building" outreach -- the initial blast to
past Discovery volunteers/TAs, plus its automated follow-ups. Kept separate
from app.py so the wording can be revised without touching route logic, and
reused identically by the one-off test send and the eventual full blast.
"""

EXCITEMENT_BLAST_SUBJECT = "Dallas Discovery Excitement is Growing"

DISCOVERY_URL = "https://www.blueskyusa.net/discovery"


def excitement_blast_content(first_name):
    """Returns (html_content, text_content) for the initial blast email,
    personalized with the contact's first name."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    html_content = f"""
        <p style="font-size:1.6em; font-weight:bold; margin-bottom:20px;">Dallas Discovery Excitement is Growing!!!</p>
        <p>Dear {greeting},</p>
        <p>Please forgive the intrusion, but I thought you might want to know about the
        Discovery life training seminars discussed below. If not, please disregard this email.</p>
        <p>In 2020 when the Dallas Discovery program closed (due to Covid), many of us
        who gave their time and heart to the training expressed a desire to re-open the
        program. We are in the very early stages of putting all this together, and nothing
        is official yet, but we are working hard to put the pieces together to make this
        happen.</p>
        <p>Our hope is that in the next month or so we will be meeting with all interested
        volunteers. If you were a Dallas Discovery Facilitator, TA, Trainee, Admin Assistant,
        Sound Tech, Room/Equipment Manager, Saturday contract volunteer, or you are simply
        interested to know more about the training, we would love to hear from you. Please
        open the link below and complete the brief contact form so that you will be among
        the first to know when a meeting date is set, or additional information is
        available.</p>
        <p><a href="{DISCOVERY_URL}">{DISCOVERY_URL}</a></p>
        <p>Thanks so much. Indeed, the excitement is building!!!</p>
        <p>Your support and giving hearts are much appreciated.</p>
        <p>We look forward to seeing you soon.</p>
        <p>Blessings on you,<br>Kent Hafemann</p>
        <p style="color:#666; font-size:0.9em;">PS...Please text or email this link to anyone
        who you think would be interested and invite them to sign up.<br>
        <a href="{DISCOVERY_URL}">{DISCOVERY_URL}</a></p>
    """

    text_content = f"""DALLAS DISCOVERY EXCITEMENT IS GROWING!!!

Dear {greeting},

Please forgive the intrusion, but I thought you might want to know about the Discovery life training seminars discussed below. If not, please disregard this email.

In 2020 when the Dallas Discovery program closed (due to Covid), many of us who gave their time and heart to the training expressed a desire to re-open the program. We are in the very early stages of putting all this together, and nothing is official yet, but we are working hard to put the pieces together to make this happen.

Our hope is that in the next month or so we will be meeting with all interested volunteers. If you were a Dallas Discovery Facilitator, TA, Trainee, Admin Assistant, Sound Tech, Room/Equipment Manager, Saturday contract volunteer, or you are simply interested to know more about the training, we would love to hear from you. Please open the link below and complete the brief contact form so that you will be among the first to know when a meeting date is set, or additional information is available.

{DISCOVERY_URL}

Thanks so much. Indeed, the excitement is building!!!

Your support and giving hearts are much appreciated.

We look forward to seeing you soon.

Blessings on you,
Kent Hafemann

PS...Please text or email this link to anyone who you think would be interested and invite them to sign up.
{DISCOVERY_URL}
"""

    return html_content, text_content


LONGFORM_FOLLOWUP_SUBJECT = "Thank you! One more quick step for our volunteer teams"


def longform_followup_content(first_name, longform_url):
    """Returns (html_content, text_content) for the short thank-you email
    that carries someone's personal long-form link -- sent right after they
    check "Volunteer" on the discovery short form, and reusable any time
    that link needs to be sent or re-sent by hand."""
    html_content = f"""
        <p>Hi {first_name},</p>
        <p>Thank you so much for adding your name &mdash; it means a lot to know you're
        interested in helping bring this training back to life.</p>
        <p>As promised, here's a short follow-up form with a bit more information that
        will help us organize our volunteer teams:</p>
        <p><a href="{longform_url}">{longform_url}</a></p>
        <p>No rush at all &mdash; fill it out whenever you get a chance.</p>
        <p>Thanks again for your support and your giving heart.</p>
        <p>Blessings,<br>Kent Hafemann</p>
    """
    text_content = (
        f"Hi {first_name},\n\n"
        "Thank you so much for adding your name -- it means a lot to know you're "
        "interested in helping bring this training back to life.\n\n"
        "As promised, here's a short follow-up form with a bit more information that "
        f"will help us organize our volunteer teams:\n{longform_url}\n\n"
        "No rush at all -- fill it out whenever you get a chance.\n\n"
        "Thanks again for your support and your giving heart.\n\nBlessings,\nKent Hafemann"
    )
    return html_content, text_content
