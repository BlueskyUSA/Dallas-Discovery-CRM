"""One-time, safe to re-run. Adds program_playlists.folder so playlists can be grouped into folders
on the Music page. Existing playlists stay where they are (no folder).

Run in the Render Shell:

    python3 migrate_add_playlist_folders.py
"""
from db import get_db


def main():
    conn = get_db()
    has_col = conn.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = 'program_playlists' AND column_name = 'folder'"""
    ).fetchone()
    if not has_col:
        conn.execute("ALTER TABLE program_playlists ADD COLUMN folder TEXT")
        conn.commit()
        print("Added program_playlists.folder.")
    else:
        print("program_playlists.folder already exists.")
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
