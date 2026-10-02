"""
One-time import: loads the 2020/2021 Squeeze (relationship training) song
lists into the CRM's Music & Playlists feature, and imports the Saturday
Stretch Song Assignment worksheet into the training materials library.

Before running:
  1. Create a folder named "import_materials" right next to this script
     (the same one used for import_t4_handouts.py, if it's still there).
  2. Put "Stretch Song Assignment Sheet (For TAs).docx" into that folder.

Then run (with the app stopped):

    python3 import_squeeze_music.py

Safe to run more than once -- playlists are matched by day + category,
songs are matched by title within their playlist, and the worksheet is
matched by title, so nothing gets duplicated on a re-run.
"""
import os
import secrets
import shutil
from db import get_db

IMPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_materials")
MATERIAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_uploads", "program_materials")

# (day_label, category, [(title, artist, album, duration), ...])
PLAYLISTS = [
    ("Friday Night", "Background", [
        ("How Sweet It Is (To Be Loved by You)", "James Taylor", "James Taylor: Greatest Hits, Vol. 1", "3:35"),
        ("All of Me", "John Legend", "Love in the Future (Deluxe Edition)", "4:30"),
        ("Remember Me This Way", "Pocket Songs Karaoke", "Pocket Songs Karaoke", "4:23"),
        ("You Are So Beautiful", "Joe Cocker", "Joe Cocker", "2:42"),
        ("If You Leave Me Now (New Version)", "Peter Cetera", "You're the Inspiration", "4:22"),
        ("Night Fall", "Paul Hovda", "Inner Image", "3:17"),
        ("Unfolding", "Doug Hammer", "Solace", "4:07"),
        ("Piano Instrumental", "Easy Listening Piano", "Easy Listening Piano: Background", "4:31"),
        ("I Won't Give Up", "Jason Mraz", "I Won't Give Up - Single", "4:00"),
        ("Just the Way You Are", "Billy Joel", "Greatest Hits Volume 1", "4:49"),
    ]),
    ("Saturday", "Background", [
        ("Cantaloop (Flip Fantasia)", "Us3", "Hand On the Torch", "4:39"),
        ("That's What I'm Here For", "Jim Brickman", "Visions of Love", "4:08"),
        ("Have You Ever Been in Love", "Celine Dion", "A New Day Has Come", "4:09"),
        ("A Warm Place In Winter", "Doug Hammer", "Solace", "3:39"),
        ("Make You Feel My Love", "Adele", "19", "3:32"),
        ("Soliloquy", "Doug Hammer", "Solace", "5:40"),
        ("I Won't Let Go", "Rascal Flatts", "Nothing Like This", "3:48"),
        ("Surfin Safari", "Beach Boys", "20 Good Vibrations - The Greatest Hits", "2:08"),
        ("You Really Got Me", "The Kinks", "You Really Got Me / Tired of Waiting for You", "2:10"),
        ("You Make Loving Fun", "Fleetwood Mac", "Greatest Hits", "3:32"),
        ("Mary, Queen of Scots", "John Barry", "Moviola", "3:31"),
        ("Broken Together", "Casting Crowns", "Thrive", "4:45"),
        ("Something in the Way She Moves", "James Taylor", "James Taylor's Greatest Hits", "3:13"),
        ("My Heart Belongs to You", "Peabo Bryson", "Unconditional Love", "4:28"),
        ("Sorry Seems To Be The Hardest Word", "Ray Charles And Elton John", "Genius Loves Company", "3:59"),
        ("Thinking Thoughts", "Paul Hovda", "Inner Image", "3:57"),
        ("Have I Told You Lately", "Rod Stewart", "Vagabond Heart", "4:02"),
        ("I've Got You Under My Skin", "Bono/Frank Sinatra", "Duets", "3:33"),
        ("Safe In Your Embrace", "Kevin Kern", "Imagination's Light", "3:48"),
        ("You Are The Sunshine Of My Life", "Stevie Wonder", "A Greatest Hits Collection - Disc 1", "2:57"),
        ("Better Get to Livin'", "Dolly Parton", "Backwoods Barbie", "3:36"),
        ("If Tomorrow Never Comes", "If Tomorrow Never Comes", "If Tomorrow Never Comes - Single", "3:38"),
        ("Stillness", "Doug Hammer", "Travels", "4:32"),
        ("Those Sweet Words", "Norah Jones", "Feels Like Home", "3:22"),
        ("Cavatina - Theme from \"The Deer Hunter\"", "John Williams", "Changes (Remastered)", "3:31"),
        ("Gabrielle", "Doug Hammer", "Solace", "5:40"),
        ("When Roads Divide", "Paul Hovda", "Inner Image", "3:41"),
        ("The Dance", "Doug Hammer", "Solace", "5:16"),
        ("Remembering the Light", "Kevin Kern", "Imagination's Light", "4:26"),
        ("Feel Like Makin' Love", "Roberta Flack", "The Very Best of Roberta Flack", "2:55"),
    ]),
    ("Saturday", "Dinner Entry", [
        ("Why Don't We Just Dance", "Josh Turner", None, "3:12"),
        ("I've Got A Crush On You", "Steve Tyrell", "Somethings Gotta Give", "2:32"),
    ]),
    ("Saturday", "Dinner Background", [
        ("'Round Midnight", "Kenny Rankin", "A Song for You", "3:46"),
        ("Be Still My Beating Heart", "Sting", "Fields of Gold: The Best of Sting", "5:34"),
        ("Belief", "John Mayer", "Continuum", "4:02"),
        ("The Best Of My Love", "Eagles", "On The Border", "4:34"),
        ("Caught in a Trap", "Elvis Presley", "Essential Elvis", "4:28"),
        ("Don't Let Me Be Lonely Tonight", "James Taylor", "Greatest Hits", "2:39"),
        ("Dreaming With A Broken Heart", "John Mayer", "Continuum", "4:06"),
        ("Feeling Good", "Michael Bublé", "It's Time", "3:57"),
        ("Fields Of Gold", "Sting", "Fields of Gold: The Best of Sting", "3:38"),
        ("Have You Ever Been", "Celine Dion", "One Heart", "4:09"),
        ("Haven't We Met", "Kenny Rankin", "Silver Morning", "2:30"),
        ("The Heart Of Life", "John Mayer", "Continuum", "3:18"),
        ("I Don't Trust Myself (With Loving You)", "John Mayer", "Continuum", "4:52"),
        ("I Surrender", "Celine Dion", "A New Day Has Come", "4:47"),
        ("I Will Not Let You Go", "Ray Charles", None, "2:45"),
        ("I'm Alive", "Celine Dion", "A New Day Has Come", "3:30"),
        ("I've Got A Woman", "Ray Charles", "Ray!", "2:51"),
        ("I've Got the World on a String", "Frank Sinatra/Liza Minnelli", "Duets", "2:19"),
        ("I've Got You Under My Skin", "Michael Bublé", "It's Time", "3:40"),
        ("IF", "Bread", None, "2:36"),
        ("Isn't She Lovely", "Stevie Wonder", None, "6:35"),
        ("Kiss Lonely Good Bye", "Stevie Wonder", "A Greatest Hits Collection - Disc 2", "4:05"),
        ("Life Without You", "Stevie Ray Vaughan", "Greatest Hits", "4:18"),
        ("Little More Time With You", "James Taylor", "Hourglass", "3:51"),
        ("Lost Without Your Love", "Bread", None, "3:01"),
        ("Make It With You", "Bread", None, "3:12"),
        ("My Cherie Amour", "Stevie Wonder", "A Greatest Hits Collection - Disc 1", "2:53"),
        ("Nearness of You", "Norah Jones", "Miscellaneous Artists", "3:08"),
        ("Peaceful Easy Feeling", "Eagles", "1972-1999 Selected Works", "5:24"),
        ("Rainbow", "G. Love", "Thicker Than Water", "3:23"),
        ("Secret Garden", "Bruce Springsteen", "Bruce Springsteen - Greatest Hits", "4:27"),
        ("Secret O' Life", "James Taylor", "Greatest Hits Volume 2", "3:35"),
        ("She's Got A Way [live]", "Billy Joel", "Greatest Hits, Vol. II (1978-1985)", "3:01"),
        ("Slow Dancing In A Burning Room", "John Mayer", "Continuum", "4:02"),
        ("Something in the Way She Moves", "James Taylor", "Greatest Hits", "3:14"),
        ("Song For You", "Michael Bublé", "It's Time", "4:42"),
        ("Stuck Like Glue", "Sugarland", "Stuck Like Glue - Single", "4:06"),
        ("Summer Wind", "Frank Sinatra/Julio Iglesias", "Duets", "2:31"),
        ("Sunrise", "Norah Jones", "Feels Like Home", "3:17"),
        ("Tears in Heaven", "Eric Clapton with Sting & Billy Joel", None, "3:18"),
        ("Those Sweet Words", "Norah Jones", "Feels Like Home", "3:22"),
        ("Try A Little Tenderness", "Michael Bublé", "It's Time", "4:06"),
        ("Up On The Roof", "James Taylor", "Greatest Hits Volume 2", "4:20"),
        ("The Way You Look Tonight", "Frank Sinatra", "Fly Me to the Moon: Opus Collection", "3:22"),
        ("What Am I to You?", "Norah Jones", "Feels Like Home", "3:30"),
        ("When We Dance", "Sting", "Fields of Gold: The Best of Sting", "5:59"),
        ("You Are The Sunshine Of My Life", "Stevie Wonder", "A Greatest Hits Collection - Disc 1", "2:57"),
        ("You Make Loving Fun", "Fleetwood Mac", "Greatest Hits", "3:32"),
        ("You're My Best Friend (1991 Bonus)", "Queen", "A Night At The Opera", "2:52"),
        ("La Vie En Rose", "Louis Armstrong", "Somethings Gotta Give", "3:25"),
        ("I Only Have Eyes For You", "The Flamingos", "Somethings Gotta Give", "3:14"),
        ("So Nice - Summer Samba", "Astrud Gilberto", "Somethings Gotta Give", "2:37"),
        ("Remember Me", "Heitor Pereira", "Somethings Gotta Give", "1:51"),
        ("Samba De Mon Coeur Qui Bat", "Coralie Clement", "Somethings Gotta Give", "3:53"),
        ("Que Reste Til De Nos Amour", "Charles Trenet", "Somethings Gotta Give", "3:11"),
        ("Les Escrocs", "Les Escrocs", "Somethings Gotta Give", "4:22"),
        ("Je Cherche Un Homme", "Eartha Kitt", "Somethings Gotta Give", "2:49"),
        ("C'est Si Bon", "Eartha Kitt", "Somethings Gotta Give", "2:58"),
        ("Brazil", "Django Reinhardt", "Somethings Gotta Give", "2:50"),
        ("Sweet Lorraine", "Stephane Grapelli", "Somethings Gotta Give", "3:08"),
        ("Love Makes The World Go Round", "Deon Jackson", "Somethings Gotta Give", "2:28"),
        ("La Vie En Rose (2)", "Jack Nicholson", "Somethings Gotta Give", "2:57"),
        ("When Sunny Gets Blue", "Kenny Rankin", "The Kenny Rankin Album", "3:03"),
        ("When the Sun Comes Out", "Kenny Rankin", "A Song for You", "6:10"),
        ("Where Do You Start", "Kenny Rankin", "A Song for You", "3:25"),
        ("The Way You Look Tonight (2)", "Kenny Rankin", "A Song for You", "3:30"),
        ("Then I'll Be Tired of You", "Kenny Rankin", "A Song for You", "5:24"),
        ("Summer Wind (2)", "Frank Sinatra", "Nothing But the Best", "2:55"),
        ("Spanish Harlem", "Kenny Rankin", "A Song for You", "4:27"),
        ("A Song for You", "Kenny Rankin", "A Song for You", "5:57"),
        ("Here's That Rainy Day", "Kenny Rankin", "The Kenny Rankin Album", "2:40"),
        ("House of Gold", "Kenny Rankin", "The Kenny Rankin Album", "3:07"),
        ("Love Walked In", "Kenny Rankin", "A Song for You", "3:58"),
        ("On and On", "Kenny Rankin", "The Kenny Rankin Album", "3:33"),
    ]),
    ("Saturday", "Stretch Songs", [
        ("I Won't Give Up", "Jason Mraz", "I Won't Give Up - Single", "4:00"),
        ("God Gave Me You", "Dave Barnes", "What We Want, What We Get", "3:49"),
        ("Love Me Tender", "Elvis Presley", None, "2:42"),
        ("Wonderful Tonight", "Eric Clapton", None, "3:39"),
        ("At Last", "Etta James", None, "3:06"),
        ("Unchained Melody", "The Righteous Brothers", "Rock 'N' Roll Legends", "3:36"),
        ("Sorry Seems To Be The Hardest Word", "Ray Charles And Elton John", "Genius Loves Company", "3:59"),
        ("I Only Have Eyes For You", "The Flamingos", "Somethings Gotta Give", "3:14"),
        ("Let's Make Love", "Faith Hill/Tim McGraw", "Greatest Hits", "4:13"),
        ("Can You Feel the Love Tonight", "Elton John & London Community Gospel Choir", "The Lion King", "4:01"),
        ("I've Got A Crush On You", "Steve Tyrell", "Somethings Gotta Give", "2:32"),
        ("Hurting Each Other", "Carpenters", "Carpenters - Their Greatest Hits", "2:49"),
        ("Looking for Your Face", "Jared Harris", "A Gift of Love", "2:12"),
        ("Then", "Brad Paisley", "American Saturday Night (Bonus)", "5:22", "Theme: Good Relationships & Getting Better"),
        ("Remind Me", "Brad Paisley", "This Is Country Music", "4:32", "Theme: Commitment To Do It Better (duet w/ Carrie Underwood)"),
        ("I Will Be Here", "Steven Curtis Chapman", "All About Love", "4:13", "Theme: Need a Miracle"),
        ("Can't We Try", "Dan Hill", "D1 Stretch Cradle 15A", "4:06", "Theme: Renewed Commitment"),
    ]),
    ("Saturday", "All Dance Songs", [
        ("Why Don't We Just Dance", "Josh Turner", None, "3:12"),
        ("I've Got A Crush On You", "Steve Tyrell", "Somethings Gotta Give", "2:32"),
        ("'Round Midnight", "Kenny Rankin", "A Song for You", "3:46"),
        ("Love Shack", "The B-52's", "Cosmic Thing", "5:21"),
        ("Making Memories Of Us", "Keith Urban", "Be Here", "4:11"),
        ("Dynamite", "Taio Cruz", "Rokstarr", "3:24"),
        ("You Don't Know Me", "Michael Bublé", "It's Time", "4:14"),
        ("Remind Me", "American Country Hits", "Remind Me - Single (Tribute)", "4:10"),
        ("I Won't Give Up", "Jason Mraz", "I Won't Give Up - Single", "4:00"),
        ("Can't Get Enough of Your Love, Babe", "Barry White", "Gold", "3:50"),
        ("Just Dance", "Lady GaGa & Colby O'Donis", "The Fame", "4:02"),
        ("Can't We Try", "Discovery", "Disc 2", "3:57"),
        ("Pink Cadillac", "Bruce Springsteen", "18 Tracks", "3:33"),
        ("I Don't Want This Night to End", "Luke Bryan", "Tailgates & Tanlines", "3:40"),
        ("I Will Be Here", "Steven Curtis Chapman", "All About Love", "4:13"),
        ("Zoot Suit Riot", "Cherry Poppin' Daddies", "Zoot Suit Riot", "3:53"),
        ("Cotton Eye Joe", "Rednex", "Top 100 Dance Collection", "3:11"),
    ]),
    ("Sunday", "Background", [
        ("Happy (From \"Despicable Me 2\")", "Pharrell Williams", "G I R L", "3:53"),
        ("If Tomorrow Never Comes", "If Tomorrow Never Comes", "If Tomorrow Never Comes - Single", "3:38"),
        ("Graveyard Visit & I Forgive You", "Paul Mills", "October Baby: The Original Score", "6:13"),
        ("September Smile", "Paul Hovda", "Inner Image", "4:21"),
        ("The Way You Look Tonight", "Michael Bublé", "Michael Bublé", "4:38"),
        ("Caught in a Trap", "Elvis Presley", "Essential Elvis", "4:28"),
        ("Asleep the Snow Came Flying", "Tim Story", "A Winter's Solstice IV", "4:34"),
        ("You've Got a Friend", "James Taylor", "James Taylor's Greatest Hits", "4:28"),
        ("Halcyon Days", "John Tesh", "Avalon", "2:44"),
        ("Making Memories Of Us", "Keith Urban", "Be Here", "4:11"),
        ("Lucky", "Jason Mraz (feat. Colbie Caillat)", "We Sing. We Dance. We Steal Things.", "3:11"),
        ("Nearness of You", "Norah Jones", "Miscellaneous Artists", "3:08"),
        ("Homeward Bound", "Paul Hovda", "Inner Image", "3:59"),
        ("The Rose", "Bette Midler", "D1 Sunday", "3:34"),
        ("Irish Blessing", "Bill O'Connor", "D1 Sunday", "1:12"),
    ]),
]

ASSIGNMENT_SHEET = (
    "Stretch Song Assignment Sheet (For TAs).docx",
    "Saturday",
    "Stretch Song Assignment Sheet (for TAs)",
)


def import_playlists(conn):
    program = conn.execute("SELECT * FROM programs WHERE code = 'B4'").fetchone()
    if not program:
        print("Couldn't find the Squeeze program -- nothing imported.")
        return
    program_id = program["id"]

    playlists_created, songs_added, songs_skipped = 0, 0, 0
    for day_label, category, songs in PLAYLISTS:
        pl = conn.execute(
            "SELECT id FROM program_playlists WHERE program_id = ? AND day_label = ? AND category = ?",
            (program_id, day_label, category),
        ).fetchone()
        if pl:
            playlist_id = pl["id"]
        else:
            cur = conn.execute(
                "INSERT INTO program_playlists (program_id, day_label, category) VALUES (?, ?, ?)",
                (program_id, day_label, category),
            )
            playlist_id = cur.lastrowid
            playlists_created += 1

        for entry in songs:
            title, artist, album, duration = entry[0], entry[1], entry[2], entry[3]
            notes = entry[4] if len(entry) > 4 else None
            existing = conn.execute(
                "SELECT id FROM playlist_songs WHERE playlist_id = ? AND title = ?",
                (playlist_id, title),
            ).fetchone()
            if existing:
                songs_skipped += 1
                continue
            max_pos = conn.execute(
                "SELECT MAX(position) m FROM playlist_songs WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()["m"]
            conn.execute(
                """INSERT INTO playlist_songs (playlist_id, position, title, artist, album, duration, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (playlist_id, (max_pos or 0) + 1, title, artist, album, duration, notes),
            )
            songs_added += 1
        conn.commit()

    print(f"Playlists created: {playlists_created}. Songs added: {songs_added}, already-there: {songs_skipped}.")
    return program_id


def import_assignment_sheet(conn, program_id):
    local_name, day_label, title = ASSIGNMENT_SHEET
    existing = conn.execute(
        "SELECT id FROM program_materials WHERE program_id = ? AND title = ?",
        (program_id, title),
    ).fetchone()
    if existing:
        print("Assignment sheet already imported -- skipping.")
        return
    source_path = os.path.join(IMPORT_DIR, local_name)
    if not os.path.exists(source_path):
        print(f"Missing file: {local_name} (put it in import_materials/ to import it).")
        return
    os.makedirs(MATERIAL_DIR, exist_ok=True)
    ext = local_name.rsplit(".", 1)[1].lower()
    new_filename = f"program_{program_id}_{secrets.token_hex(6)}.{ext}"
    shutil.copyfile(source_path, os.path.join(MATERIAL_DIR, new_filename))
    conn.execute(
        "INSERT INTO program_materials (program_id, day_label, title, filename, original_filename) VALUES (?, ?, ?, ?, ?)",
        (program_id, day_label, title, new_filename, local_name),
    )
    conn.commit()
    print("Imported the Stretch Song Assignment Sheet into training materials.")


def run():
    conn = get_db()
    program_id = import_playlists(conn)
    if program_id:
        import_assignment_sheet(conn, program_id)
    conn.close()
    print("View the song lists under Programs -> Squeeze -> Music & playlists.")


if __name__ == "__main__":
    run()
