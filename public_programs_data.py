"""Public wording for the four Dallas Discovery programs (front-end only).
The internal CRM codes stay as they are: D4 = The Art of Us, D1 = The Beginning,
D2 = Awakening, D3 = Vision. Dates are tentative months until real dates are set."""

PROGRAM_PAGES = {
    "the-art-of-us": {
        "code": "D4", "num": "PROGRAM 1 · RELATIONSHIPS", "title": "The Art of Us",
        "tagline": "Creating a More Honest, Intimate, and Fulfilling Marriage or Committed Relationship",
        "intro": "The Art of Us is a fun, challenging, and interactive three-day seminar for couples who want to deepen intimacy, practice honesty, and learn to communicate more accurately and constructively.",
        "points": [("Honesty and understanding", "Explore ways to express yourself clearly and hear your partner more fully."),
                   ("Communication that builds up", "Practice tools intended to strengthen connection, mutual respect, and closeness.")],
        "fit": "The Art of Us is a standalone seminar with no prerequisites.",
        "schedule": "Planned as Dallas Discovery's opening seminar in March.",
    },
    "the-beginning": {
        "code": "D1", "num": "PROGRAM 2 · THE PAST", "title": "The Beginning",
        "tagline": "Understand Your Past. Discover New Possibilities.",
        "intro": "Every story has a beginning. Long before we became the people we are today, we began forming beliefs about ourselves, others, and the world around us. Family experiences, relationships, and the messages we received while growing up can continue to shape how we think, feel, and respond to life. The Beginning is an immersive, three-day experiential seminar that invites you to explore those early influences with curiosity and compassion. The goal is not to remain in the past, but to better understand it, and discover possibilities for how you live today.",
        "points": [("Your early story", "Look at the people, experiences, and environments that helped shape your understanding of yourself."),
                   ("Beliefs and patterns", "Notice assumptions and familiar responses that may influence your choices and relationships."),
                   ("Awareness and perspective", "Consider new ways of understanding your experiences and the meaning you've given them."),
                   ("Strength and possibility", "Recognize inner resources and explore more intentional ways of moving forward.")],
        "fit": "The Beginning is the first step in the three-part personal-growth sequence. It has no prior Dallas Discovery seminar requirement. Completing it prepares you to participate in Awakening, followed by Vision.",
        "schedule": "Planned for April, with additional sessions alongside Awakening in May and alongside Awakening and Vision in June.",
    },
    "awakening": {
        "code": "D2", "num": "PROGRAM 3 · THE PRESENT", "title": "Awakening",
        "tagline": "Become More Aware of Your Present.",
        "intro": "Awakening is the second three-day seminar in the Dallas Discovery personal-growth sequence. It builds on the insights of The Beginning and invites you to bring greater awareness to your thoughts, emotions, relationships, and choices, and to discover new ways of responding to life today.",
        "points": [],
        "fit": "Prerequisite: completion of The Beginning.",
        "schedule": "Planned to run alongside The Beginning in May, with both seminars offered at the same facility for different groups of participants.",
    },
    "vision": {
        "code": "D3", "num": "PROGRAM 4 · THE FUTURE", "title": "Vision",
        "tagline": "Envision Your Future. Live With Greater Intention.",
        "intro": "Vision is the third three-day seminar in the Dallas Discovery personal-growth sequence. It invites you to clarify what matters most, imagine new possibilities, and begin shaping a future that reflects your values and intentions.",
        "points": [],
        "fit": "Prerequisites: completion of The Beginning and Awakening.",
        "schedule": "Planned to be offered in June, alongside the other two personal-growth seminars for participants at different stages.",
    },
}
ORDER = ["the-art-of-us", "the-beginning", "awakening", "vision"]
