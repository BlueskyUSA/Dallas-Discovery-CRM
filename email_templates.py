"""
Email copy for the "Excitement is Building" outreach -- the initial blast to
past Discovery volunteers/TAs, plus its automated follow-ups. Kept separate
from app.py so the wording can be revised without touching route logic, and
reused identically by the one-off test send and the eventual full blast.
"""

EXCITEMENT_BLAST_SUBJECT = "Dallas Discovery Excitement is Growing"

DISCOVERY_URL = "https://dallas-discovery-crm.onrender.com/discovery"


def excitement_blast_content(first_name, connect_url=None):
    """Returns (html_content, text_content) for the initial blast email,
    personalized with the contact's first name. connect_url, if given, is
    that contact's personal link to the "reach out to me" page -- lets
    someone ask Kent to personally follow up (by email or phone) without
    emailing him directly, so replies don't just pile up in his inbox."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    connect_html = ""
    connect_text = ""
    if connect_url:
        connect_html = f"""
        <p>Want me to personally follow up with you? <a href="{connect_url}">Click here</a> and
        let me know the best way to reach you &mdash; email or a call &mdash; and I'll get to you
        as soon as I possibly can. So many of you have already reached out, and every single one
        of you matters to me, even though I'm just one guy trying to keep up!</p>
        """
        connect_text = (
            f"Want me to personally follow up with you? Click here and let me know the best way "
            f"to reach you -- email or a call -- and I'll get to you as soon as I possibly can. So "
            f"many of you have already reached out, and every single one of you matters to me, "
            f"even though I'm just one guy trying to keep up!\n{connect_url}\n\n"
        )

    html_content = f"""
        <p style="font-size:1.6em; font-weight:bold; margin-bottom:20px;">Dallas Discovery Excitement is Growing!!!</p>
        <p>Dear {greeting},</p>
        <p>Please forgive the intrusion &mdash; you're receiving this email because you're someone
        I've known or worked with over the years, whether through business, family, friend, or
        just life.</p>
        <p>Some of you are familiar with Dallas Discovery, an amazing and powerful
        personal-growth and relationship training program that Pamela and I have been a part of
        since 1996. Unfortunately, the program closed in 2020 due to Covid. But we are in the
        very early stages of re-opening the program. Nothing is official yet, but we are working
        hard to put the pieces together to make this happen. Whether or not Discovery has ever
        been part of your story, I'd love to stay connected with you.</p>
        <p>Our hope is that in the next month or so we will be meeting with many who attended or
        volunteered to serve the program. If you were a Dallas Discovery Facilitator, TA,
        Trainee, Admin Assistant, Sound Tech, Room/Equipment Manager, Saturday-only contract
        volunteer, or if you are simply interested to know more about the training, we would
        love to hear from you.
        <span style="font-size:1.25em; font-weight:bold; color:#1f6fb2;">Please open the link
        below and complete the brief contact form so that you will be among the first to know
        when a meeting date is set, or additional information is available.</span></p>
        <p><a href="{DISCOVERY_URL}">{DISCOVERY_URL}</a></p>
        {connect_html}
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

Please forgive the intrusion -- you're receiving this email because you're someone I've known or worked with over the years, whether through business, family, friend, or just life.

Some of you are familiar with Dallas Discovery, an amazing and powerful personal-growth and relationship training program that Pamela and I have been a part of since 1996. Unfortunately, the program closed in 2020 due to Covid. But we are in the very early stages of re-opening the program. Nothing is official yet, but we are working hard to put the pieces together to make this happen. Whether or not Discovery has ever been part of your story, I'd love to stay connected with you.

Our hope is that in the next month or so we will be meeting with many who attended or volunteered to serve the program. If you were a Dallas Discovery Facilitator, TA, Trainee, Admin Assistant, Sound Tech, Room/Equipment Manager, Saturday-only contract volunteer, or if you are simply interested to know more about the training, we would love to hear from you. Please open the link below and complete the brief contact form so that you will be among the first to know when a meeting date is set, or additional information is available.

{DISCOVERY_URL}

{connect_text}Thanks so much. Indeed, the excitement is building!!!

Your support and giving hearts are much appreciated.

We look forward to seeing you soon.

Blessings on you,
Kent Hafemann

PS...Please text or email this link to anyone who you think would be interested and invite them to sign up.
{DISCOVERY_URL}
"""

    return html_content, text_content


CONNECT_REQUEST_SUBJECT = "I got your message -- thank you"


def connect_request_confirmation_content(first_name, method):
    """Returns (html_content, text_content) for the auto-reply sent right
    after someone submits the "please reach out to me personally" request
    on the /connect/<token> page -- a personal note from Kent explaining
    that many people have reached out, that they matter to him, and that
    he'll get to them as soon as he can (he's one person, not a team)."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"
    how = "give you a call" if method == "Phone" else "email you personally"

    html_content = f"""
        <p>Dear {greeting},</p>
        <p>Thank you so much for letting me know you'd like me to {how} &mdash; I got your
        message, and I wanted to write back right away so you know it didn't just disappear into
        the void.</p>
        <p>So many of you have reached out already, and I want you to know that every single one
        of you matters to me. I'm doing my best to personally get back to each person, but I'm
        just one guy right now, so it may take me a little time. Please bear with me &mdash; I
        promise I will get to you as soon as I possibly can.</p>
        <p>Thank you again for your patience, and for being part of this with me.</p>
        <p>Blessings on you,<br>Kent Hafemann</p>
    """

    text_content = (
        f"Dear {greeting},\n\n"
        f"Thank you so much for letting me know you'd like me to {how} -- I got your message, "
        "and I wanted to write back right away so you know it didn't just disappear into the void.\n\n"
        "So many of you have reached out already, and I want you to know that every single one of "
        "you matters to me. I'm doing my best to personally get back to each person, but I'm just "
        "one guy right now, so it may take me a little time. Please bear with me -- I promise I "
        "will get to you as soon as I possibly can.\n\n"
        "Thank you again for your patience, and for being part of this with me.\n\n"
        "Blessings on you,\nKent Hafemann"
    )

    return html_content, text_content


INTERESTED_PARTY_WELCOME_SUBJECT = "Thank you for your interest in Dallas Discovery"


def interested_party_welcome_content(first_name):
    """Returns (html_content, text_content) for the welcome email sent when
    someone selects "Interested Party" (rather than a past volunteer role)
    on the /discovery short form -- thanks them for their interest and
    shares a bit of the personal story and heart behind the program."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    html_content = f"""
        <p>Dear {greeting},</p>
        <p>Thank you so much for letting us know you're interested in Dallas Discovery &mdash;
        it truly means a lot to us.</p>
        <p>Pamela and I have been part of this personal-growth and relationship training program
        since 1996, and it's hard to put into words just how much it has shaped our lives and our
        marriage over the years. Discovery has a way of helping people find more freedom, more
        honesty, and more connection &mdash; with themselves and with the people they love.</p>
        <p>The program closed in 2020 because of Covid, but we are now in the early stages of
        working to bring it back. Nothing is official yet, and there are still a lot of pieces to
        put together, but knowing there are people like you who are curious and open to it gives
        us so much encouragement.</p>
        <p>We'll keep you in the loop as things develop, including when there's a meeting date or
        more information to share. In the meantime, if you ever want to hear more about the
        program or our own story with Discovery, just reply to this email &mdash; we'd love to
        talk with you.</p>
        <p>Thank you again for your interest and for taking the time to reach out.</p>
        <p>Blessings on you,<br>Kent Hafemann</p>
    """

    text_content = f"""Dear {greeting},

Thank you so much for letting us know you're interested in Dallas Discovery -- it truly means a lot to us.

Pamela and I have been part of this personal-growth and relationship training program since 1996, and it's hard to put into words just how much it has shaped our lives and our marriage over the years. Discovery has a way of helping people find more freedom, more honesty, and more connection -- with themselves and with the people they love.

The program closed in 2020 because of Covid, but we are now in the early stages of working to bring it back. Nothing is official yet, and there are still a lot of pieces to put together, but knowing there are people like you who are curious and open to it gives us so much encouragement.

We'll keep you in the loop as things develop, including when there's a meeting date or more information to share. In the meantime, if you ever want to hear more about the program or our own story with Discovery, just reply to this email -- we'd love to talk with you.

Thank you again for your interest and for taking the time to reach out.

Blessings on you,
Kent Hafemann
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
