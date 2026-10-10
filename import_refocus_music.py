"""
One-time import: loads the Refocus training's music into Music & Playlists for the Refocus program:

  1. "Program music in cue order" (Saturday) -- the song cues from the Austin Refocus schedule, in order,
     each with the time, cue number, what is happening in the room, and the lights.
  2. "Song bank (candidate songs)" -- the ~100 songs in the Refocus songs list, with the
     entry/exit notes that were beside some of them.

Run in the Render Shell:

    python3 import_refocus_music.py

Safe to run more than once: a playlist that already has songs is left alone, so nothing is duplicated.
The Refocus syllabus and supply list are documents -- upload them on Programs > Refocus > Training materials.
"""
from db import get_db

# (title, artist, album, notes)
CUES=[('Beautiful Day', 'U2', None, '9:00 AM · cue #1 · Greeting Trainees As they Enter Room · lights full'),
 ('I Believe I Can Fly', 'R. Kelly', None, '9:00 AM · cue #2 · All Eyes Closed - Listen to Words of This Song · lights low'),
 ('Brave', None, None, '9:30 AM · cue #3 · Beginning The Day · lights med'),
 ('Simplicity', None, None, '9:55 AM · cue #4 · Move Chairs to Small Groups · lights med'),
 ('Halcyon Days', None, None, "9:55 AM · cue #5 · TA's Bring Self Defeating Game Cards With them · background · lights med"),
 ('Simplicity', None, None, '10:20 AM · cue #6 · Break - Pick a Buddy · lights full'),
 ('Amapola', None, None, '10:35 AM · cue #7 · FeedBack Game · background · lights med'),
 ('Simplicity', None, None, '11:00 AM · cue #8 · Break · lights full'),
 ('I Wanna Know What Love Is', None, None, '11:15 AM · cue #9 · Psuedo Names - "A" · lights low'),
 ('Real', None, None, '11:15 AM · cue #10 · Listen to Words of This Song · lights low'),
 ('Mary Queen of Scotts', None, None, '11:15 AM · cue #11 · A Tap B · background'),
 ('Mary Queen of Scotts', None, None, '11:35 AM · Psuedo Names - "B" · background · lights low'),
 ('The Last Day', None, None, '12:25 PM · cue #12 · Lunch Break Setup · lights low'),
 ('The Last Day', None, None, '12:30-2pm · Lunch Break · lights low'),
 ('Got To Be Real', None, None, '2:00 PM · cue #13 · Large Group Sharing · lights full'),
 ('Do The Walls Come Down', None, None, '2:20 PM · cue #14 · Close Eyes and Listen To Words of This Song · lights low'),
 ('Halcyon Days', None, None, '2:40 PM · cue #15 · Rocks, Stones, Wall Exercise · background · lights low'),
 ('Stand', None, None, '3:05 PM · cue #16 · Break · lights low'),
 ('The View From Here', None, None, '3:20 PM · cue #17 · Contraps - Small Group Exercise · lights low'),
 ('The Real Me', None, None, '3:20 PM · cue #18 · Listen to Words of This Song · lights low'),
 ('Halcyon Days', None, None, "3:20 PM · cue #19 · TA's Begin · background · lights low"),
 ('Child of God', None, None, '4:40 PM · cue #20 · If Commitments to God - Play this song · lights low'),
 ('I will Change Your Name', None, None, '4:40 PM · cue #21 · Else - Play this Song · lights low'),
 ('Got To Be Real', None, None, '4:45 PM · cue #22 · Break · lights low'),
 ('Little Help From My Friends', None, None, '5:00 PM · cue #23 · Setup - Missing Puzzle Piece Exercise · lights full'),
 ('Assorted fun background songs (#24-29)', None, None, '5:00 PM · cue #24-29 · Missing Puzzle Piece Exercise · lights full'),
 ('The Climb', None, None, '5:20 PM · cue #30 · Listen to Words of This Song · lights med'),
 ('Stand', None, None, '5:20 PM · cue #31 · Goals Exercise · background · lights med'),
 ('Live Every Moment', None, None, '5:50 PM · cue #32 · Break · lights full'),
 ('Assorted fun background songs (#33-39)', None, None, '6:00 PM · cue #33-39 · Paint Rocks Exercise · lights full'),
 ('Better Get to Livin', None, None, '6:20 PM · cue #40 · Gift Cups Exercise Setup - Get in Small Groups · lights full'),
 ("Better Get to Livin' (repeat), then 5 songs: Better Get to Livin', Change, Don't Stop, Got to Be Real, The Climb",
  None,
  None,
  "6:20 PM · cue #40-44 · Play 5 songs - Better Get To Livin, Change, Don't Stop, Got To Be Real, The Climb · lights full"),
 ('Halcyon Days',
  None,
  None,
  '6:35 PM · cue #45 · Each Person Reads Gifts Given - Name has the gift of…….Name Recieves these Gifts · background · lights med'),
 ('The View From Here', None, None, '6:40 PM · cue #46 · Journal · background'),
 ('The Rose', None, None, '6:45 PM · cue #47 · Letter To Discovery · background · lights med'),
 ('Friends', None, None, '6:55 PM · cue #48 · Closing Ceremony · lights full · also the exit music')]
BANK=[('My Girl', 'The Temptations', None, None),
 ('I’ll Be There', 'The Jackson 5', None, None),
 ('Wonderful Tonight', 'Eric Clapton', None, None),
 ('I Just Called to Say I Love You', 'Stevie Wonder', None, None),
 ('You Are So Beautiful', 'Joe Cocker', None, None),
 ('In My Life', 'The Beatles', None, None),
 ('The First Time Ever I Saw Your Face', 'Roberta Flack', None, None),
 ('Are You Lonesome Tonight?', 'Elvis Presley', None, None),
 ('Michelle', 'The Beatles', None, None),
 ('I Can’t Stop Loving You', 'Ray Charles', None, None),
 ('Best of My Love', 'The Emotions', None, None),
 ('Bridge Over Troubled Water', 'Simon & Garfunkel', None, None),
 ('All I Have to Do Is Dream', 'The Everly Brothers', None, None),
 ('You’ve Got a Friend', 'Carole King', None, None),
 ('Killing Me Softly With His Song', 'Roberta Flack', None, None),
 ('To Sir With Love', 'Lulu', None, None),
 ('Sherry', 'The Four Seasons', None, None),
 ('Crazy Little Thing Called Love', 'Queen', None, None),
 ('Unchained Melody', 'The Righteous Brothers', None, None),
 ('Save the Last Dance for Me', 'The Drifters', None, None),
 ('Stay', 'Maurice Williams and the Zodiacs', None, None),
 ('Will You Love Me Tomorrow?', 'Carole King', None, None),
 ('Up on the Roof', 'The Drifters', None, None),
 ('He’s So Fine', 'The Chiffons', None, None),
 ('I Will Follow Him', 'Little Peggy March', None, None),
 ('Chapel of Love', 'The Dixie Cups', None, None),
 ('My Guy', 'Mary Wells', None, None),
 ('Happy Together', 'The Turtles', None, None),
 ('Back in My Arms Again', 'The Supremes', None, None),
 ('I Got You Babe', 'Sonny and Cher', None, None),
 ('My Love', 'Petula Clark', None, None),
 ('(You’re My) Soul and Inspiration', 'The Righteous Brothers', None, None),
 ('I Will Always Love You', 'Dolly Parton/Whitney Houston', None, None),
 ('When a Man Loves a Woman', 'Percy Sledge', None, None),
 ('Reach Out I’ll Be There', 'The Four Tops', None, None),
 ('Strangers in the Night', 'Frank Sinatra', None, None),
 ('Baby Love', 'The Supremes', None, None),
 ('This Guy’s in Love With You', 'Herb Alpert', None, None),
 ('Love Theme From Romeo and Juliet', 'Henry Mancini', None, None),
 ('Higher Love', 'Steve Winwood', None, None),
 ('Sugar Sugar', 'The Archies', None, None),
 ('The Long and Winding Road', 'The Beatles', None, None),
 ('Close to You', 'The Carpenters', None, None),
 ('Ain’t No Mountain High Enough', 'Diana Ross', None, None),
 ('Let’s Stay Together', 'Al Green', None, None),
 ('Heart of Gold', 'Neil Young', None, None),
 ('Here, There and Everywhere', 'The Beatles', None, None),
 ('Lean on Me', 'Bill Withers', None, None),
 ('You Are the Sunshine of My Life', 'Stevie Wonder', None, 'Exit Music'),
 ('Let’s Get It On', 'Marvin Gaye', None, None),
 ('Midnight Train To Georgia', 'Gladys Knight', None, None),
 ('The Way We Were', 'Barbra Streisand', None, None),
 ('Love’s Theme', 'The Love Unlimited Orchestra', None, None),
 ('Feel Like Makin’ Love', 'Roberta Flack', None, 'Entry Music?'),
 ('Can’t Get Enough of Your Love Babe', 'Barry White', None, None),
 ('Then Came You', 'Dionne Warwick and the Spinners', None, None),
 ('Lovin’ You', 'Minnie Ripperton', None, None),
 ('Shining Star', 'Earth Wind & Fire', None, None),
 ('You Don’t Have to Be a Star (To Be in My Show)', 'Marilyn McCoo and Billy Davis Jr.', None, None),
 ('How Deep Is Your Love', 'The Bee Gees', None, 'Exit Music'),
 ('You’re the One That I Want', 'John Travolta and Olivia Newton-John', None, None),
 ('Love You Inside Out', 'Bee Gees', None, None),
 ('Come On Eileen', 'Dexys Midnight Runners', None, None),
 ('Time After Time', 'Cyndi Lauper', None, None),
 ('Crazy for You', 'Madonna', None, None),
 ('Take on Me', 'a-ha', None, None),
 ('Moon River', 'Henry Mancini', None, None),
 ('With or Without You', 'U2', None, None),
 ('Vision of Love', 'Mariah Carey', None, None),
 ('Kiss From a Rose', 'Seal', None, None),
 ('Girl', 'The Beatles', None, None),
 ('Chelsea Morning', 'Joni Mitchell', None, None),
 ('P.S. I Love You', 'The Beatles', None, 'entry exit songs'),
 ('Just Like a Woman', 'Bob Dylan', None, 'entry exit songs'),
 ('Do You Believe in Magic?', 'The Lovin’ Spoonful', None, None),
 ('Still in Love With You', 'Al Green', None, None),
 ('Somewhere', 'Stephen Sondheim and Leonard Bernstein', None, None),
 ('On the Street Where You Live', None, 'From My Fair Lady', None),
 ('Saving All My Love for You', 'Whitney Houston', None, None),
 ('Always on My Mind', 'Willie Nelson', None, None),
 ('Crazy', 'Patsy Cline', None, None),
 ('Sexual Healing', 'Marvin Gaye', None, None),
 ('Do You Want to Dance?', 'Bette Midler', None, None),
 ('Fever', 'Peggy Lee', None, None),
 ('Last Dance', 'Donna Summer', None, None),
 ('Just the Way You Are', 'Billy Joel', None, None),
 ('Songbird', 'Fleetwood Mac', None, None),
 ('You Make Loving Fun', 'Fleetwood Mac', None, 'exit music'),
 ('At Last', 'Etta James', None, None),
 ('All I Want', 'Joni Mitchell', None, None),
 ('Natural Woman', 'Aretha Franklin', None, None),
 ('A Man and a Woman', 'Anita Kerr Singers', None, None),
 ('Someone to Watch Over Me', 'Linda Ronstadt', None, None),
 ('Some Enchanted Evening', None, 'From South Pacific', None),
 ('Do You Love Me?', None, 'From Fiddler on the Roof', 'entry music'),
 ('Alison', 'Elvis Costello', None, None),
 ('How Can I Tell You', 'Cat Stevens', None, None),
 ('Have I Told You Lately', 'Van Morrison', None, None),
 ('When I Fall In Love', 'Nat King Cole', None, None),
 ('Oh Girl', 'The Chi-Lites', None, None),
 ('My Heart Belongs To You', 'Peabo Bryson', None, None)]


PLAYLISTS = [
    ("Saturday", "Program music in cue order", CUES),
    ("General", "Song bank (candidate songs)", BANK),
]


def main():
    conn = get_db()
    prog = conn.execute(
        "SELECT id, code, name FROM programs WHERE LOWER(name) LIKE '%refocus%' OR code = 'D5' ORDER BY id LIMIT 1"
    ).fetchone()
    if not prog:
        print("Could not find the Refocus program. Nothing was changed.")
        conn.close()
        return
    print(f"Loading into program: {prog['code']} - {prog['name']}")
    for day, category, songs in PLAYLISTS:
        pl = conn.execute(
            "SELECT id FROM program_playlists WHERE program_id = ? AND category = ? AND COALESCE(day_label, '') = ?",
            (prog["id"], category, day),
        ).fetchone()
        if pl:
            have = conn.execute("SELECT COUNT(*) AS n FROM playlist_songs WHERE playlist_id = ?", (pl["id"],)).fetchone()["n"]
            if have:
                print(f"  '{category}' already has {have} songs -- left alone.")
                continue
            playlist_id = pl["id"]
        else:
            conn.execute(
                "INSERT INTO program_playlists (program_id, day_label, category) VALUES (?, ?, ?)",
                (prog["id"], day, category),
            )
            playlist_id = conn.execute(
                "SELECT id FROM program_playlists WHERE program_id = ? AND category = ? ORDER BY id DESC LIMIT 1",
                (prog["id"], category),
            ).fetchone()["id"]
        for pos, (title, artist, album, notes) in enumerate(songs, start=1):
            conn.execute(
                "INSERT INTO playlist_songs (playlist_id, position, title, artist, album, notes) VALUES (?,?,?,?,?,?)",
                (playlist_id, pos, title, artist, album, notes),
            )
        print(f"  '{category}': added {len(songs)} songs.")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
