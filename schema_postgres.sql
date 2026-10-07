-- Bluesky Life Training Seminars CRM schema — PostgreSQL version
-- Ported from schema.sql (SQLite). Column names, types-as-seen-by-the-app,
-- and default date/time FORMAT are kept identical to the SQLite version
-- (text columns holding 'YYYY-MM-DD HH:MI:SS' or 'YYYY-MM-DD' strings) so
-- app.py's existing string handling (e.g. slicing the first 10 chars of a
-- timestamp) keeps working unchanged.

CREATE TABLE IF NOT EXISTS contacts (
    id SERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT,
    photo_filename TEXT,
    profile_token TEXT UNIQUE,  -- unguessable link letting them fill in/update their own profile, no login
    email TEXT,
    cell_phone TEXT,
    home_phone TEXT,
    work_phone TEXT,
    partner_name TEXT,
    partner_cell TEXT,
    partner_email TEXT,
    emergency_contact_name TEXT,
    emergency_contact_cell TEXT,
    emergency_contact_home TEXT,
    sponsor_name TEXT,
    sponsor_phone TEXT,
    sponsor_email TEXT,
    sponsor_street_address TEXT,
    sponsor_street_address_2 TEXT,  -- apt/suite/unit/floor, optional
    sponsor_city TEXT,
    sponsor_state TEXT,          -- 2-letter abbreviation, e.g. TX
    sponsor_zip TEXT,
    street_address TEXT,
    street_address_2 TEXT,          -- apt/suite/unit/floor, optional
    city TEXT,
    state TEXT,                  -- 2-letter abbreviation, e.g. TX
    zip TEXT,
    gender TEXT,
    gender_self_description TEXT,
    age INTEGER,
    dob TEXT,
    ethnicity TEXT,
    ethnicity_self_description TEXT,
    marital_status TEXT,
    living_with_partner TEXT,       -- Yes / No
    children_ages TEXT,
    employment_status TEXT,
    occupation TEXT,
    occupation_self_description TEXT,
    employer TEXT,
    spiritual_orientation TEXT,
    spiritual_orientation_self_description TEXT,
    overall_health TEXT,
    health_limitations TEXT,
    education TEXT,
    bluesky_attendance TEXT,      -- Yes / No, if applicable
    discovery_attendance TEXT,   -- Yes / No, if applicable
    d1_month_year TEXT,
    d2_month_year TEXT,
    d3_month_year TEXT,
    refocus_month_year TEXT,                       -- Refocus (Discovery)
    relationship_training_month_year TEXT,         -- Relationship Training (Discovery)
    t1_month_year TEXT,
    t2_month_year TEXT,
    t3_month_year TEXT,
    bluesky_refocus_month_year TEXT,               -- Refocus (Bluesky)
    bluesky_relationship_training_month_year TEXT, -- Relationship Training (Bluesky)
    other_classes TEXT,
    notes TEXT,
    marketing_source TEXT,
    marketing_source_self_description TEXT,
    status TEXT NOT NULL DEFAULT 'Interested Party',   -- Interested Party, Registered, Attended, Graduate, TA, Facilitator, Donor
    pseudonym TEXT,
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS programs (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,           -- T1, T2, T3, T4
    name TEXT NOT NULL,
    description TEXT,
    prerequisite_program_id INTEGER REFERENCES programs(id)
);

CREATE TABLE IF NOT EXISTS cohorts (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES programs(id),
    session_number INTEGER NOT NULL,      -- permanent, auto-increment per program
    session_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Scheduled',  -- Scheduled, Completed, Cancelled
    UNIQUE(program_id, session_number)
);

CREATE TABLE IF NOT EXISTS small_groups (
    id SERIAL PRIMARY KEY,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    label TEXT NOT NULL                    -- e.g. "Group 3"
);

-- Whole-session staffing: who's covering the training as a whole, not tied
-- to one small group. role is the person's level -- Director, Lead
-- Facilitator, Support Facilitator, TA, Trainee. duty is optional and only
-- used for the handful of whole-session jobs that aren't tied to a small
-- group: 'Sound Equipment' (role TA) and 'Large Group Leader' (role
-- Trainee). Most rows (Director/Lead Facilitator/Support Facilitator) leave
-- duty blank.
CREATE TABLE IF NOT EXISTS cohort_staffing (
    id SERIAL PRIMARY KEY,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    role TEXT NOT NULL,                    -- Director, Lead Facilitator, Support Facilitator, TA, Trainee
    duty TEXT                              -- Sound Equipment, Large Group Leader, or blank
);

-- Per-small-group staffing: being in this table already means the person is
-- that group's Small Group Leader/TA, so role stays 'TA'. duty holds the
-- one extra job they also cover for that group, if any -- Team Captain,
-- Doors, Runners, Time Keeper, Microphones, Housekeeper, Lights -- or blank
-- for a TA with no extra duty.
CREATE TABLE IF NOT EXISTS small_group_staffing (
    id SERIAL PRIMARY KEY,
    small_group_id INTEGER NOT NULL REFERENCES small_groups(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    role TEXT NOT NULL DEFAULT 'TA',
    duty TEXT                              -- Team Captain, Doors, Runners, Time Keeper, Microphones, Housekeeper, Lights, or blank
);

CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    small_group_id INTEGER REFERENCES small_groups(id),
    questionnaire_completed_at TEXT,
    payment_completed_at TEXT,
    attended INTEGER NOT NULL DEFAULT 0,   -- 0/1
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
    feedback_token TEXT UNIQUE,
    enrolled_by_contact_id INTEGER REFERENCES contacts(id),  -- who brought this person in (TA / Team Captain requirement)
    UNIQUE(contact_id, cohort_id)
);

-- One row per submitted Bluesky Feedback Form (see the "Bluesky Feedback Form"
-- doc for the exact question wording). Linked to the enrollment so it auto-ties
-- to that trainee's Cohort/Program, Small Group -> TA(s), and Cohort Staffing ->
-- Facilitator without asking the trainee to identify anyone by name. Visible
-- only through the leadership-only Feedback view in the CRM -- never surfaced
-- to general staff, TAs, or Facilitators.
CREATE TABLE IF NOT EXISTS feedback_responses (
    id SERIAL PRIMARY KEY,
    enrollment_id INTEGER NOT NULL UNIQUE REFERENCES enrollments(id),
    facilitator_clear INTEGER,              -- 1-5
    facilitator_safe INTEGER,               -- 1-5
    facilitator_responsive INTEGER,         -- 1-5
    facilitator_did_well TEXT,
    facilitator_could_improve TEXT,
    ta_safe INTEGER,                        -- 1-5
    ta_focused INTEGER,                     -- 1-5
    ta_individual_attention INTEGER,        -- 1-5
    ta_did_well TEXT,
    ta_could_improve TEXT,
    practical_tools INTEGER,                -- 1-5
    emotionally_safe INTEGER,               -- 1-5
    would_recommend INTEGER,                -- 1-5
    future_training_likelihood INTEGER,     -- 1-5
    uncomfortable_notes TEXT,
    other_comments TEXT,
    submitted_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS contracts (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    kind TEXT NOT NULL DEFAULT 'D1',  -- which training this belongs to: D1 (Contract), D2 (Your Poem), D6 (Spiritual Contract)
    current_text TEXT NOT NULL,
    originating_cohort_id INTEGER REFERENCES cohorts(id),
    led_by_contact_id INTEGER REFERENCES contacts(id),
    assisted_by_contact_id INTEGER REFERENCES contacts(id),
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
    UNIQUE(contact_id, kind)
);

CREATE TABLE IF NOT EXISTS contract_revisions (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id),
    text TEXT NOT NULL,
    context TEXT,                          -- e.g. "Added during spiritual training"
    revised_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

-- The "Relationship Profile" registration questionnaire, used by all
-- programs (T1-T4). One per person per enrollment (in T4, each partner
-- fills out their own).
CREATE TABLE IF NOT EXISTS training_profiles (
    id SERIAL PRIMARY KEY,
    enrollment_id INTEGER NOT NULL UNIQUE REFERENCES enrollments(id),
    first_time_attending TEXT,        -- Yes / No
    training_goals TEXT,              -- What do you want to get out of this training?
    relationship_length TEXT,         -- How long have you been in your relationship?
    how_met TEXT,                     -- How did you meet your partner and what attracted you to them?
    childhood_patterns TEXT,          -- Similar patterns to your childhood/parents?
    emotional_milestone TEXT,         -- A significant emotional milestone in the relationship
    relationship_strengths TEXT,
    relationship_weaknesses TEXT,
    stress_points TEXT,
    song_artist TEXT,                 -- A song & artist that describes how you feel about your partner
    song_feelings TEXT,               -- How that song makes you feel toward your partner
    other_info TEXT,                  -- Anything else important to this relationship
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
    updated_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS donations (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    amount REAL NOT NULL,
    fund TEXT NOT NULL DEFAULT 'General Operating',
    donation_date TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD'),
    payment_method TEXT,
    receipt_sent INTEGER NOT NULL DEFAULT 0
);

-- New: login links for the public client portal (T4 questionnaire
-- self-service). A row is created when staff sends a client their
-- questionnaire link; the token is emailed, and is single-use / expiring.
CREATE TABLE IF NOT EXISTS portal_links (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id),
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
    expires_at TEXT NOT NULL,
    used_at TEXT
);

-- Training materials library: handouts, resource sheets, etc. given to
-- trainees during a program's weekend, organized by which day they're used.
CREATE TABLE IF NOT EXISTS program_materials (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES programs(id),
    day_label TEXT,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    data BYTEA,
    content_type TEXT,
    uploaded_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

-- Contact photo bytes, stored in the database (not on local disk) so they
-- survive Render deploys, which wipe the web server's filesystem. One row
-- per contact who has uploaded a photo.
CREATE TABLE IF NOT EXISTS contact_photos (
    contact_id INTEGER PRIMARY KEY REFERENCES contacts(id),
    data BYTEA NOT NULL,
    content_type TEXT NOT NULL
);

-- Music lists per program (e.g. Squeeze's Saturday dinner entry/background,
-- stretch songs, dance songs). Editable directly in the CRM rather than
-- maintained in an external music app.
CREATE TABLE IF NOT EXISTS program_playlists (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES programs(id),
    day_label TEXT,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS playlist_songs (
    id SERIAL PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES program_playlists(id),
    position INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    duration TEXT,
    notes TEXT
);

-- Team logins for the CRM (/crm). role is 'Team' or 'Leadership' --
-- Leadership additionally sees feedback results, contracts, and donations.
-- (Defined here, before access_grants below, since access_grants has a
-- foreign key to staff -- Postgres requires the referenced table to exist
-- first when running this script fresh on a brand-new database.)
CREATE TABLE IF NOT EXISTS staff (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Team',
    active INTEGER NOT NULL DEFAULT 1,
    is_owner INTEGER NOT NULL DEFAULT 0,
    contact_id INTEGER REFERENCES contacts(id),  -- optional link to their full Contact profile
    created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);

-- Authorized Users: which Staff accounts (beyond Leadership, who always
-- have access) may view a confidential area. area_key is either a fixed
-- key ("accounting", "marketing", "contacts") or a program's code
-- ("B1", "B2", ...). Capped at 4 grants per area_key, enforced in the app.
CREATE TABLE IF NOT EXISTS access_grants (
    id SERIAL PRIMARY KEY,
    area_key TEXT NOT NULL,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    granted_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
);
