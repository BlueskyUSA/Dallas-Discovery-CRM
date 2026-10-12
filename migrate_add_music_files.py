"""One-time, safe to re-run. Creates program_music_files, where MP3s uploaded on a program's Music
page are stored (in the database, so they survive Render deploys).

Run in the Render Shell:

    python3 migrate_add_music_files.py
"""
from db import get_db


def main():
    conn = get_db()
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'program_music_files'"
    ).fetchone()
    if not exists:
        conn.execute(
            """CREATE TABLE program_music_files (
                   id SERIAL PRIMARY KEY,
                   program_id INTEGER NOT NULL REFERENCES programs(id),
                   day_label TEXT,
                   title TEXT NOT NULL,
                   original_filename TEXT NOT NULL,
                   data BYTEA,
                   content_type TEXT,
                   uploaded_at TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
               )"""
        )
        conn.commit()
        print("Created program_music_files table.")
    else:
        print("program_music_files table already exists.")
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
