"""
Email copy for the "Excitement is Building" outreach -- the initial blast to
past Discovery volunteers/TAs, plus its automated follow-ups. Kept separate
from app.py so the wording can be revised without touching route logic, and
reused identically by the one-off test send and the eventual full blast.
"""

from config import PROGRAM_NAME, DISCOVERY_URL

EXCITEMENT_BLAST_SUBJECT = f"{PROGRAM_NAME} Excitement is Growing"


def _connect_blocks(connect_url):
    """The optional personal "reach out to me" paragraph used by the
    blast emails. Returns (html, text); both empty when there is no link."""
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
    return connect_html, connect_text


def excitement_blast_content(first_name, connect_url=None):
    """Returns (html_content, text_content) for the initial blast email,
    personalized with the contact's first name. connect_url, if given, is
    that contact's personal link to the "reach out to me" page -- lets
    someone ask Kent to personally follow up (by email or phone) without
    emailing him directly, so replies don't just pile up in his inbox."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    connect_html, connect_text = _connect_blocks(connect_url)

    html_content = f"""
        <p style="font-size:1.6em; font-weight:bold; margin-bottom:20px;">{PROGRAM_NAME} Excitement is Growing!!!</p>
        <p>Dear {greeting},</p>
        <p>Please forgive the intrusion &mdash; this is Kent Hafemann, and you're receiving this
        email because you're someone I've known or worked with over the years, whether through
        business, family, friend, or just life.</p>
        <p>Some of you are aware of Dallas Discovery, a powerful and life-changing
        personal-growth and relationship training that Pamela and I have been involved with
        since 1996. Unfortunately, the program closed in 2020 because of COVID-19. Yet many have
        expressed a strong desire to reopen the program. Nothing is official yet, but we're
        working hard to make that happen.</p>
        <p>Our hope is in the next few months we will be meeting with those who attended or
        volunteered in the training. If you were a Discovery volunteer, or if you are interested
        to know more about this program, we would love to hear from you.</p>
        <p><span style="font-size:1.25em; font-weight:bold; color:#1f6fb2;">Please open the link
        below and complete a brief contact form so that you will be among the first to know when
        a meeting date is set, or additional information is available.</span></p>
        <p><a href="{DISCOVERY_URL}">{DISCOVERY_URL}</a></p>
        {connect_html}
        <p>Please share this link and help us get the word out.</p>
        <p>Thanks so much. Indeed, the excitement is building!!!</p>
        <p>Your support and giving hearts are much appreciated.</p>
        <p>We look forward to seeing you soon.</p>
        <p>Blessings on you,<br>Kent Hafemann</p>
    """

    text_content = f"""DALLAS DISCOVERY EXCITEMENT IS GROWING!!!

Dear {greeting},

Please forgive the intrusion -- this is Kent Hafemann, and you're receiving this email because you're someone I've known or worked with over the years, whether through business, family, friend, or just life.

Some of you are aware of Dallas Discovery, a powerful and life-changing personal-growth and relationship training that Pamela and I have been involved with since 1996. Unfortunately, the program closed in 2020 because of COVID-19. Yet many have expressed a strong desire to reopen the program. Nothing is official yet, but we're working hard to make that happen.

Our hope is in the next few months we will be meeting with those who attended or volunteered in the training. If you were a Discovery volunteer, or if you are interested to know more about this program, we would love to hear from you.

Please open the link below and complete a brief contact form so that you will be among the first to know when a meeting date is set, or additional information is available.

{DISCOVERY_URL}

{connect_text}Please share this link and help us get the word out.

Thanks so much. Indeed, the excitement is building!!!

Your support and giving hearts are much appreciated.

We look forward to seeing you soon.

Blessings on you,
Kent Hafemann
"""

    return html_content, text_content


BLAST2_SUBJECT = f"{PROGRAM_NAME} Excitement is Growing"


def blast2_cover_content(first_name):
    """Returns (html_content, text_content) for Email Blast #2 -- the cover
    letter for the Dallas Discovery contact database (people who already know
    the program, so no introduction of who Kent is). Deliberately has no
    personal "reach out to me" paragraph. NOT sent automatically by anything."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    html_content = f"""
        <p>Dear {greeting},</p>
        <p>As you know, the Dallas Discovery training closed in 2020 because of COVID-19. Yet
        many have expressed a strong desire to reopen the program. Nothing is official yet, but
        we're working hard to make that happen.</p>
        <p>Our hope is in the next few months we will be meeting with those who attended or
        volunteered in the training. If you were a Discovery volunteer, or if you are interested
        to know more about this program, we would love to hear from you.</p>
        <p><span style="font-size:1.25em; font-weight:bold; color:#1f6fb2;">Please open the link
        below and complete a brief contact form so that you will be among the first to know when
        a meeting date is set, or additional information is available.</span></p>
        <p><a href="{DISCOVERY_URL}">{DISCOVERY_URL}</a></p>
        <p>Please share this link and help us get the word out.</p>
        <p>Thanks so much. Indeed, the excitement is building!!!</p>
        <p>Your support and giving hearts are much appreciated.</p>
        <p>We look forward to seeing you soon.</p>
        <p>Blessings on you,<br>Kent Hafemann</p>
    """

    text_content = f"""Dear {greeting},

As you know, the Dallas Discovery training closed in 2020 because of COVID-19. Yet many have expressed a strong desire to reopen the program. Nothing is official yet, but we're working hard to make that happen.

Our hope is in the next few months we will be meeting with those who attended or volunteered in the training. If you were a Discovery volunteer, or if you are interested to know more about this program, we would love to hear from you.

Please open the link below and complete a brief contact form so that you will be among the first to know when a meeting date is set, or additional information is available.

{DISCOVERY_URL}

Please share this link and help us get the word out.

Thanks so much. Indeed, the excitement is building!!!

Your support and giving hearts are much appreciated.

We look forward to seeing you soon.

Blessings on you,
Kent Hafemann
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


INTERESTED_PARTY_WELCOME_SUBJECT = f"Thank you for your interest in {PROGRAM_NAME}"


def interested_party_welcome_content(first_name, connect_url=None):
    """Returns (html_content, text_content) for the welcome email sent when
    someone selects "Interested Party" (rather than a past volunteer role)
    on the /discovery short form -- thanks them for their interest and
    shares Kent and Pamela's own story with Discovery. connect_url, if
    given, is that contact's personal link to the "reach out to me" page,
    so they can ask Kent to personally follow up without just replying to
    this email."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    connect_html = ""
    connect_text = ""
    if connect_url:
        connect_html = f' Feel free to <a href="{connect_url}">click here</a> if you\'d like me to reach out to you personally.'
        connect_text = f" Feel free to click the link below if you'd like me to reach out to you personally.\n{connect_url}"

    html_content = f"""
        <p>Dear {greeting},</p>
        <p>Thank you so much for your interest in {PROGRAM_NAME}. Let me tell you a bit about
        our journey and the profound impact Discovery has had on our lives.</p>
        <p>In 1996, my wife Pamela and I moved to Texas, and soon after, a close friend invited us
        to attend Pathways (now Discovery) &mdash; our marriage was struggling. With three
        children and eighteen years of marriage behind us, we were lost and confused. We tried
        counselors, church advisors, mentors, healing prayer, anything we could think of. But we
        simply couldn't figure it out &mdash; it was like trying to hold water in our hands, our
        life falling apart, leaking right through our fingers.</p>
        <p>Pamela went through D1 training first. I'll never forget the phone call that changed
        everything &mdash; I was at work, wondering what was happening with her in this mysterious
        training our friend had invited us to. That one phone call was a breakthrough that changed
        the course of our marriage and put us on a healing path we're still thankful for today.</p>
        <p>Of course, wanting our marriage to work meant actually using the tools that Discovery
        offered. We learned how our pasts had shaped our differences, and how to honor and accept
        our limitations and broken stories. We learned how to listen and walk through conflict
        without breaking the relationship. We learned about the power of forgiveness and the
        importance of standing up for yourself honestly in a relationship &mdash; and so much
        more.</p>
        <p>Since then, Pamela and I have been involved in many trainings and programs helping
        people identify and overcome what stands in the way of joy and peace. But there is nothing
        out there that compares to the effectiveness of Discovery. The core trainings include D1,
        D2, D3, and Relationship Training. D1, D2, and D3 are monthly, consecutive trainings that
        build on one another &mdash; advancing from one to the next requires having completed the
        one before. The Relationship Training, on the other hand, welcomes all committed couples
        with no prior Discovery involvement required. We find that most couples who complete it go
        on to enlist individually in D1 through D3.</p>
        <p>We welcome your interest, and we hope you'll take the risk of stepping into this
        life-changing training with us.{connect_html}</p>
        <p>Thank you, and God bless you,<br>Kent Hafemann</p>
    """

    text_content = f"""Dear {greeting},

Thank you so much for your interest in {PROGRAM_NAME}. Let me tell you a bit about our journey and the profound impact Discovery has had on our lives.

In 1996, my wife Pamela and I moved to Texas, and soon after, a close friend invited us to attend Pathways (now Discovery) -- our marriage was struggling. With three children and eighteen years of marriage behind us, we were lost and confused. We tried counselors, church advisors, mentors, healing prayer, anything we could think of. But we simply couldn't figure it out -- it was like trying to hold water in our hands, our life falling apart, leaking right through our fingers.

Pamela went through D1 training first. I'll never forget the phone call that changed everything -- I was at work, wondering what was happening with her in this mysterious training our friend had invited us to. That one phone call was a breakthrough that changed the course of our marriage and put us on a healing path we're still thankful for today.

Of course, wanting our marriage to work meant actually using the tools that Discovery offered. We learned how our pasts had shaped our differences, and how to honor and accept our limitations and broken stories. We learned how to listen and walk through conflict without breaking the relationship. We learned about the power of forgiveness and the importance of standing up for yourself honestly in a relationship -- and so much more.

Since then, Pamela and I have been involved in many trainings and programs helping people identify and overcome what stands in the way of joy and peace. But there is nothing out there that compares to the effectiveness of Discovery. The core trainings include D1, D2, D3, and Relationship Training. D1, D2, and D3 are monthly, consecutive trainings that build on one another -- advancing from one to the next requires having completed the one before. The Relationship Training, on the other hand, welcomes all committed couples with no prior Discovery involvement required. We find that most couples who complete it go on to enlist individually in D1 through D3.

We welcome your interest, and we hope you'll take the risk of stepping into this life-changing training with us.{connect_text}

Thank you, and God bless you,
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


LONGFORM_THANKYOU_SUBJECT = "Thank you!"


def longform_thankyou_content(first_name, connect_url, plan_url):
    """Returns (html_content, text_content) for the thank-you email sent
    when someone clicks Submit on the public long form (complete-profile)
    -- not every autosave, just that final submit. Offers two separate
    opt-ins: plan_url is a button that sends them the (much longer)
    Preliminary Discovery Plan email (see preliminary_plan_content below)
    -- it's only sent if they click through, not automatically. connect_url
    is their personal /connect link so they can ask Kent to follow up
    directly if they'd like, without needing to reply to this email."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    html_content = f"""
        <p>Dear {greeting},</p>
        <p>Thank you for taking the time to complete the &ldquo;Additional Information&rdquo;
        form &mdash; we appreciate your effort and your energy.</p>
        <p>What you shared helps us as we work to bring Discovery back to life, and it tells me
        you still care about what this program stands for.</p>
        <p>I've put together a preliminary outline of where things stand, including our mission
        and goals, the tasks involved with getting this training running again, and where there
        might be a place for you.</p>
        <p>In short&hellip; we need an army of serious volunteers willing to dig deep and take on
        responsibility. If you wish, we'll send you the plan outline.</p>
        <p><a href="{plan_url}" style="display:inline-block; background:#1f6fb2; color:#ffffff;
        text-decoration:none; font-weight:700; padding:12px 22px; border-radius:8px;">Send me the
        Preliminary Discovery Plan</a></p>
        <p>Thanks again for all that you have done in years past, and for perhaps all that you
        will be doing for Discovery in the future.</p>
        <p>If you'd like for me to personally reach out to you &mdash; just
        <a href="{connect_url}">click here</a> and I'll get to you as soon as I can.</p>
        <p>Thank you again for your time, your heart, and your support.</p>
        <p>Blessings on you,<br>Kent Hafemann</p>
    """

    text_content = (
        f"Dear {greeting},\n\n"
        "Thank you for taking the time to complete the \"Additional Information\" form -- we "
        "appreciate your effort and your energy.\n\n"
        "What you shared helps us as we work to bring Discovery back to life, and it tells me "
        "you still care about what this program stands for.\n\n"
        "I've put together a preliminary outline of where things stand, including our mission "
        "and goals, the tasks involved with getting this training running again, and where "
        "there might be a place for you.\n\n"
        "In short... we need an army of serious volunteers willing to dig deep and take on "
        "responsibility. If you wish, we'll send you the plan outline:\n"
        f"{plan_url}\n\n"
        "Thanks again for all that you have done in years past, and for perhaps all that you "
        "will be doing for Discovery in the future.\n\n"
        "If you'd like for me to personally reach out to you -- just click the link below and "
        f"I'll get to you as soon as I can.\n{connect_url}\n\n"
        "Thank you again for your time, your heart, and your support.\n\n"
        "Blessings on you,\nKent Hafemann"
    )
    return html_content, text_content


PRELIMINARY_PLAN_SUBJECT = "Preliminary business plan for Discovery"


def preliminary_plan_content(first_name):
    """Returns (html_content, text_content) for the Preliminary Discovery
    Plan email -- sent only when staff click "Send business plan" on a contact,
    or someone clicks the "Send me the Preliminary Discovery Plan" button in the
    long-form thank-you email, never automatically. Lays out Kent's mission and
    goals for relaunching Discovery and the volunteer help still needed."""
    greeting = first_name.strip() if first_name and first_name.strip() else "Friend"

    html_content = f"""
        <p>Dear {greeting},</p>
        <p>Thanks for your continued interest in the {PROGRAM_NAME} launch.</p>
        <p>So, what are the goals and mission for {PROGRAM_NAME}? Perhaps you may want to weigh in
        on this question&hellip; but here is a start:</p>
        <ol>
            <li>To reflect the Love of God by creating an effective, powerful and emotionally
            safe environment for trainees to evaluate and overcome the broken parts of their
            lives so that freedom, joy, peace, and love become the cornerstones of their
            lives.</li>
            <li>Create an enduring ministry that outlasts any one key leader by:
                <ol type="a">
                    <li>Operating {PROGRAM_NAME} so that revenue exceeds expenses;</li>
                    <li>When the time is right, transfer Discovery intellectual property,
                    licenses, software, and equipment to the next operating team at no cost.</li>
                </ol>
            </li>
        </ol>
        <p>We all believe that Discovery is a much-needed ministry in our broken world. Do you
        remember the cleansing of your heart, the amazing and powerful freedom, love, and
        forgiveness we each experienced in this training? Our lives were changed forever!!! I
        thank God for Discovery, and I know you do too, otherwise you would not be reading this.
        So, let&rsquo;s think big, and do something for Discovery that has never been done
        before&hellip; GIVE IT AWAY&hellip; so that for generations, by God&rsquo;s grace,
        millions of people will experience the love of God and love for themselves&hellip; and
        with this new-found power and freedom, LOVE OTHERS!!!! This is the greatest
        commandment.</p>
        <p>This is a monumental vision. To give Discovery away, as volunteers, it needs your
        prayers and your support. We need to find folks who believe in our mission and can give
        financially to the non-profit. If that might be you, or someone you know, please reach
        out to me to discuss the financial needs of the program. The program cannot open without
        financial support.</p>
        <p><strong>How will we accomplish this Mission?</strong></p>
        <ol type="a">
            <li>First, we will begin hosting Discovery social gatherings to build vision and
            excitement, and renew old friendships (volunteers will be needed to organize and pull
            these events together).</li>
            <li>Hold fund-raisers to capture the financial support needed to open the program.
            All donations will go to the Discovery 501(c)(3). Our goal is to secure sufficient
            funding to pay for the acquisition cost of the program, plus we have been advised
            that to responsibly host {PROGRAM_NAME} we need to raise $100,000 in cash-flow
            reserves to ensure the program survives.</li>
            <li>Begin the selection process for leadership teams. Previous leaders will have
            priority due to their experience, but the minimum expectation of all leadership is to
            have enrolled at least one trainee in D1, or one couple in the Relationship training.
            We need Facilitators for D2, D3, and Refocus. Initially, Max Thompson, Pamela
            Hafemann and I will be the lead facilitators in D1 and the Relationship training. But
            it is our desire to share this role with others who are qualified in the very near
            future. The Board of Directors will make final leadership selections.</li>
            <li>Begin a marketing campaign with the goal of hosting a Relationship training within
            the next four to six months, and to kick off D1 soon thereafter. Our goal is to have
            forty couples in the training room for the first Relationship training. We believe
            the need for relationship coaching is so significant that with the right marketing
            campaign, we can easily fill the seats and use the energy from that training to kick
            off D1.</li>
        </ol>
        <p><strong>VOLUNTEERS NEEDED AS FOLLOWS:</strong></p>
        <ol type="a">
            <li>We need video and photography experts willing to work for free to help us put
            together an effective marketing campaign. Videography will include testimonials of
            enthusiastic trainees whose lives have been changed by the training. Relationship
            training videos and photography will be needed first.</li>
            <li>We need admin volunteers for the following:
                <ol>
                    <li>Skilled coaches to help trainees with registration and payment
                    confirmation;</li>
                    <li>Two volunteers to man the event sign-in table;</li>
                    <li>Greet and mingle with trainees at the clap-in;</li>
                    <li>Qualified security personnel to oversee the safety of the program.</li>
                </ol>
            </li>
            <li>We need someone to evaluate existing audio equipment and/or acquire additional
            equipment if needed.</li>
            <li>We need a place to store audio/video equipment and training supplies, and a means
            to transport this equipment if we cannot keep it at the hotel.</li>
            <li>We need someone to gather, organize, maintain and assemble the supplies needed
            for each training.</li>
        </ol>
        <p>Please let me know what role you may wish to play to open Discovery. Feel free to
        reach out to me with your thoughts at
        <a href="mailto:Kent@blueskyusa.net">Kent@blueskyusa.net</a>.</p>
        <p>Thank you again for your energy, your time, your heart, and your support.</p>
        <p>Blessings on you. I look forward to meeting you at our first Discovery get-together,
        if not sooner.</p>
        <p>Take care,<br>Kent Hafemann</p>
    """

    text_content = f"""Dear {greeting},

Thanks for your continued interest in the {PROGRAM_NAME} launch.

So, what are the goals and mission for {PROGRAM_NAME}? Perhaps you may want to weigh in on this question... but here is a start:

1. To reflect the Love of God by creating an effective, powerful and emotionally safe environment for trainees to evaluate and overcome the broken parts of their lives so that freedom, joy, peace, and love become the cornerstones of their lives.
2. Create an enduring ministry that outlasts any one key leader by:
   a. Operating {PROGRAM_NAME} so that revenue exceeds expenses;
   b. When the time is right, transfer Discovery intellectual property, licenses, software, and equipment to the next operating team at no cost.

We all believe that Discovery is a much-needed ministry in our broken world. Do you remember the cleansing of your heart, the amazing and powerful freedom, love, and forgiveness we each experienced in this training? Our lives were changed forever!!! I thank God for Discovery, and I know you do too, otherwise you would not be reading this. So, let's think big, and do something for Discovery that has never been done before... GIVE IT AWAY... so that for generations, by God's grace, millions of people will experience the love of God and love for themselves... and with this new-found power and freedom, LOVE OTHERS!!!! This is the greatest commandment.

This is a monumental vision. To give Discovery away, as volunteers, it needs your prayers and your support. We need to find folks who believe in our mission and can give financially to the non-profit. If that might be you, or someone you know, please reach out to me to discuss the financial needs of the program. The program cannot open without financial support.

HOW WILL WE ACCOMPLISH THIS MISSION?
a. First, we will begin hosting Discovery social gatherings to build vision and excitement, and renew old friendships (volunteers will be needed to organize and pull these events together).
b. Hold fund-raisers to capture the financial support needed to open the program. All donations will go to the Discovery 501(c)(3). Our goal is to secure sufficient funding to pay for the acquisition cost of the program, plus we have been advised that to responsibly host {PROGRAM_NAME} we need to raise $100,000 in cash-flow reserves to ensure the program survives.
c. Begin the selection process for leadership teams. Previous leaders will have priority due to their experience, but the minimum expectation of all leadership is to have enrolled at least one trainee in D1, or one couple in the Relationship training. We need Facilitators for D2, D3, and Refocus. Initially, Max Thompson, Pamela Hafemann and I will be the lead facilitators in D1 and the Relationship training. But it is our desire to share this role with others who are qualified in the very near future. The Board of Directors will make final leadership selections.
d. Begin a marketing campaign with the goal of hosting a Relationship training within the next four to six months, and to kick off D1 soon thereafter. Our goal is to have forty couples in the training room for the first Relationship training. We believe the need for relationship coaching is so significant that with the right marketing campaign, we can easily fill the seats and use the energy from that training to kick off D1.

VOLUNTEERS NEEDED AS FOLLOWS:
a. We need video and photography experts willing to work for free to help us put together an effective marketing campaign. Videography will include testimonials of enthusiastic trainees whose lives have been changed by the training. Relationship training videos and photography will be needed first.
b. We need admin volunteers for the following:
   1. Skilled coaches to help trainees with registration and payment confirmation;
   2. Two volunteers to man the event sign-in table;
   3. Greet and mingle with trainees at the clap-in;
   4. Qualified security personnel to oversee the safety of the program.
c. We need someone to evaluate existing audio equipment and/or acquire additional equipment if needed.
d. We need a place to store audio/video equipment and training supplies, and a means to transport this equipment if we cannot keep it at the hotel.
e. We need someone to gather, organize, maintain and assemble the supplies needed for each training.

Please let me know what role you may wish to play to open Discovery. Feel free to reach out to me with your thoughts at Kent@blueskyusa.net.

Thank you again for your energy, your time, your heart, and your support.

Blessings on you. I look forward to meeting you at our first Discovery get-together, if not sooner.

Take care,
Kent Hafemann
"""

    return html_content, text_content
