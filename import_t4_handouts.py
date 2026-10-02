"""
One-time helper: bulk-imports the Friday/Saturday/Sunday handout files into
the training materials library for the T4 program (Bluesky Relationship
Training / "Squeeze").

Before running:
  1. Create a folder named "import_materials" right next to this script
     (i.e. inside your bluesky-crm project folder).
  2. Put all 13 handout files Claude sent you into that folder, with their
     names unchanged.

Then run:

    python3 import_t4_handouts.py

This copies each file into static/uploads/program_materials (the same place
the CRM's own upload form saves to) and creates a program_materials row for
it, tagged with the day it's used and a clean title. Safe to run more than
once -- it skips any file it already imported (matched by title).
"""
import os
import secrets
import shutil
from db import get_db

IMPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_materials")
MATERIAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_uploads", "program_materials")

# (local filename in import_materials/, day label, title to show in the CRM)
MATERIALS = [
    ("The Thought Cycle.docx", "Friday Night", "The Thought Cycle"),
    ("TA Questions - Name the Animal.docx", "Friday Night", "TA Questions: Name the Animal"),
    ("The 5 Losing Strategies.docx", "Friday Night", "The 5 Losing Strategies"),
    ("TA Questions - The Five Losing Strategies.docx", "Friday Night", "TA Questions: The Five Losing Strategies"),
    ("Homework - Relationship Assessment.xlsx", "Friday Night", "Homework: Relationship Assessment"),

    ("Imago Exercise - Sender-Receiver.docx", "Saturday", "Imago Exercise (Sender/Receiver)"),
    ("Needs and Love Language.docx", "Saturday", "Needs and Love Language"),
    ("Making Memories of Us - Stretch Song.docx", "Saturday", "Making Memories of Us (Stretch Song)"),

    ("Relationship Resources.docx", "Sunday", "Relationship Resources"),
    ("Rules of Engagement Exercise.docx", "Sunday", "Rules of Engagement Exercise"),
    ("Relationship Traps.docx", "Sunday", "Relationship Traps"),
    ("36 Questions to Fall in Love.docx", "Sunday", "36 Questions Designed to Help You Fall in Love"),
    ("Soul Words.pdf", "Sunday", "Soul Words"),
]


def import_materials():
    conn = get_db()
    program = conn.execute("SELECT * FROM programs WHERE code = 'SQZ'").fetchone()
    if not program:
        print("Couldn't find the Squeeze program -- nothing imported.")
        conn.close()
        return
    program_id = program["id"]

    os.makedirs(MATERIAL_DIR, exist_ok=True)

    imported, skipped, missing = 0, 0, []
    for local_name, day_label, title in MATERIALS:
        existing = conn.execute(
            "SELECT id FROM program_materials WHERE program_id = ? AND title = ?",
            (program_id, title),
        ).fetchone()
        if existing:
            skipped += 1
            continue

        source_path = os.path.join(IMPORT_DIR, local_name)
        if not os.path.exists(source_path):
            missing.append(local_name)
            continue

        ext = local_name.rsplit(".", 1)[1].lower()
        new_filename = f"program_{program_id}_{secrets.token_hex(6)}.{ext}"
        shutil.copyfile(source_path, os.path.join(MATERIAL_DIR, new_filename))

        conn.execute(
            "INSERT INTO program_materials (program_id, day_label, title, filename, original_filename) VALUES (?, ?, ?, ?, ?)",
            (program_id, day_label, title, new_filename, local_name),
        )
        conn.commit()
        imported += 1

    conn.close()
    print(f"Imported {imported}, skipped {skipped} already-imported, {len(missing)} file(s) not found.")
    if missing:
        print("Missing files (make sure these are in import_materials/ with these exact names):")
        for m in missing:
            print(" -", m)
    print("View them in the CRM under Programs -> Squeeze -> Training materials.")


if __name__ == "__main__":
    import_materials()
