"""
One-time migration: adds music/playlist tracking to the CRM.

What it does:
  - Creates program_playlists -- one row per named song list for a program
    (e.g. Squeeze's Saturday "Dinner Entry" or "Stretch Songs"), tagged
    with which day it's used.
  - Creates playlist_songs -- one row per song in a playlist (title,
    artist, album, duration), ordered by `position`, editable right in
    the CRM.

Safe to run more than once.

Run this ONCE against your Postgres database, with the app stopped:

    python3 migrate_add_playlists.py
"""
from db import get_db


def migrate():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'program_playlists'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE program_playlists (
                id SERIAL PRIMARY KEY,
                program_id INTEGER NOT NULL REFERENCES programs(id),
                day_label TEXT,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            )"""
        )
        print("Created program_playlists table.")
    else:
        print("program_playlists table already exists.")

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'playlist_songs'"
    )
    if not cur.fetchall():
        cur.execute(
            """CREATE TABLE playlist_songs (
                id SERIAL PRIMARY KEY,
                playlist_id INTEGER NOT NULL REFERENCES program_playlists(id),
                position INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                artist TEXT,
                album TEXT,
                duration TEXT,
                notes TEXT
            )"""
        )
        print("Created playlist_songs table.")
    else:
        print("playlist_songs table already exists.")

    conn.commit()
    conn.close()
    print("Done. Manage playlists from each program's Music page in the CRM.")


if __name__ == "__main__":
    migrate()
